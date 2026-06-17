# src/probebot/strategy/run.py
"""
probebot Live Strategy Runner

Modes:
  --mode signal  : compute features on latest closed candle,
                   check signal score, place trade if entry criteria met
  --mode check   : check if open position is still on exchange

Signal flow:
  1. Load config_SYM_TF.json  (optimizer output — signal + risk params)
  2. Load bot_spec_SYM_TF.json  (entry_conditions per move type)
  3. Fetch 200 recent OHLCV candles, drop last (still open)
  4. Compute all 177 features (reuse probebot feature engine)
  5. For each tradeable move type: compute_signal_score on last candle
  6. Best type with score >= min_score AND hit_rate >= min_hit_rate -> entry
  7. Place market order + SL/TP trigger orders
"""
import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from probebot.utils.exchange import Exchange
from probebot.utils.telegram import send_message
from probebot.utils.trade_manager import (
    is_strategy_free,
    is_candle_cooldown_active,
    execute_signal_trade,
    check_position_status,
)
from probebot.analysis.backtester import compute_signal_score


# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logger(symbol: str, timeframe: str) -> logging.Logger:
    safe   = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
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


# ── Config / bot_spec helpers ─────────────────────────────────────────────────

def _load_config(symbol: str, timeframe: str) -> dict | None:
    sym_safe    = symbol.replace('/', '_').replace(':', '_')
    config_path = PROJECT_ROOT / 'src' / 'probebot' / 'strategy' / 'configs' / \
                  f'config_{sym_safe}_{timeframe}.json'
    if not config_path.exists():
        return None
    with open(config_path, encoding='utf-8') as f:
        return json.load(f)


def _load_bot_spec(config: dict, symbol: str, timeframe: str) -> dict | None:
    bot_spec_path = config.get('strategy', {}).get('bot_spec_path', '')
    p = Path(bot_spec_path)
    if not p.exists():
        sym_safe = symbol.replace('/', '_').replace(':', '_')
        p = PROJECT_ROOT / 'artifacts' / 'db' / f'bot_spec_{sym_safe}_{timeframe}.json'
    if not p.exists():
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


# ── Feature computation for live candle ───────────────────────────────────────

def _compute_live_features(df_ohlcv) -> dict | None:
    """
    Run the full feature engine on recent OHLCV and return the last row as dict.
    Requires at least 200 candles for indicator warmup.
    """
    from probebot.features.engine import compute_all_features
    try:
        df_feat = compute_all_features(df_ohlcv, min_candles=100)
        last    = df_feat.iloc[-1].to_dict()
        return last
    except Exception as e:
        return None


# ── Signal logic ──────────────────────────────────────────────────────────────

def _find_best_signal(last_row: dict, config: dict, entry_conditions: dict) -> dict | None:
    """
    Check all tradeable move types against the last candle.
    Returns best signal dict or None.
    """
    sig_cfg       = config.get('signal', {})
    t_threshold   = float(sig_cfg.get('t_threshold', 4.0))
    min_score     = float(sig_cfg.get('min_score', 20.0))
    min_hit_rate  = float(sig_cfg.get('min_hit_rate', 0.4))
    tradeable     = config.get('strategy', {}).get('tradeable_types', [])

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
                    'side':          'long' if direction == 'LONG' else 'short',
                    'move_type':     move_type,
                    'score':         round(score, 2),
                    'n_met':         n_met,
                    'n_total':       n_total,
                    'hit_rate':      round(hit_rate, 3),
                    'entry_price':   last_row.get('close', 0),
                    'signal_reason': (
                        f"{move_type} score={score:.1f} "
                        f"({n_met}/{n_total} conds met, hit={hit_rate:.0%})"
                    ),
                }
    return best


# ── Main runner ───────────────────────────────────────────────────────────────

def run(symbol: str, timeframe: str, mode: str,
        secrets: dict, logger: logging.Logger):

    logger.info(f"=== probebot | {symbol} ({timeframe}) | Modus: {mode} ===")

    # Load config
    config = _load_config(symbol, timeframe)
    if config is None:
        logger.error(
            f"Keine Config gefunden fuer {symbol} ({timeframe}). "
            f"Erst run_pipeline.sh -> Optimizer ausfuehren."
        )
        return

    account       = secrets.get('probebot', [{}])
    account       = account[0] if isinstance(account, list) else account
    telegram_cfg  = secrets.get('telegram', {})
    exchange      = Exchange(account)

    if mode == 'check':
        check_position_status(exchange, symbol, timeframe, telegram_cfg, logger)
        return

    # ── Signal mode ───────────────────────────────────────────────────────────
    if not is_strategy_free(symbol, timeframe):
        logger.info(f"{symbol} ({timeframe}): offener Trade — ueberspringe.")
        return

    if is_candle_cooldown_active(symbol, timeframe):
        logger.info(f"{symbol} ({timeframe}): Candle-Cooldown aktiv — ueberspringe.")
        return

    # Load bot_spec for entry_conditions
    bot_spec = _load_bot_spec(config, symbol, timeframe)
    if bot_spec is None:
        logger.error(f"bot_spec nicht gefunden fuer {symbol}. Erst run_pipeline.sh ausfuehren.")
        return

    entry_conditions = bot_spec.get('entry_conditions', {})
    if not entry_conditions:
        logger.error(f"Keine entry_conditions in bot_spec fuer {symbol}.")
        return

    # Fetch OHLCV
    df = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=250)
    if df.empty or len(df) < 101:
        logger.warning(f"Zu wenig Kerzen ({len(df)}) fuer {symbol}. Ueberspringe.")
        return

    # Drop last (still-open) candle — signal only on closed candles
    df_closed = df.iloc[:-1].reset_index(drop=True)

    # Compute features
    logger.info(f"Berechne Features fuer {len(df_closed)} Kerzen...")
    last_row = _compute_live_features(df_closed)
    if last_row is None:
        logger.error("Feature-Berechnung fehlgeschlagen.")
        return

    # Find signal
    signal = _find_best_signal(last_row, config, entry_conditions)

    if signal is None:
        logger.info(f"Kein Signal fuer {symbol} ({timeframe}).")
        return

    logger.info(
        f"SIGNAL: {signal['side'].upper()} | {signal['move_type']} | "
        f"Score: {signal['score']} | Hit-Rate: {signal['hit_rate']:.0%}"
    )

    # Double-check free (race condition)
    if not is_strategy_free(symbol, timeframe):
        logger.info(f"{symbol} parallel belegt — ueberspringe.")
        return

    execute_signal_trade(exchange, symbol, timeframe, signal, config, telegram_cfg, logger)

    logger.info(f"=== probebot Ende | {symbol} ({timeframe}) ===")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='probebot Live Strategy Runner')
    parser.add_argument('--symbol',    required=True)
    parser.add_argument('--timeframe', required=True)
    parser.add_argument('--mode',      required=True, choices=['signal', 'check'])
    args   = parser.parse_args()

    logger = _setup_logger(args.symbol, args.timeframe)

    try:
        with open(PROJECT_ROOT / 'settings.json', encoding='utf-8') as f:
            settings = json.load(f)  # noqa: F841
        with open(PROJECT_ROOT / 'secret.json', encoding='utf-8') as f:
            secrets = json.load(f)
    except FileNotFoundError as e:
        logger.critical(f"Datei nicht gefunden: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.critical(f"JSON-Fehler: {e}")
        sys.exit(1)

    try:
        run(args.symbol, args.timeframe, args.mode, secrets, logger)
    except Exception as e:
        logger.error(f"Fehler: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
