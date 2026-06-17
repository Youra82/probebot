# src/probebot/utils/trade_manager.py
"""
probebot Trade Manager — full_trade_cycle pattern (learned from ltbbot/dnabot).

Key design decisions vs. earlier version:
- Per-symbol tracker file (not a shared active_positions.json)
- SL/TP order IDs stored in tracker → reliable hit detection
- ensure_tp_sl: safety-net to re-place missing SL/TP orders every cycle
- housekeeper: cancel ghost orders + close orphaned positions
- full_trade_cycle: single entry point covering signal → entry → check → close

SL/TP calculation:
  sl_pct:  % of entry price (e.g. 1.5 → 1.5% price move)
  tp_rr:   Risk:Reward ratio  (TP = SL × tp_rr)

Position sizing:
  contracts = (balance × risk_per_trade_pct%) / |entry_price - sl_price|
"""

import json
import logging
import math
import os
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


# ── SL / TP / Position size ───────────────────────────────────────────────────

def compute_sl_tp(entry_price: float, side: str,
                  sl_pct: float, tp_rr: float) -> tuple[float, float]:
    sl_dist = entry_price * sl_pct / 100.0
    tp_dist = sl_dist * tp_rr
    if side == 'long':
        return entry_price - sl_dist, entry_price + tp_dist
    return entry_price + sl_dist, entry_price - tp_dist


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
    """Cancel all ghost orders + close any orphaned exchange position."""
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


# ── ensure_tp_sl: safety-net to re-place missing orders ──────────────────────

def ensure_tp_sl(exchange, tracker: dict, logger: logging.Logger):
    """
    Check if the stored SL/TP trigger orders still exist on the exchange.
    Re-place any missing orders using the prices from the tracker.
    Inspired by dnabot's ensure_tp_sl pattern.
    """
    symbol    = tracker['symbol']
    timeframe = tracker['timeframe']
    side      = tracker['side']
    contracts = tracker['contracts']
    sl_price  = tracker['sl_price']
    tp_price  = tracker['tp_price']
    sl_id     = tracker.get('sl_order_id')
    tp_id     = tracker.get('tp_order_id')
    cl_side   = 'sell' if side == 'long' else 'buy'

    open_trigger_ids = {o['id'] for o in exchange.fetch_open_trigger_orders(symbol)}

    # Re-place SL if missing
    if sl_id and sl_id not in open_trigger_ids:
        logger.warning(f"SL-Order {sl_id} fehlt — lege neu: {sl_price:.6f}")
        try:
            sl_order = exchange.place_trigger_market_order(
                symbol, cl_side, contracts, sl_price, reduce=True
            )
            tracker['sl_order_id'] = sl_order.get('id', sl_id)
            _write_tracker(symbol, timeframe, tracker)
        except Exception as e:
            logger.error(f"ensure_tp_sl: SL-Neuanlage fehlgeschlagen: {e}")

    # Re-place TP if missing
    if tp_id and tp_id not in open_trigger_ids:
        logger.warning(f"TP-Order {tp_id} fehlt — lege neu: {tp_price:.6f}")
        try:
            tp_order = exchange.place_trigger_market_order(
                symbol, cl_side, contracts, tp_price, reduce=True
            )
            tracker['tp_order_id'] = tp_order.get('id', tp_id)
            _write_tracker(symbol, timeframe, tracker)
        except Exception as e:
            logger.error(f"ensure_tp_sl: TP-Neuanlage fehlgeschlagen: {e}")


# ── Detect closed trade ───────────────────────────────────────────────────────

def _detect_close_reason(exchange, tracker: dict, logger: logging.Logger) -> str:
    """
    Try to determine whether SL or TP was hit by checking closed trigger orders.
    Returns 'SL', 'TP', or 'UNKNOWN'.
    """
    sl_id = tracker.get('sl_order_id')
    tp_id = tracker.get('tp_order_id')
    symbol = tracker['symbol']

    closed = exchange.fetch_closed_trigger_orders(symbol, limit=20)
    closed_ids = {o.get('id') for o in closed if o.get('status') in ('closed', 'filled')}

    if tp_id and tp_id in closed_ids:
        return 'TP'
    if sl_id and sl_id in closed_ids:
        return 'SL'
    # Fallback: price-based guess
    entry_p = tracker.get('entry_price', 0)
    sl_p    = tracker.get('sl_price', 0)
    tp_p    = tracker.get('tp_price', 0)
    if entry_p and sl_p and tp_p:
        try:
            positions = exchange.fetch_open_positions(symbol)
            # no position → compare which target was closer to current market
            # (rough heuristic when order IDs are unreliable)
        except Exception:
            pass
    return 'UNKNOWN'


