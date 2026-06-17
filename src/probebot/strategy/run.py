# src/probebot/strategy/run.py
"""
probebot Live Strategy Runner

Modes:
  --mode signal  : fetch OHLCV → compute 177 features → score signal → full_trade_cycle
  --mode check   : full_trade_cycle with no signal (position check only)

Uses full_trade_cycle pattern (learned from ltbbot/dnabot):
  - Per-symbol tracker file with SL/TP order IDs
  - ensure_tp_sl safety-net every cycle
  - housekeeper on idle
  - guardian_decorator for crash protection + Telegram alert
"""
import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from probebot.utils.exchange import Exchange
from probebot.utils.guardian import guardian_decorator
from probebot.utils.trade_manager import full_trade_cycle, read_tracker, is_candle_cooldown_active
from probebot.analysis.backtester import compute_signal_score


# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logger(symbol: str, timeframe: str) -> logging.Logger:
    safe    = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    log_dir = PROJECT_ROOT / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    logger  = logging.getLogger(f'probebot_{safe}')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fh = RotatingFileHandler(
            str(log_dir / f'probebot_{safe}.log'),
            maxBytes=5 * 1024 * 1024, backupCount=3,
        )
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(
            f'%(asctime)s [probebot {safe}] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S',
        ))
        logger.addHandler(ch)
        logger.propagate = False
    return logger


# ── Config + bot_spec loading ─────────────────────────────────────────────────

def _load_config(symbol: str, timeframe: str) -> dict | None:
    sym_safe = symbol.replace('/', '_').replace(':', '_')
    path     = (PROJECT_ROOT / 'src' / 'probebot' / 'strategy' / 'configs'
                / f'config_{sym_safe}_{timeframe}.json')
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _load_bot_spec(config: dict, symbol: str, timeframe: str) -> dict | None:
    p = Path(config.get('strategy', {}).get('bot_spec_path', ''))
    if not p.exists():
        sym_safe = symbol.replace('/', '_').replace(':', '_')
        p = PROJECT_ROOT / 'artifacts' / 'db' / f'bot_spec_{sym_safe}_{timeframe}.json'
    if not p.exists():
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


# ── Signal computation ────────────────────────────────────────────────────────

def _compute_signal(symbol: str, timeframe: str,
                    config: dict, entry_conditions: dict,
                    exchange: Exchange,
                    logger: logging.Logger) -> dict | None:
    """
    Fetch recent OHLCV → compute 177 features → score all tradeable
    move types → return best signal or None.
    """
    from probebot.features.engine import compute_all_features

    df = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=250)
    if df.empty or len(df) < 101:
        logger.warning(f"Zu wenig Kerzen ({len(df)}) — kein Signal.")
        return None

    # Drop still-open last candle — signal only on closed candles
    df_closed = df.iloc[:-1].reset_index(drop=True)

    logger.info(f"Features berechnen ({len(df_closed)} Kerzen)...")
    try:
        df_feat  = compute_all_features(df_closed, min_candles=100)
        last_row = df_feat.iloc[-1].to_dict()
    except Exception as e:
        logger.error(f"Feature-Berechnung fehlgeschlagen: {e}")
        return None

    sig_cfg      = config.get('signal', {})
    t_threshold  = float(sig_cfg.get('t_threshold', 4.0))
    min_score    = float(sig_cfg.get('min_score', 20.0))
    min_hit_rate = float(sig_cfg.get('min_hit_rate', 0.4))
    tradeable    = config.get('strategy', {}).get('tradeable_types', [])
    # Strategy type chosen by optimizer (BREAKOUT/MOMENTUM/…); used in trade_params
    strategy_type = config.get('strategy', {}).get('type', '')

    best = None
    for entry in tradeable:
        move_type = entry.get('move_type', '')
        direction = entry.get('direction', 'LONG')
        conds     = entry_conditions.get(move_type)
        if not conds:
            continue

        score, n_met, n_total = compute_signal_score(last_row, conds, t_threshold)
        hit_rate = n_met / n_total if n_total > 0 else 0.0

        if score >= min_score and hit_rate >= min_hit_rate:
            if best is None or score > best['score']:
                best = {
                    'side':        'long' if direction == 'LONG' else 'short',
                    'move_type':   move_type,
                    'score':       round(score, 2),
                    'n_met':       n_met,
                    'n_total':     n_total,
                    'hit_rate':    round(hit_rate, 3),
                    'entry_price': last_row.get('close', 0),
                    'strategy':    strategy_type or move_type.split('_')[0],
                    'last_row':    last_row,
                }

    if best:
        logger.info(
            f"Signal: {best['side'].upper()} | {best['move_type']} | "
            f"Strategie: {best['strategy']} | "
            f"Score={best['score']} | Hit={best['hit_rate']:.0%} "
            f"({best['n_met']}/{best['n_total']})"
        )

        # Pre-compute trade_params with estimated entry price for position sizing
        from probebot.strategy.signal_logic import compute_trade_params
        try:
            best['trade_params'] = compute_trade_params(
                strategy    = best['strategy'],
                move_type   = best['move_type'],
                last_row    = last_row,
                entry_price = float(best['entry_price']),
                side        = best['side'],
                config_risk = config.get('risk', {}),
            )
            logger.info(
                f"TradeParams: SL={best['trade_params'].sl_price:.6f} "
                f"[{best['trade_params'].sl_source}] | "
                f"TP={'trailing' if best['trade_params'].use_trailing else str(round(best['trade_params'].tp_price, 6))} "
                f"[{best['trade_params'].tp_source}]"
            )
        except Exception as e:
            logger.error(f"compute_trade_params fehlgeschlagen: {e}")
            best = None
    else:
        logger.info(f"Kein Signal fuer {symbol} ({timeframe}).")
    return best


