# src/probebot/utils/trade_manager.py
"""
probebot Trade Manager — full_trade_cycle pattern (learned from ltbbot/dnabot).

Per-symbol tracker file stores SL/TP order IDs + strategy-specific params.
Strategy-specific SL/TP is computed by signal_logic.py and stored in signal['trade_params'].
"""
import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

TRACKER_DIR       = PROJECT_ROOT / 'artifacts' / 'tracker'
MIN_NOTIONAL_USDT = 5.0

_TF_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '8h': 28800,
    '12h': 43200, '1d': 86400, '3d': 259200, '1w': 604800,
}


# ── Tracker I/O ───────────────────────────────────────────────────────────────

def _tracker_path(symbol: str, timeframe: str) -> Path:
    safe = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    return TRACKER_DIR / f'tracker_{safe}.json'


def read_tracker(symbol: str, timeframe: str) -> dict:
    path = _tracker_path(symbol, timeframe)
    if not path.exists():
        return {'status': 'idle'}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {'status': 'idle'}
    except Exception:
        return {'status': 'idle'}


def _write_tracker(symbol: str, timeframe: str, data: dict):
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    _tracker_path(symbol, timeframe).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8'
    )


def _set_idle(symbol: str, timeframe: str, candle_blocked_until: str = ''):
    _write_tracker(symbol, timeframe, {
        'status':               'idle',
        'candle_blocked_until': candle_blocked_until,
    })


# ── Candle cooldown ───────────────────────────────────────────────────────────

def _candle_end_iso(timeframe: str) -> str:
    tf_secs = _TF_SECONDS.get(timeframe, 3600)
    now_ts  = datetime.now(timezone.utc).timestamp()
    return datetime.fromtimestamp(
        math.ceil(now_ts / tf_secs) * tf_secs, tz=timezone.utc
    ).isoformat()


def is_candle_cooldown_active(tracker: dict) -> bool:
    blocked = tracker.get('candle_blocked_until', '')
    if not blocked:
        return False
    try:
        return datetime.now(timezone.utc) < datetime.fromisoformat(blocked)
    except ValueError:
        return False


# ── Position size ─────────────────────────────────────────────────────────────

def compute_contracts(balance: float, entry_price: float, sl_price: float,
                      min_amount: float,
                      risk_per_trade_pct: float = 1.0) -> float:
    risk_usdt = balance * risk_per_trade_pct / 100.0
    sl_dist   = abs(entry_price - sl_price)
    if sl_dist <= 0:
        return min_amount
    return max(risk_usdt / sl_dist, min_amount)


# ── Housekeeper ───────────────────────────────────────────────────────────────

def housekeeper(exchange, symbol: str, logger: logging.Logger):
    logger.info(f"Housekeeper: {symbol}")
    try:
        exchange.cancel_all_orders(symbol)
        time.sleep(1)
        positions = exchange.fetch_open_positions(symbol)
        if positions:
            pos        = positions[0]
            close_side = 'sell' if pos['side'] == 'long' else 'buy'
            amount     = float(pos.get('contracts') or pos.get('contractSize') or 0)
            logger.warning(f"Housekeeper: verwaiste {pos['side']}-Position — schliesse...")
            exchange.place_market_order(symbol, close_side, amount, reduce=True)
            time.sleep(2)
    except Exception as e:
        logger.error(f"Housekeeper-Fehler: {e}")


# ── ensure_tp_sl: re-place missing SL/TP orders (dnabot pattern) ─────────────

