# src/probebot/strategy/signal_logic.py
"""
Strategy-specific trade parameter computation for probebot.

Each strategy type gets its own SL price, TP price and exit method,
derived from the live feature values of the last closed candle.

Strategies:
  BREAKOUT   — price breaks out of N-bar consolidation range
  MOMENTUM   — impulse / acceleration move
  ORDERFLOW  — volume/CVD driven entry, VWAP/POC as target
  MEAN_REV   — overextension reversal, mean (EMA20) as TP target
  COMPLEXITY — entropy/Hurst regime shift
  SQUEEZE    — Bollinger/Keltner squeeze release
  HYBRID     — fall back to config sl_pct + tp_rr

All functions accept a `last_row` dict (feature values of the last closed candle)
and return a TradeParams named-tuple.

Fallback: if a required feature is missing or produces an invalid price,
the function falls back to the config-based fixed sl_pct / tp_rr.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeParams:
    sl_price:                 float
    tp_price:                 Optional[float]  # None → use trailing stop
    use_trailing:             bool
    trailing_activation_price: Optional[float]  # price at which trailing activates
    trailing_pct:             float            # callback % (e.g. 0.8 = 0.8%)
    sl_source:                str
    tp_source:                str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fallback(entry: float, side: str, sl_pct: float, tp_rr: float) -> TradeParams:
    """Config-based fixed SL/TP (used when features are unavailable)."""
    sl_dist = entry * sl_pct / 100.0
    if side == 'long':
        sl = entry - sl_dist
        tp = entry + sl_dist * tp_rr
    else:
        sl = entry + sl_dist
        tp = entry - sl_dist * tp_rr
    return TradeParams(
        sl_price=sl, tp_price=tp,
        use_trailing=False,
        trailing_activation_price=None, trailing_pct=0.0,
        sl_source=f'config sl_pct={sl_pct}%',
        tp_source=f'config tp_rr={tp_rr}',
    )


def _valid_sl(sl: float, entry: float, side: str, min_pct: float = 0.1) -> bool:
    """Check that SL is on the correct side and at least min_pct away from entry."""
    dist_pct = abs(entry - sl) / entry * 100
    if dist_pct < min_pct:
        return False
    return (side == 'long' and sl < entry) or (side == 'short' and sl > entry)


def _valid_tp(tp: float, entry: float, side: str, min_pct: float = 0.1) -> bool:
    dist_pct = abs(tp - entry) / entry * 100
    if dist_pct < min_pct:
        return False
    return (side == 'long' and tp > entry) or (side == 'short' and tp < entry)


def _get(row: dict, key: str) -> Optional[float]:
    """Safe feature lookup — returns None if missing or NaN."""
    val = row.get(key)
    if val is None:
        return None
    try:
        v = float(val)
        return None if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return None


# ── Per-strategy logic ────────────────────────────────────────────────────────

def _breakout(entry: float, side: str, row: dict,
              sl_pct: float, tp_rr: float) -> TradeParams:
    """
    BREAKOUT / BREAKDOWN
    SL: behind the consolidation (swing_low for LONG, swing_high for SHORT)
    TP: trailing stop — breakouts can run far, let the winner ride
    Trailing activates at 1.5× SL distance, callback 0.8%
    """
    swing_low  = _get(row, 'swing_low')
    swing_high = _get(row, 'swing_high')

    if side == 'long':
        # SL: just below the consolidation support
        sl_candidate = swing_low * 0.998 if swing_low else None
        sl = sl_candidate if sl_candidate and _valid_sl(sl_candidate, entry, 'long') \
             else entry * (1 - sl_pct * 1.2 / 100)
        sl_src = f'swing_low={swing_low:.4f}' if sl_candidate and _valid_sl(sl_candidate, entry, 'long') \
                 else f'config sl_pct×1.2'
    else:
        sl_candidate = swing_high * 1.002 if swing_high else None
        sl = sl_candidate if sl_candidate and _valid_sl(sl_candidate, entry, 'short') \
             else entry * (1 + sl_pct * 1.2 / 100)
        sl_src = f'swing_high={swing_high:.4f}' if sl_candidate and _valid_sl(sl_candidate, entry, 'short') \
                 else f'config sl_pct×1.2'

    sl_dist              = abs(entry - sl)
    act_price_long       = entry + sl_dist * 1.5
    act_price_short      = entry - sl_dist * 1.5
    activation           = act_price_long if side == 'long' else act_price_short

    return TradeParams(
        sl_price=sl, tp_price=None,
        use_trailing=True,
        trailing_activation_price=activation,
        trailing_pct=0.8,
        sl_source=sl_src,
        tp_source='trailing_stop 0.8% after 1.5×SL',
    )


def _momentum(entry: float, side: str, row: dict,
              sl_pct: float, tp_rr: float) -> TradeParams:
    """
    MOMENTUM — IMPULSE / ACCELERATION
    SL: 1.5× ATR behind entry (gives the impulse room to breathe)
    TP: 2.5× ATR (ATR-based fixed RR)
    """
    atr = _get(row, 'atr_14')

    if atr and atr > 0:
        sl_dist = 1.5 * atr
        tp_dist = 2.5 * atr
        if side == 'long':
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist
        sl_src = f'1.5×ATR14={atr:.4f}'
        tp_src = f'2.5×ATR14={atr:.4f}'

        if _valid_sl(sl, entry, side) and _valid_tp(tp, entry, side):
            return TradeParams(
                sl_price=sl, tp_price=tp,
                use_trailing=False,
                trailing_activation_price=None, trailing_pct=0.0,
                sl_source=sl_src, tp_source=tp_src,
            )

    return _fallback(entry, side, sl_pct, tp_rr)


def _orderflow(entry: float, side: str, row: dict,
               sl_pct: float, tp_rr: float) -> TradeParams:
    """
    ORDERFLOW — CVD/volume driven
    SL: 1.5× ATR
    TP: nearest VWAP or Volume POC in range 1.5–4× SL distance (liquidity magnet)
        Fallback: 2.0× SL
    """
    atr       = _get(row, 'atr_14')
    vwap      = _get(row, 'vwap_20')
    vol_poc   = _get(row, 'vol_poc_20')

    if atr and atr > 0:
        sl_dist = 1.5 * atr
        if side == 'long':
            sl = entry - sl_dist
        else:
            sl = entry + sl_dist
        sl_src = f'1.5×ATR14={atr:.4f}'
    else:
        sl_dist = entry * sl_pct / 100.0
        sl = entry - sl_dist if side == 'long' else entry + sl_dist
        sl_src = f'config sl_pct={sl_pct}%'

    if not _valid_sl(sl, entry, side):
        return _fallback(entry, side, sl_pct, tp_rr)

    # Find best liquidity target
    candidates = []
    for target, name in [(vwap, 'vwap_20'), (vol_poc, 'vol_poc_20')]:
        if target is None:
            continue
        if not _valid_tp(target, entry, side):
            continue
        dist = abs(target - entry)
        ratio = dist / sl_dist if sl_dist > 0 else 0
        if 1.5 <= ratio <= 4.0:
            candidates.append((ratio, target, name))

    if candidates:
        candidates.sort(key=lambda x: x[0])  # pick closest valid target
        _, tp, tp_name = candidates[0]
        return TradeParams(
            sl_price=sl, tp_price=tp,
            use_trailing=False,
            trailing_activation_price=None, trailing_pct=0.0,
            sl_source=sl_src, tp_source=f'{tp_name}={tp:.4f}',
        )

    # Fallback: 2× SL
    tp = entry + sl_dist * 2.0 if side == 'long' else entry - sl_dist * 2.0
    return TradeParams(
        sl_price=sl, tp_price=tp,
        use_trailing=False,
        trailing_activation_price=None, trailing_pct=0.0,
        sl_source=sl_src, tp_source='2×SL (fallback)',
    )


def _mean_rev(entry: float, side: str, row: dict,
              sl_pct: float, tp_rr: float) -> TradeParams:
    """
    MEAN_REV — REVERSAL
    SL: just beyond the last swing extreme (where the reversal came from)
    TP: EMA20 as the mean target, fallback 1.5× SL
    The mean is where price "wants" to return to.
    """
    swing_low  = _get(row, 'swing_low')
    swing_high = _get(row, 'swing_high')
    ema_20     = _get(row, 'ema_20')

    if side == 'long':
        # Price bounced up from low → SL below swing_low
        sl_candidate = swing_low * 0.997 if swing_low else None
        sl = sl_candidate if sl_candidate and _valid_sl(sl_candidate, entry, 'long') \
             else entry * (1 - sl_pct / 100)
        sl_src = f'swing_low×0.997={swing_low:.4f}' if sl_candidate and _valid_sl(sl_candidate, entry, 'long') \
                 else 'config sl_pct'
    else:
        # Price reversed down from high → SL above swing_high
        sl_candidate = swing_high * 1.003 if swing_high else None
        sl = sl_candidate if sl_candidate and _valid_sl(sl_candidate, entry, 'short') \
             else entry * (1 + sl_pct / 100)
        sl_src = f'swing_high×1.003={swing_high:.4f}' if sl_candidate and _valid_sl(sl_candidate, entry, 'short') \
                 else 'config sl_pct'

    if not _valid_sl(sl, entry, side):
        return _fallback(entry, side, sl_pct, tp_rr)

    sl_dist = abs(entry - sl)

    # TP: EMA20 as mean target
    tp = None
    tp_src = ''
    if ema_20 and _valid_tp(ema_20, entry, side):
        tp_dist = abs(ema_20 - entry)
        if tp_dist >= sl_dist * 1.2:   # at least 1.2:1 RR
            tp = ema_20
            tp_src = f'ema_20={ema_20:.4f}'

    if tp is None:
        tp = entry + sl_dist * 1.5 if side == 'long' else entry - sl_dist * 1.5
        tp_src = '1.5×SL (fallback)'

    return TradeParams(
        sl_price=sl, tp_price=tp,
        use_trailing=False,
        trailing_activation_price=None, trailing_pct=0.0,
        sl_source=sl_src, tp_source=tp_src,
    )


def _complexity(entry: float, side: str, row: dict,
                sl_pct: float, tp_rr: float) -> TradeParams:
    """
    COMPLEXITY — entropy/Hurst regime shift
    SL: 2× ATR (wider — complexity signals are noisier)
    TP: 1.5× SL (modest target — signal doesn't imply directional trend)
    """
    atr = _get(row, 'atr_14')

    if atr and atr > 0:
        sl_dist = 2.0 * atr
        tp_dist = sl_dist * 1.5
        if side == 'long':
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist
        if _valid_sl(sl, entry, side) and _valid_tp(tp, entry, side):
            return TradeParams(
                sl_price=sl, tp_price=tp,
                use_trailing=False,
                trailing_activation_price=None, trailing_pct=0.0,
                sl_source=f'2×ATR14={atr:.4f}',
                tp_source=f'1.5×SL',
            )

    return _fallback(entry, side, sl_pct, min(tp_rr, 1.5))


def _squeeze(entry: float, side: str, row: dict,
             sl_pct: float, tp_rr: float) -> TradeParams:
    """
    SQUEEZE — Bollinger/Keltner squeeze release
    SL: half the BB-width behind entry (inside the squeeze zone)
    TP: 1.5× BB-width projection (squeeze expansion target)
    Fallback: trailing stop 1.0% after 1× SL (squeeze releases are explosive)
    """
    bb_width = _get(row, 'bb_width')
    atr      = _get(row, 'atr_14')

    if bb_width and bb_width > 0 and entry > 0:
        # bb_width is in price terms (upper - lower)
        sl_dist = bb_width * 0.5
        tp_dist = bb_width * 1.5
        if side == 'long':
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        if _valid_sl(sl, entry, side) and _valid_tp(tp, entry, side):
            # Also add trailing stop as backup exit method
            return TradeParams(
                sl_price=sl, tp_price=None,
                use_trailing=True,
                trailing_activation_price=tp,   # activate trailing at BB-width target
                trailing_pct=1.0,
                sl_source=f'0.5×bb_width={bb_width:.4f}',
                tp_source=f'trailing 1% after 1.5×bb_width={tp:.4f}',
            )

    # Fallback: ATR-based with trailing
    if atr and atr > 0:
        sl_dist = 1.5 * atr
        act_dist = sl_dist * 1.5
        if side == 'long':
            sl = entry - sl_dist
            act = entry + act_dist
        else:
            sl = entry + sl_dist
            act = entry - act_dist
        if _valid_sl(sl, entry, side):
            return TradeParams(
                sl_price=sl, tp_price=None,
                use_trailing=True,
                trailing_activation_price=act,
                trailing_pct=1.0,
                sl_source=f'1.5×ATR14={atr:.4f}',
                tp_source='trailing 1% (ATR fallback)',
            )

    return _fallback(entry, side, sl_pct, max(tp_rr, 2.5))


# ── Public API ────────────────────────────────────────────────────────────────

_STRATEGY_FN = {
    'BREAKOUT':   _breakout,
    'MOMENTUM':   _momentum,
    'ORDERFLOW':  _orderflow,
    'MEAN_REV':   _mean_rev,
    'COMPLEXITY': _complexity,
    'SQUEEZE':    _squeeze,
}


def compute_trade_params(
    strategy:    str,
    move_type:   str,
    last_row:    dict,
    entry_price: float,
    side:        str,
    config_risk: dict,
) -> TradeParams:
    """
    Compute SL/TP prices for a given strategy and live feature values.

    Args:
        strategy:    selected strategy name (BREAKOUT/MOMENTUM/ORDERFLOW/…)
        move_type:   specific movement type (BREAKOUT_UP, IMPULSE_DOWN, …)
        last_row:    feature dict of the last closed candle
        entry_price: estimated entry price (last close)
        side:        'long' or 'short'
        config_risk: config['risk'] dict (sl_pct, tp_rr as fallback)

    Returns:
        TradeParams with sl_price, tp_price, use_trailing, etc.
    """
    sl_pct = float(config_risk.get('sl_pct', 1.5))
    tp_rr  = float(config_risk.get('tp_rr', 2.0))

    # Strategy may be stored as HYBRID or have no specific handler
    fn = _STRATEGY_FN.get(strategy.upper() if strategy else '')

    # If no specific handler: try to infer from move_type
    if fn is None:
        upper = (move_type or '').upper()
        if 'BREAKOUT' in upper or 'BREAKDOWN' in upper:
            fn = _breakout
        elif 'IMPULSE' in upper or 'ACCELERATION' in upper:
            fn = _momentum
        elif 'REVERSAL' in upper:
            fn = _mean_rev
        elif 'SQUEEZE' in upper:
            fn = _squeeze
        else:
            return _fallback(entry_price, side, sl_pct, tp_rr)

    return fn(entry_price, side, last_row, sl_pct, tp_rr)