# ── Main run function (wrapped by guardian) ───────────────────────────────────

@guardian_decorator
def run_strategy(account: dict, telegram_cfg: dict,
                 symbol: str, timeframe: str, mode: str,
                 logger: logging.Logger):
    logger.info(f"=== probebot | {symbol} ({timeframe}) | {mode.upper()} ===")

    config = _load_config(symbol, timeframe)
    if config is None:
        logger.error(
            f"Keine Config fuer {symbol} ({timeframe}). "
            f"Erst run_pipeline.sh → Optimizer ausfuehren."
        )
        return

    bot_spec = _load_bot_spec(config, symbol, timeframe)
    if bot_spec is None:
        logger.error(f"bot_spec nicht gefunden fuer {symbol}.")
        return

    entry_conditions = bot_spec.get('entry_conditions', {})
    exchange         = Exchange(account)

    signal = None
    if mode == 'signal':
        tracker = read_tracker(symbol, timeframe)
        if tracker.get('status') == 'open':
            logger.info(f"Offener Trade — pruefen statt neues Signal suchen.")
        elif is_candle_cooldown_active(tracker):
            logger.info(f"Candle-Cooldown aktiv — ueberspringe Signal-Suche.")
        else:
            signal = _compute_signal(
                symbol, timeframe, config, entry_conditions, exchange, logger
            )

    full_trade_cycle(
        exchange, symbol, timeframe,
        config, entry_conditions,
        telegram_cfg, logger,
        signal=signal,
    )

    logger.info(f"=== probebot Ende | {symbol} ({timeframe}) ===")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='probebot Strategy Runner')
    parser.add_argument('--symbol',    required=True)
    parser.add_argument('--timeframe', required=True)
    parser.add_argument('--mode',      required=True, choices=['signal', 'check'])
    args = parser.parse_args()

    logger = _setup_logger(args.symbol, args.timeframe)

    try:
        with open(PROJECT_ROOT / 'secret.json', encoding='utf-8') as f:
            secrets = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.critical(f"secret.json Fehler: {e}")
        sys.exit(1)

    account = secrets.get('probebot', {})
    if isinstance(account, list):
        account = account[0] if account else {}
    telegram_cfg = secrets.get('telegram', {})

    if not account.get('api_key') and not account.get('apiKey'):
        logger.critical("Kein 'probebot' Account in secret.json.")
        sys.exit(1)

    run_strategy(account, telegram_cfg, args.symbol, args.timeframe, args.mode, logger)


if __name__ == '__main__':
    main()