def ensure_tp_sl(exchange, tracker: dict, logger: logging.Logger):
    symbol      = tracker['symbol']
    timeframe   = tracker['timeframe']
    side        = tracker['side']
    contracts   = tracker['contracts']
    sl_price    = tracker['sl_price']
    tp_price    = tracker.get('tp_price')
    trail_act   = tracker.get('trailing_activation')
    trail_pct   = tracker.get('trailing_pct', 0.8)
    use_trail   = tracker.get('use_trailing', False)
    sl_id       = tracker.get('sl_order_id')
    tp_id       = tracker.get('tp_order_id')
    cl_side     = 'sell' if side == 'long' else 'buy'

    open_ids = {o['id'] for o in exchange.fetch_open_trigger_orders(symbol)}

    if sl_id and sl_id not in open_ids:
        logger.warning(f"SL-Order fehlt — lege neu @ {sl_price:.6f}")
        try:
            order = exchange.place_trigger_market_order(
                symbol, cl_side, contracts, sl_price, reduce=True
            )
            tracker['sl_order_id'] = order.get('id', sl_id)
            _write_tracker(symbol, timeframe, tracker)
        except Exception as e:
            logger.error(f"SL-Neuanlage fehlgeschlagen: {e}")

    if tp_id and tp_id not in open_ids:
        if use_trail and trail_act:
            logger.warning(f"Trailing Stop fehlt — lege neu, Aktivierung @ {trail_act:.6f}")
            try:
                order = exchange.place_trailing_stop_order(
                    symbol, cl_side, contracts, trail_act, trail_pct, reduce=True
                )
                tracker['tp_order_id'] = order.get('id', tp_id)
                _write_tracker(symbol, timeframe, tracker)
            except Exception as e:
                logger.error(f"Trailing-Stop-Neuanlage fehlgeschlagen: {e}")
        elif tp_price:
            logger.warning(f"TP-Order fehlt — lege neu @ {tp_price:.6f}")
            try:
                order = exchange.place_trigger_market_order(
                    symbol, cl_side, contracts, tp_price, reduce=True
                )
                tracker['tp_order_id'] = order.get('id', tp_id)
                _write_tracker(symbol, timeframe, tracker)
            except Exception as e:
                logger.error(f"TP-Neuanlage fehlgeschlagen: {e}")


# ── Detect close reason ───────────────────────────────────────────────────────

def _detect_close_reason(exchange, tracker: dict, logger: logging.Logger) -> str:
    sl_id  = tracker.get('sl_order_id')
    tp_id  = tracker.get('tp_order_id')
    symbol = tracker['symbol']

    try:
        closed     = exchange.fetch_closed_trigger_orders(symbol, limit=20)
        closed_ids = {o.get('id') for o in closed
                      if o.get('status') in ('closed', 'filled')}
        if tp_id and tp_id in closed_ids:
            return 'TP'
        if sl_id and sl_id in closed_ids:
            return 'SL'
    except Exception as e:
        logger.debug(f"Konnte geschlossene Orders nicht abrufen: {e}")
    return 'UNKNOWN'


# ── Execute new trade ─────────────────────────────────────────────────────────

