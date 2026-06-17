# src/probebot/utils/trade_manager.py
"""
Trade Manager for probebot live trading.

SL/TP from config directly (sl_pct in % of entry price, tp_rr = TP/SL ratio).
Position sizing: (balance * risk_per_trade_pct%) / sl_distance_in_price.
Multi-position state via artifacts/tracker/active_positions.json.
"""
import json
import logging
import math
import os
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

ACTIVE_POSITIONS_PATH = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker', 'active_positions.json')
CANDLE_COOLDOWNS_PATH = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker', 'candle_cooldowns.json')
MIN_NOTIONAL_USDT     = 5.0

_TF_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '8h': 28800,
    '12h': 43200, '1d': 86400, '3d': 259200, '1w': 604800,
}


# ── Candle Cooldown ───────────────────────────────────────────────────────────

def _read_cooldowns() -> list:
    if not os.path.exists(CANDLE_COOLDOWNS_PATH):
        return []
    try:
        with open(CANDLE_COOLDOWNS_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_cooldowns(cooldowns: list):
    os.makedirs(os.path.dirname(CANDLE_COOLDOWNS_PATH), exist_ok=True)
    with open(CANDLE_COOLDOWNS_PATH, 'w') as f:
        json.dump(cooldowns, f, indent=2)


def set_candle_cooldown(symbol: str, timeframe: str):
    tf_secs = _TF_SECONDS.get(timeframe)
    if tf_secs is None:
        return
    now_ts        = datetime.now(timezone.utc).timestamp()
    candle_end_ts = math.ceil(now_ts / tf_secs) * tf_secs
    blocked_until = datetime.fromtimestamp(candle_end_ts, tz=timezone.utc).isoformat()
    cooldowns     = [c for c in _read_cooldowns()
                     if not (c.get('symbol') == symbol and c.get('timeframe') == timeframe)]
    cooldowns.append({'symbol': symbol, 'timeframe': timeframe, 'blocked_until': blocked_until})
    _write_cooldowns(cooldowns)
    logging.getLogger(__name__).info(
        f"Candle-Cooldown: {symbol} ({timeframe}) bis {blocked_until}"
    )


def is_candle_cooldown_active(symbol: str, timeframe: str) -> bool:
    now = datetime.now(timezone.utc)
    for c in _read_cooldowns():
        if c.get('symbol') == symbol and c.get('timeframe') == timeframe:
            try:
                return now < datetime.fromisoformat(c['blocked_until'])
            except (KeyError, ValueError):
                return False
    return False


# ── Position State ────────────────────────────────────────────────────────────

def _read_positions() -> list:
    if not os.path.exists(ACTIVE_POSITIONS_PATH):
        return []
    try:
        with open(ACTIVE_POSITIONS_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_positions(positions: list):
    os.makedirs(os.path.dirname(ACTIVE_POSITIONS_PATH), exist_ok=True)
    with open(ACTIVE_POSITIONS_PATH, 'w') as f:
        json.dump(positions, f, indent=2)


def read_position(symbol: str, timeframe: str) -> dict | None:
    for pos in _read_positions():
        if pos.get('symbol') == symbol and pos.get('timeframe') == timeframe:
            return pos
    return None


def is_strategy_free(symbol: str, timeframe: str) -> bool:
    return read_position(symbol, timeframe) is None


def claim_position(symbol: str, timeframe: str, side: str,
                   entry_price: float, sl_price: float,
                   tp_price: float, contracts: float,
                   move_type: str = '') -> bool:
    positions = _read_positions()
    for pos in positions:
        if pos.get('symbol') == symbol and pos.get('timeframe') == timeframe:
            return False
    positions.append({
        'symbol':       symbol,
        'timeframe':    timeframe,
        'side':         side,
        'move_type':    move_type,
        'entry_price':  entry_price,
        'sl_price':     sl_price,
        'tp_price':     tp_price,
        'contracts':    contracts,
        'active_since': datetime.now(timezone.utc).isoformat(),
    })
    _write_positions(positions)
    return True


def clear_position(symbol: str, timeframe: str):
    positions = [p for p in _read_positions()
                 if not (p.get('symbol') == symbol and p.get('timeframe') == timeframe)]
    _write_positions(positions)
    logging.getLogger(__name__).info(f"Position entfernt: {symbol} ({timeframe})")


# ── SL / TP / Size Calculation ────────────────────────────────────────────────

def compute_sl_tp(entry_price: float, side: str,
                  sl_pct: float, tp_rr: float) -> tuple[float, float]:
    """
    sl_pct:  stop-loss as % of entry price (e.g. 1.5 → 1.5%)
    tp_rr:   take-profit R:R ratio (e.g. 2.0 → TP = 2× SL distance)
    """
    sl_dist = entry_price * sl_pct / 100.0
    tp_dist = sl_dist * tp_rr
    if side == 'long':
        return entry_price - sl_dist, entry_price + tp_dist
    else:
        return entry_price + sl_dist, entry_price - tp_dist


def compute_contracts(balance: float, entry_price: float, sl_price: float,
                      min_amount: float, risk_per_trade_pct: float = 1.0) -> float:
    risk_usdt  = balance * risk_per_trade_pct / 100.0
    sl_dist    = abs(entry_price - sl_price)
    if sl_dist <= 0:
        return min_amount
    contracts  = risk_usdt / sl_dist
    return max(contracts, min_amount)


# ── Execute Trade ─────────────────────────────────────────────────────────────

def execute_signal_trade(exchange, symbol: str, timeframe: str,
                         signal: dict, config: dict,
                         telegram_cfg: dict, logger: logging.Logger) -> bool:
    """
    signal keys: side ('long'|'short'), entry_price, move_type, score, signal_reason
    config:      full config_*.json dict (risk + signal sections)
    Returns True on successful entry.
    """
    from probebot.utils.telegram import send_message

    risk        = config.get('risk', {})
    side        = signal['side']
    sl_pct      = float(risk.get('sl_pct', 1.5))
    tp_rr       = float(risk.get('tp_rr', 2.0))
    leverage    = int(risk.get('leverage', 10))
    risk_pct    = float(risk.get('risk_per_trade_pct', 1.0))
    margin_mode = 'isolated'

    # Balance
    balance = exchange.fetch_balance_usdt()
    if balance < MIN_NOTIONAL_USDT:
        logger.warning(f"Zu wenig Kapital: {balance:.2f} USDT")
        return False

    # Margin + Leverage
    exchange.set_margin_mode(symbol, margin_mode)
    exchange.set_leverage(symbol, leverage, margin_mode)

    current_price = float(signal['entry_price'])
    min_amount    = exchange.fetch_min_amount(symbol)

    # Pre-compute SL for position sizing
    sl_price_est, _ = compute_sl_tp(current_price, side, sl_pct, tp_rr)
    contracts       = compute_contracts(balance, current_price, sl_price_est, min_amount, risk_pct)

    # Margin cap
    max_by_margin = (balance * leverage) / current_price * 0.99
    if contracts > max_by_margin:
        logger.warning(f"Kontrakte {contracts:.4f} > Margin-Cap {max_by_margin:.4f} — reduziere")
        contracts = max_by_margin

    # Notional check
    if contracts * current_price < MIN_NOTIONAL_USDT:
        logger.warning(f"Notional zu klein ({contracts * current_price:.2f} USDT). Kein Trade.")
        return False

    logger.info(f"Entry: {side.upper()} {contracts:.4f} {symbol} | "
                f"Hebel: {leverage}x | Kapital: {balance:.2f} USDT | Risiko: {risk_pct}%")

    entry_side = 'buy' if side == 'long' else 'sell'
    try:
        order = exchange.place_market_order(symbol, entry_side, contracts,
                                            margin_mode=margin_mode)
    except Exception as e:
        logger.error(f"Entry fehlgeschlagen: {e}")
        return False

    # Actual fill price
    entry_price = float(order.get('average') or order.get('price') or current_price)
    if entry_price <= 0:
        entry_price = current_price
    filled = float(order.get('filled') or order.get('amount') or contracts)
    if filled <= 0:
        filled = contracts

    # Final SL/TP from actual fill price
    sl_price, tp_price = compute_sl_tp(entry_price, side, sl_pct, tp_rr)
    sl_dist_pct = abs(entry_price - sl_price) / entry_price * 100
    tp_dist_pct = abs(tp_price - entry_price) / entry_price * 100

    logger.info(f"Fill: {entry_price:.6f} | SL: {sl_price:.6f} (-{sl_dist_pct:.3f}%) | "
                f"TP: {tp_price:.6f} (+{tp_dist_pct:.3f}%) | R:R 1:{tp_dist_pct/sl_dist_pct:.1f}")

    time.sleep(1.0)

    # SL order
    sl_side = 'sell' if side == 'long' else 'buy'
    try:
        exchange.place_trigger_market_order(symbol, sl_side, filled, sl_price, reduce=True)
        logger.info(f"SL platziert @ {sl_price:.6f}")
    except Exception as e:
        logger.error(f"SL-Platzierung fehlgeschlagen: {e} — schliesse Position!")
        try:
            exchange.close_position(symbol)
        except Exception as ce:
            logger.critical(f"Position konnte nicht geschlossen werden: {ce}")
        return False

    # TP order
    try:
        exchange.place_trigger_market_order(symbol, sl_side, filled, tp_price, reduce=True)
        logger.info(f"TP platziert @ {tp_price:.6f}")
    except Exception as e:
        logger.error(f"TP-Platzierung fehlgeschlagen (nicht kritisch): {e}")

    # Claim state
    move_type = signal.get('move_type', '')
    if not claim_position(symbol, timeframe, side, entry_price,
                          sl_price, tp_price, filled, move_type):
        logger.warning("Race condition: Position bereits belegt — schliesse!")
        try:
            exchange.cancel_all_orders(symbol)
            exchange.close_position(symbol)
        except Exception as ce:
            logger.error(f"Fehler beim Schliessen: {ce}")
        return False

    # Telegram
    emoji = "🟢" if side == 'long' else "🔴"
    risk_usdt = balance * risk_pct / 100.0
    score_str = f"{signal.get('score', 0):.1f}" if signal.get('score') else '?'
    msg = (
        f"🚀 probebot SIGNAL: {symbol} ({timeframe})\n"
        f"{'─' * 32}\n"
        f"{emoji} {side.upper()} | {move_type}\n"
        f"📊 Signal-Score: {score_str}\n"
        f"💰 Entry:   ${entry_price:.6f}\n"
        f"🛑 SL:      ${sl_price:.6f} (-{sl_dist_pct:.2f}%)\n"
        f"🎯 TP:      ${tp_price:.6f} (+{tp_dist_pct:.2f}%)\n"
        f"📐 R:R:     1:{tp_dist_pct/sl_dist_pct:.1f}\n"
        f"⚙️ Hebel:   {leverage}x\n"
        f"🛡️ Risiko:  {risk_pct:.1f}% ({risk_usdt:.2f} USDT)\n"
        f"📦 Kontr.:  {filled:.4f}"
    )
    send_message(telegram_cfg.get('bot_token'), telegram_cfg.get('chat_id'), msg)
    logger.info("Trade platziert.")
    return True


# ── Check Position ────────────────────────────────────────────────────────────

def check_position_status(exchange, symbol: str, timeframe: str,
                          telegram_cfg: dict, logger: logging.Logger):
    """
    Prueft ob die Exchange-Position noch offen ist.
    Falls nicht: cleanup + Telegram-Benachrichtigung + State leeren.
    """
    from probebot.utils.telegram import send_message

    pos       = read_position(symbol, timeframe)
    positions = exchange.fetch_open_positions(symbol)

    if positions:
        if pos:
            p       = positions[0]
            unr_pnl = p.get('unrealizedPnl', 0.0)
            logger.info(
                f"Position offen: {p.get('side','?').upper()} {symbol} | "
                f"Entry: {pos.get('entry_price','?')} | Unrealized PnL: {unr_pnl:.2f} USDT"
            )
        return

    # Position auf Exchange geschlossen (SL/TP getroffen)
    _housekeeper(exchange, symbol, logger)

    if pos is None:
        return

    logger.info(f"Position geschlossen: {symbol} ({timeframe})")

    side_str = pos.get('side', '?')
    emoji    = "🟢" if side_str == 'long' else "🔴"
    msg = (
        f"✅ probebot TRADE GESCHLOSSEN\n"
        f"{'─' * 32}\n"
        f"{emoji} {side_str.upper()} | {symbol} ({timeframe})\n"
        f"🎯 Move-Typ: {pos.get('move_type', '?')}\n"
        f"💰 Entry:  ${pos.get('entry_price', '?')}\n"
        f"🛑 SL:     ${pos.get('sl_price', '?')}\n"
        f"🎯 TP:     ${pos.get('tp_price', '?')}\n"
        f"🕐 Seit:   {pos.get('active_since', '?')}\n"
        f"{'─' * 32}\n"
        f"⏳ Warte auf naechstes Signal..."
    )
    send_message(telegram_cfg.get('bot_token'), telegram_cfg.get('chat_id'), msg)
    clear_position(symbol, timeframe)
    set_candle_cooldown(symbol, timeframe)


def _housekeeper(exchange, symbol: str, logger: logging.Logger):
    try:
        exchange.cancel_all_orders(symbol)
        time.sleep(1)
        positions = exchange.fetch_open_positions(symbol)
        if positions:
            pos_info   = positions[0]
            close_side = 'sell' if pos_info['side'] == 'long' else 'buy'
            logger.warning(f"Housekeeper: verwaiste Position — schliesse {pos_info['side']}")
            exchange.place_market_order(symbol, close_side,
                                        float(pos_info.get('contracts', 0)), reduce=True)
    except Exception as e:
        logger.error(f"Housekeeper-Fehler: {e}")