# ── Execute new trade ─────────────────────────────────────────────────────────

def _execute_trade(exchange, symbol: str, timeframe: str,
                   signal: dict, config: dict,
                   telegram_cfg: dict, logger: logging.Logger) -> bool:
    from probebot.utils.telegram import send_message

    risk        = config.get('risk', {})
    side        = signal['side']
    sl_pct      = float(risk.get('sl_pct', 1.5))
    tp_rr       = float(risk.get('tp_rr', 2.0))
    leverage    = int(risk.get('leverage', 10))
    risk_pct    = float(risk.get('risk_per_trade_pct', 1.0))
    margin_mode = 'isolated'

    balance = exchange.fetch_balance_usdt()
    if balance < MIN_NOTIONAL_USDT:
        logger.warning(f"Zu wenig Kapital: {balance:.2f} USDT")
        return False

    exchange.set_margin_mode(symbol, margin_mode)
    exchange.set_leverage(symbol, leverage, margin_mode)

    current_price = float(signal['entry_price'])
    min_amount    = exchange.fetch_min_amount(symbol)

    sl_price_est, _ = compute_sl_tp(current_price, side, sl_pct, tp_rr)
    contracts       = compute_contracts(balance, current_price, sl_price_est,
                                        min_amount, risk_pct)

    # Margin cap
    max_by_margin = (balance * leverage) / current_price * 0.99
    if contracts > max_by_margin:
        logger.warning(f"Margin-Cap: {contracts:.4f} → {max_by_margin:.4f}")
        contracts = max_by_margin

    if contracts * current_price < MIN_NOTIONAL_USDT:
        logger.warning(f"Notional zu klein ({contracts * current_price:.2f} USDT).")
        return False

    entry_side = 'buy' if side == 'long' else 'sell'
    logger.info(f"Entry: {side.upper()} {contracts:.4f} {symbol} | "
                f"{leverage}x | {balance:.2f} USDT | {risk_pct}% Risiko")

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

    sl_price, tp_price = compute_sl_tp(entry_price, side, sl_pct, tp_rr)
    sl_dist_pct = abs(entry_price - sl_price) / entry_price * 100
    tp_dist_pct = abs(tp_price - entry_price) / entry_price * 100

    logger.info(f"Fill: {entry_price:.6f} | SL: {sl_price:.6f} (-{sl_dist_pct:.3f}%) | "
                f"TP: {tp_price:.6f} (+{tp_dist_pct:.3f}%) | "
                f"R:R 1:{tp_dist_pct / sl_dist_pct:.1f}")

    time.sleep(1.0)

    cl_side  = 'sell' if side == 'long' else 'buy'
    sl_order = None
    tp_order = None

    try:
        sl_order = exchange.place_trigger_market_order(
            symbol, cl_side, filled, sl_price, reduce=True
        )
        logger.info(f"SL platziert @ {sl_price:.6f}  ID: {sl_order.get('id')}")
    except Exception as e:
        logger.error(f"SL fehlgeschlagen: {e} — schliesse Position!")
        try:
            exchange.close_position(symbol)
        except Exception as ce:
            logger.critical(f"Position nicht schliessbar: {ce}")
        return False

    try:
        tp_order = exchange.place_trigger_market_order(
            symbol, cl_side, filled, tp_price, reduce=True
        )
        logger.info(f"TP platziert @ {tp_price:.6f}  ID: {tp_order.get('id')}")
    except Exception as e:
        logger.error(f"TP fehlgeschlagen (nicht kritisch): {e}")

    # Write tracker with order IDs
    tracker = {
        'status':               'open',
        'symbol':               symbol,
        'timeframe':            timeframe,
        'side':                 side,
        'move_type':            signal.get('move_type', ''),
        'entry_price':          entry_price,
        'sl_price':             sl_price,
        'tp_price':             tp_price,
        'contracts':            filled,
        'sl_order_id':          sl_order.get('id') if sl_order else None,
        'tp_order_id':          tp_order.get('id') if tp_order else None,
        'active_since':         datetime.now(timezone.utc).isoformat(),
        'candle_blocked_until': '',
    }
    _write_tracker(symbol, timeframe, tracker)

    # Telegram
    emoji     = '🟢' if side == 'long' else '🔴'
    risk_usdt = balance * risk_pct / 100.0
    msg = (
        f"🚀 probebot SIGNAL: {symbol} ({timeframe})\n"
        f"{'─' * 32}\n"
        f"{emoji} {side.upper()} | {signal.get('move_type', '')}\n"
        f"📊 Score: {signal.get('score', 0):.1f} "
        f"({signal.get('n_met', 0)}/{signal.get('n_total', 0)} Bed., "
        f"{signal.get('hit_rate', 0):.0%})\n"
        f"💰 Entry:   ${entry_price:.6f}\n"
        f"🛑 SL:      ${sl_price:.6f} (-{sl_dist_pct:.2f}%)\n"
        f"🎯 TP:      ${tp_price:.6f} (+{tp_dist_pct:.2f}%)\n"
        f"📐 R:R:     1:{tp_dist_pct / sl_dist_pct:.1f}\n"
        f"⚙️ Hebel:   {leverage}x\n"
        f"🛡️ Risiko:  {risk_pct:.1f}% ({risk_usdt:.2f} USDT)\n"
        f"📦 Kontr.:  {filled:.4f}"
    )
    send_message(telegram_cfg.get('bot_token'), telegram_cfg.get('chat_id'), msg)
    logger.info("Trade erfolgreich platziert.")
    return True