def _execute_trade(exchange, symbol: str, timeframe: str,
                   signal: dict, config: dict,
                   telegram_cfg: dict, logger: logging.Logger) -> bool:
    """
    Execute a trade using strategy-specific SL/TP from signal['trade_params'].
    The trade_params TradeParams object is computed by signal_logic.py in run.py.
    """
    from probebot.utils.telegram import send_message
    from probebot.strategy.signal_logic import compute_trade_params

    risk        = config.get('risk', {})
    side        = signal['side']
    leverage    = int(risk.get('leverage', 10))
    risk_pct    = float(risk.get('risk_per_trade_pct', 1.0))
    margin_mode = 'isolated'
    strategy    = signal.get('strategy', 'HYBRID')

    # Pre-computed trade params (may be recomputed after fill for slippage)
    tp_params = signal.get('trade_params')
    if tp_params is None:
        logger.error("Keine trade_params im Signal.")
        return False

    balance = exchange.fetch_balance_usdt()
    if balance < MIN_NOTIONAL_USDT:
        logger.warning(f"Zu wenig Kapital: {balance:.2f} USDT")
        return False

    exchange.set_margin_mode(symbol, margin_mode)
    exchange.set_leverage(symbol, leverage, margin_mode)

    current_price = float(signal['entry_price'])
    min_amount    = exchange.fetch_min_amount(symbol)
    contracts     = compute_contracts(balance, current_price, tp_params.sl_price,
                                      min_amount, risk_pct)

    # Margin cap
    max_by_margin = (balance * leverage) / current_price * 0.99
    if contracts > max_by_margin:
        logger.warning(f"Margin-Cap: {contracts:.4f} → {max_by_margin:.4f}")
        contracts = max_by_margin

    if contracts * current_price < MIN_NOTIONAL_USDT:
        logger.warning(f"Notional zu klein ({contracts * current_price:.2f} USDT).")
        return False

    logger.info(
        f"Entry: {side.upper()} {contracts:.4f} {symbol} | "
        f"Strategie: {strategy} | {leverage}x | {balance:.2f} USDT | {risk_pct}% Risiko"
    )

    entry_side = 'buy' if side == 'long' else 'sell'
    try:
        order = exchange.place_market_order(symbol, entry_side, contracts,
                                            margin_mode=margin_mode)
    except Exception as e:
        logger.error(f"Entry fehlgeschlagen: {e}")
        return False

    entry_price = float(order.get('average') or order.get('price') or current_price)
    if entry_price <= 0:
        entry_price = current_price
    filled = float(order.get('filled') or order.get('amount') or contracts)
    if filled <= 0:
        filled = contracts

    # Recompute with actual fill price to correct for slippage
    tp_params = compute_trade_params(
        strategy    = strategy,
        move_type   = signal.get('move_type', ''),
        last_row    = signal.get('last_row', {}),
        entry_price = entry_price,
        side        = side,
        config_risk = risk,
    )

    sl_price  = tp_params.sl_price
    tp_price  = tp_params.tp_price
    use_trail = tp_params.use_trailing
    trail_act = tp_params.trailing_activation_price
    trail_pct = tp_params.trailing_pct

    sl_dist_pct = abs(entry_price - sl_price) / entry_price * 100
    tp_ref      = tp_price or trail_act
    tp_dist_pct = abs(tp_ref - entry_price) / entry_price * 100 if tp_ref else sl_dist_pct * 1.5

    logger.info(
        f"Fill: {entry_price:.6f} | "
        f"SL: {sl_price:.6f} (-{sl_dist_pct:.3f}%)  [{tp_params.sl_source}] | "
        f"TP: {'trail ' + str(trail_pct) + '%' if use_trail else str(round(tp_price, 6))} "
        f"(+{tp_dist_pct:.3f}%)  [{tp_params.tp_source}]"
    )

    time.sleep(1.0)
    cl_side  = 'sell' if side == 'long' else 'buy'
    sl_order = None
    tp_order = None

    # SL
    try:
        sl_order = exchange.place_trigger_market_order(
            symbol, cl_side, filled, sl_price, reduce=True
        )
        logger.info(f"SL @ {sl_price:.6f}  ID: {sl_order.get('id')}")
    except Exception as e:
        logger.error(f"SL fehlgeschlagen: {e} — schliesse Position!")
        try:
            exchange.close_position(symbol)
        except Exception as ce:
            logger.critical(f"Position nicht schliessbar: {ce}")
        return False

    # TP or Trailing Stop
    if use_trail and trail_act:
        try:
            tp_order = exchange.place_trailing_stop_order(
                symbol, cl_side, filled, trail_act, trail_pct, reduce=True
            )
            logger.info(
                f"Trailing Stop: Aktivierung @ {trail_act:.6f} | "
                f"Callback {trail_pct}%  ID: {tp_order.get('id')}"
            )
        except Exception as e:
            logger.error(f"Trailing Stop fehlgeschlagen (nicht kritisch): {e}")
    elif tp_price:
        try:
            tp_order = exchange.place_trigger_market_order(
                symbol, cl_side, filled, tp_price, reduce=True
            )
            logger.info(f"TP @ {tp_price:.6f}  ID: {tp_order.get('id')}")
        except Exception as e:
            logger.error(f"TP fehlgeschlagen (nicht kritisch): {e}")

    # Write tracker
    tracker = {
        'status':               'open',
        'symbol':               symbol,
        'timeframe':            timeframe,
        'side':                 side,
        'strategy':             strategy,
        'move_type':            signal.get('move_type', ''),
        'entry_price':          entry_price,
        'sl_price':             sl_price,
        'tp_price':             tp_price,
        'use_trailing':         use_trail,
        'trailing_activation':  trail_act,
        'trailing_pct':         trail_pct,
        'sl_source':            tp_params.sl_source,
        'tp_source':            tp_params.tp_source,
        'contracts':            filled,
        'sl_order_id':          sl_order.get('id') if sl_order else None,
        'tp_order_id':          tp_order.get('id') if tp_order else None,
        'active_since':         datetime.now(timezone.utc).isoformat(),
        'candle_blocked_until': '',
    }
    _write_tracker(symbol, timeframe, tracker)

    # Telegram
    emoji      = '🟢' if side == 'long' else '🔴'
    risk_usdt  = balance * risk_pct / 100.0
    tp_display = (f"Trailing {trail_pct}% ab ${trail_act:.4f}"
                  if use_trail else f"${tp_price:.6f} (+{tp_dist_pct:.2f}%)")
    rr_display = f"1:{tp_dist_pct / sl_dist_pct:.1f}" if sl_dist_pct > 0 else "?"

    msg = (
        f"🚀 probebot SIGNAL: {symbol} ({timeframe})\n"
        f"{'─' * 32}\n"
        f"{emoji} {side.upper()} | {signal.get('move_type', '')}\n"
        f"🎯 Strategie: {strategy}\n"
        f"📊 Score: {signal.get('score', 0):.1f} "
        f"({signal.get('n_met', 0)}/{signal.get('n_total', 0)} Bed., "
        f"{signal.get('hit_rate', 0):.0%})\n"
        f"💰 Entry:   ${entry_price:.6f}\n"
        f"🛑 SL:      ${sl_price:.6f} (-{sl_dist_pct:.2f}%)  [{tp_params.sl_source}]\n"
        f"🎯 TP:      {tp_display}  [{tp_params.tp_source}]\n"
        f"📐 R:R:     {rr_display}\n"
        f"⚙️ Hebel:   {leverage}x\n"
        f"🛡️ Risiko:  {risk_pct:.1f}% ({risk_usdt:.2f} USDT)\n"
        f"📦 Kontr.:  {filled:.4f}"
    )
    send_message(telegram_cfg.get('bot_token'), telegram_cfg.get('chat_id'), msg)
    logger.info(f"Trade platziert: {strategy} | {tp_params.sl_source} | {tp_params.tp_source}")
    return True