# ── Full trade cycle ──────────────────────────────────────────────────────────

def full_trade_cycle(exchange, symbol: str, timeframe: str,
                     config: dict, entry_conditions: dict,
                     telegram_cfg: dict, logger: logging.Logger,
                     signal: Optional[dict] = None):
    """
    Single entry point for one strategy tick.

    signal: pre-computed signal dict (from strategy/run.py).
            If None: only position management is done (check mode).

    Flow:
      A) Tracker has open position:
         1. Check if exchange position still exists
         2. If closed: detect SL/TP, notify Telegram, set idle + candle cooldown
         3. If open:   run ensure_tp_sl safety-net, log unrealized PnL
      B) Tracker is idle:
         1. Housekeeper (clean up any ghost orders/positions)
         2. Check candle cooldown
         3. If signal provided AND passes criteria: execute trade
    """
    from probebot.utils.telegram import send_message

    tracker = read_tracker(symbol, timeframe)

    # ── A: Position open ──────────────────────────────────────────────────────
    if tracker.get('status') == 'open':
        positions = exchange.fetch_open_positions(symbol)

        if positions:
            pos      = positions[0]
            unr_pnl  = float(pos.get('unrealizedPnl', 0.0))
            entry_p  = tracker.get('entry_price', '?')
            side_str = tracker.get('side', '?')
            logger.info(
                f"Position offen: {side_str.upper()} {symbol} "
                f"| Entry: {entry_p} | Unrealized PnL: {unr_pnl:+.2f} USDT"
            )
            # Safety-net: ensure SL/TP orders exist
            ensure_tp_sl(exchange, tracker, logger)
            return

        # Position closed on exchange → clean up
        logger.info(f"Position auf Exchange geschlossen: {symbol} ({timeframe})")
        housekeeper(exchange, symbol, logger)

        close_reason = _detect_close_reason(exchange, tracker, logger)
        side_str = tracker.get('side', '?')
        emoji    = '🟢' if side_str == 'long' else '🔴'
        result_emoji = '✅' if close_reason == 'TP' else ('❌' if close_reason == 'SL' else '⚪')

        msg = (
            f"{result_emoji} probebot GESCHLOSSEN ({close_reason})\n"
            f"{'─' * 32}\n"
            f"{emoji} {side_str.upper()} | {symbol} ({timeframe})\n"
            f"🎯 Move-Typ: {tracker.get('move_type', '?')}\n"
            f"💰 Entry:  ${tracker.get('entry_price', '?')}\n"
            f"🛑 SL:     ${tracker.get('sl_price', '?')}\n"
            f"🎯 TP:     ${tracker.get('tp_price', '?')}\n"
            f"🕐 Seit:   {tracker.get('active_since', '?')}\n"
            f"{'─' * 32}\n"
            f"⏳ Warte auf naechstes Signal..."
        )
        send_message(telegram_cfg.get('bot_token'), telegram_cfg.get('chat_id'), msg)

        candle_blocked = _candle_end_iso(timeframe)
        _set_idle(symbol, timeframe, candle_blocked)
        logger.info(f"Tracker idle. Candle-Cooldown bis {candle_blocked}")
        return

    # ── B: Idle ───────────────────────────────────────────────────────────────
    housekeeper(exchange, symbol, logger)

    if is_candle_cooldown_active(tracker):
        logger.info(f"Candle-Cooldown aktiv bis {tracker.get('candle_blocked_until')} — ueberspringe.")
        return

    if signal is None:
        logger.info(f"Kein Signal — idle.")
        return

    logger.info(
        f"Signal: {signal['side'].upper()} | {signal.get('move_type')} | "
        f"Score: {signal.get('score'):.1f} | Hit-Rate: {signal.get('hit_rate'):.0%}"
    )

    _execute_trade(exchange, symbol, timeframe, signal, config, telegram_cfg, logger)