# ── Full trade cycle ──────────────────────────────────────────────────────────

def full_trade_cycle(exchange, symbol: str, timeframe: str,
                     config: dict, entry_conditions: dict,
                     telegram_cfg: dict, logger: logging.Logger,
                     signal: Optional[dict] = None):
    """
    Single entry point for one strategy tick.

    signal must include 'trade_params' (TradeParams from signal_logic.py),
    'strategy', 'last_row' — set by strategy/run.py before calling here.

    Flow A (position open):
      1. Exchange position still exists → ensure_tp_sl + log PnL
      2. Exchange position closed → detect SL/TP, notify, set idle

    Flow B (idle):
      1. Housekeeper
      2. Candle cooldown check
      3. Execute trade if signal provided
    """
    from probebot.utils.telegram import send_message

    tracker = read_tracker(symbol, timeframe)

    # ── A: Open position ──────────────────────────────────────────────────────
    if tracker.get('status') == 'open':
        positions = exchange.fetch_open_positions(symbol)

        if positions:
            pos     = positions[0]
            unr_pnl = float(pos.get('unrealizedPnl', 0.0))
            logger.info(
                f"Position offen: {tracker.get('side','?').upper()} {symbol} "
                f"| Entry: {tracker.get('entry_price','?')} "
                f"| Strategie: {tracker.get('strategy','?')} "
                f"| Unrealized PnL: {unr_pnl:+.2f} USDT"
            )
            ensure_tp_sl(exchange, tracker, logger)
            return

        # Position on exchange closed
        logger.info(f"Position geschlossen: {symbol} ({timeframe})")
        housekeeper(exchange, symbol, logger)

        close_reason = _detect_close_reason(exchange, tracker, logger)
        side_str  = tracker.get('side', '?')
        strategy  = tracker.get('strategy', '?')
        emoji     = '🟢' if side_str == 'long' else '🔴'
        res_emoji = '✅' if close_reason == 'TP' else ('❌' if close_reason == 'SL' else '⚪')

        msg = (
            f"{res_emoji} probebot GESCHLOSSEN ({close_reason})\n"
            f"{'─' * 32}\n"
            f"{emoji} {side_str.upper()} | {symbol} ({timeframe})\n"
            f"🎯 Strategie: {strategy} | {tracker.get('move_type', '?')}\n"
            f"💰 Entry:  ${tracker.get('entry_price', '?')}\n"
            f"🛑 SL:     ${tracker.get('sl_price', '?')}  [{tracker.get('sl_source', '')}]\n"
            f"🎯 TP:     ${tracker.get('tp_price', '?')}  [{tracker.get('tp_source', '')}]\n"
            f"🕐 Seit:   {tracker.get('active_since', '?')}\n"
            f"{'─' * 32}\n"
            f"⏳ Naechstes Signal wird gesucht..."
        )
        send_message(telegram_cfg.get('bot_token'), telegram_cfg.get('chat_id'), msg)

        candle_blocked = _candle_end_iso(timeframe)
        _set_idle(symbol, timeframe, candle_blocked)
        logger.info(f"Tracker idle. Cooldown bis {candle_blocked}")
        return

    # ── B: Idle ───────────────────────────────────────────────────────────────
    housekeeper(exchange, symbol, logger)

    if is_candle_cooldown_active(tracker):
        logger.info(f"Candle-Cooldown bis {tracker.get('candle_blocked_until')} — ueberspringe.")
        return

    if signal is None:
        logger.info("Kein Signal — idle.")
        return

    _execute_trade(exchange, symbol, timeframe, signal, config, telegram_cfg, logger)
