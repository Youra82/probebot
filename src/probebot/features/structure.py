"""Market structure: swing highs/lows, supply/demand zones, fair value gaps, order blocks."""
import numpy as np
import pandas as pd

from .scaling import sp


def add_all_structure(df: pd.DataFrame, scale: float = 1.0) -> pd.DataFrame:
    """scale: siehe technical.add_all_technical — skaliert alle Fenster."""
    df = df.copy()

    # Swing highs and lows (pivot detection)
    _sw = sp(3, scale, minimum=1)
    df['swing_high'] = _swing_high(df['high'], left=_sw, right=_sw)
    df['swing_low'] = _swing_low(df['low'], left=_sw, right=_sw)

    # HH / HL / LL / LH structure
    market_struct = _market_structure(df, window=sp(10, scale))
    df['struct_hh'] = market_struct['hh'].astype(float)
    df['struct_hl'] = market_struct['hl'].astype(float)
    df['struct_ll'] = market_struct['ll'].astype(float)
    df['struct_lh'] = market_struct['lh'].astype(float)
    df['struct_score'] = market_struct['score']  # +2=bullish, -2=bearish, 0=neutral

    # Distance to nearest swing high / low
    df['dist_to_nearest_resistance'] = _dist_to_nearest_level(df, df['swing_high'], direction='above')
    df['dist_to_nearest_support'] = _dist_to_nearest_level(df, df['swing_low'], direction='below')

    # Fair Value Gaps (FVG) — imbalance zones
    fvg = _fair_value_gaps(df)
    df['fvg_bull'] = fvg['bull'].astype(float)  # bullish FVG (candle 1 low > candle 3 high)
    df['fvg_bear'] = fvg['bear'].astype(float)  # bearish FVG
    df['in_fvg'] = (fvg['bull'] | fvg['bear']).astype(float)

    # Order blocks (retest of a previously confirmed zone -- causal, see
    # _order_blocks() docstring)
    ob = _order_blocks(df, lookback=sp(5, scale), max_zone_age=sp(50, scale))
    df['bull_ob'] = ob['bull'].astype(float)
    df['bear_ob'] = ob['bear'].astype(float)

    # Breakout of consolidation (N-bar range)
    for n in [10, 20]:
        breakout = _range_breakout(df, sp(n, scale))
        df[f'breakout_up_{n}'] = breakout['up'].astype(float)
        df[f'breakout_down_{n}'] = breakout['down'].astype(float)

    # Gap detection (open vs prior close)
    df['gap_up'] = (df['open'] > df['close'].shift(1) * 1.002).astype(float)
    df['gap_down'] = (df['open'] < df['close'].shift(1) * 0.998).astype(float)
    df['gap_pct'] = (df['open'] - df['close'].shift(1)) / (df['close'].shift(1) + 1e-10)

    # Price vs VWAP approximation (rolling)
    df['vwap_20'] = _rolling_vwap(df, sp(20, scale))
    df['price_vs_vwap'] = (df['close'] - df['vwap_20']) / (df['vwap_20'] + 1e-10)

    # Range compression: is current range < N-bar avg?
    for n in [10, 20]:
        ns = sp(n, scale)
        avg_range = (df['high'] - df['low']).rolling(ns).mean()
        curr_range = df['high'] - df['low']
        df[f'range_compression_{n}'] = (curr_range / (avg_range + 1e-10))

    # Inside bar / outside bar
    df['inside_bar'] = (
        (df['high'] < df['high'].shift(1)) &
        (df['low'] > df['low'].shift(1))
    ).astype(float)
    df['outside_bar'] = (
        (df['high'] > df['high'].shift(1)) &
        (df['low'] < df['low'].shift(1))
    ).astype(float)

    # Pin bar: long wick, tiny body
    body = (df['close'] - df['open']).abs()
    total = df['high'] - df['low']
    df['pin_bar_bull'] = (
        (df['lower_wick'] > 2 * body) &
        (df['lower_wick'] > df['upper_wick'] * 2) &
        (total > 0)
    ).astype(float) if 'lower_wick' in df.columns else 0.0
    df['pin_bar_bear'] = (
        (df['upper_wick'] > 2 * body) &
        (df['upper_wick'] > df['lower_wick'] * 2) &
        (total > 0)
    ).astype(float) if 'upper_wick' in df.columns else 0.0

    # Engulfing candles
    df['bull_engulf'] = _bull_engulf(df)
    df['bear_engulf'] = _bear_engulf(df)

    # Price position in N-bar range
    _rp = sp(50, scale)
    high_50 = df['high'].rolling(_rp).max()
    low_50 = df['low'].rolling(_rp).min()
    df['range_position_50'] = (df['close'] - low_50) / (high_50 - low_50 + 1e-10)

    return df


# ─── Swing highs / lows ───────────────────────────────────────────────────────

def _swing_high(series: pd.Series, left: int = 3, right: int = 3) -> pd.Series:
    result = pd.Series(False, index=series.index)
    arr = series.values
    for i in range(left, len(arr) - right):
        if arr[i] == max(arr[i - left:i + right + 1]):
            result.iloc[i] = True
    return result


def _swing_low(series: pd.Series, left: int = 3, right: int = 3) -> pd.Series:
    result = pd.Series(False, index=series.index)
    arr = series.values
    for i in range(left, len(arr) - right):
        if arr[i] == min(arr[i - left:i + right + 1]):
            result.iloc[i] = True
    return result


# ─── Market Structure (HH/HL/LH/LL) ─────────────────────────────────────────

def _market_structure(df: pd.DataFrame, window: int = 10) -> dict:
    close = df['close'].values
    n = len(close)
    half = max(1, window // 2)

    hh = np.zeros(n, dtype=bool)
    hl = np.zeros(n, dtype=bool)
    ll = np.zeros(n, dtype=bool)
    lh = np.zeros(n, dtype=bool)

    for i in range(window, n):
        w = close[i - window:i]
        prev_high = np.max(w[:-half]) if len(w) >= half else np.max(w)
        prev_low = np.min(w[:-half]) if len(w) >= half else np.min(w)
        curr = close[i]
        p_high = np.max(close[max(0, i - half):i])
        p_low = np.min(close[max(0, i - half):i])

        if p_high > prev_high:
            hh[i] = True
        if p_low > prev_low:
            hl[i] = True
        if p_low < prev_low:
            ll[i] = True
        if p_high < prev_high:
            lh[i] = True

    score = (hh.astype(int) + hl.astype(int)) - (ll.astype(int) + lh.astype(int))
    return {
        'hh': pd.Series(hh, index=df.index),
        'hl': pd.Series(hl, index=df.index),
        'll': pd.Series(ll, index=df.index),
        'lh': pd.Series(lh, index=df.index),
        'score': pd.Series(score, index=df.index),
    }


# ─── Distance to nearest swing level ─────────────────────────────────────────

def _dist_to_nearest_level(df: pd.DataFrame, swing: pd.Series, direction: str) -> pd.Series:
    """Percentage distance from close to nearest recent swing level."""
    close = df['close']
    result = pd.Series(np.nan, index=df.index)
    swing_prices = close[swing].values
    swing_indices = np.where(swing.values)[0]

    for i in range(len(df)):
        past = swing_indices[swing_indices < i]
        if len(past) == 0:
            continue
        recent_levels = close.iloc[past[-5:]].values  # last 5 swings
        curr = close.iloc[i]
        if direction == 'above':
            above = recent_levels[recent_levels > curr]
            if len(above) > 0:
                result.iloc[i] = (min(above) - curr) / (curr + 1e-10)
        else:
            below = recent_levels[recent_levels < curr]
            if len(below) > 0:
                result.iloc[i] = (curr - max(below)) / (curr + 1e-10)
    return result


# ─── Fair Value Gaps ──────────────────────────────────────────────────────────

def _fair_value_gaps(df: pd.DataFrame) -> dict:
    n = len(df)
    bull = np.zeros(n, dtype=bool)
    bear = np.zeros(n, dtype=bool)

    for i in range(2, n):
        # Bullish FVG: candle[i-2].high < candle[i].low (gap left unfilled)
        if df['high'].iloc[i - 2] < df['low'].iloc[i]:
            bull[i] = True
        # Bearish FVG: candle[i-2].low > candle[i].high
        if df['low'].iloc[i - 2] > df['high'].iloc[i]:
            bear[i] = True

    return {
        'bull': pd.Series(bull, index=df.index),
        'bear': pd.Series(bear, index=df.index),
    }


# ─── Order Blocks ─────────────────────────────────────────────────────────────

def _order_blocks(df: pd.DataFrame, impulse_threshold: float = 0.015,
                  lookback: int = 5, max_zone_age: int = 50,
                  max_active_zones: int = 3) -> dict:
    """
    Order-block RETEST detection — causally valid, unlike a naive "is this
    candle currently an order block" flag (which needs `lookback` FUTURE
    candles to confirm and is therefore always False on the most recently
    closed live candle; see NON_CAUSAL_FEATURES in features/engine.py for
    the full writeup of why that version was removed).

    A bull/bear order block (last opposing candle before a strong impulse)
    only becomes a KNOWN zone once the impulse candle `i` itself closes —
    that confirmation step uses only candles <= i, so it's causal. What
    was NOT causal was retroactively flagging the origin candle `j` (which
    lies before `i`) as "being" the order block; a real trader can't act on
    candle `j` until `i` has already happened.

    So instead: track confirmed zones going forward, and flag bull_ob/
    bear_ob True on a LATER candle `t` (t > confirmation index) whose
    high/low range overlaps a still-active zone -- i.e. "price is right
    now retesting a previously confirmed order block", which is the actual
    real-time-tradeable form of this concept (entries are taken on the
    retest/pullback, not on the impulse candle itself).

    First version of this (2026-08-14, same day) kept every zone alive for
    up to 100 bars with no cap on how many could stack up -- on real 30m
    crypto data that meant 40+ simultaneously active zones blanketing
    almost the whole recent price range, so "retest" fired on ~70% of
    candles. A signal that common carries ~no information (nothing
    validated as an edge afterwards) -- not proof there's no edge, proof
    the feature was too blunt to find one. Two changes to keep it a rare,
    specific event like a real order-block retest: a zone is MITIGATED
    (removed) the first time price touches it (in real trading the retest
    is the trade -- a zone that's already been tested isn't a fresh signal
    anymore), and at most `max_active_zones` per direction are tracked at
    once (oldest dropped first), instead of letting them accumulate
    unbounded. `max_zone_age` still ages out zones nobody retested in time.
    """
    n = len(df)
    bull_ob = np.zeros(n, dtype=bool)
    bear_ob = np.zeros(n, dtype=bool)
    close = df['close'].values
    open_ = df['open'].values
    high  = df['high'].values
    low   = df['low'].values

    active_bull = []  # list of (confirmed_at_idx, zone_low, zone_high)
    active_bear = []

    for i in range(3, n):
        active_bull = [z for z in active_bull if i - z[0] <= max_zone_age]
        active_bear = [z for z in active_bear if i - z[0] <= max_zone_age]

        # Retest check at candle i, using only zones confirmed on a
        # strictly earlier candle -- causal. First touch mitigates
        # (consumes) the zone so it can't keep firing True forever.
        hi_i, lo_i = high[i], low[i]
        for k, (_, z_lo, z_hi) in enumerate(active_bull):
            if lo_i <= z_hi and hi_i >= z_lo:
                bull_ob[i] = True
                del active_bull[k]
                break
        for k, (_, z_lo, z_hi) in enumerate(active_bear):
            if lo_i <= z_hi and hi_i >= z_lo:
                bear_ob[i] = True
                del active_bear[k]
                break

        # New zone confirmation using candle i's own close -- also causal,
        # only becomes part of `active_*` (and thus checkable) from the
        # NEXT candle onward.
        move = (close[i] - close[i - 1]) / (close[i - 1] + 1e-10)
        if move > impulse_threshold:
            for j in range(i - 1, max(i - lookback, 0), -1):
                if close[j] < open_[j]:  # bearish candle -> bull OB zone
                    active_bull.append((i, low[j], high[j]))
                    if len(active_bull) > max_active_zones:
                        active_bull.pop(0)
                    break
        elif move < -impulse_threshold:
            for j in range(i - 1, max(i - lookback, 0), -1):
                if close[j] > open_[j]:  # bullish candle -> bear OB zone
                    active_bear.append((i, low[j], high[j]))
                    if len(active_bear) > max_active_zones:
                        active_bear.pop(0)
                    break

    return {
        'bull': pd.Series(bull_ob, index=df.index),
        'bear': pd.Series(bear_ob, index=df.index),
    }


# ─── Range Breakout ───────────────────────────────────────────────────────────

def _range_breakout(df: pd.DataFrame, n: int) -> dict:
    close = df['close']
    prev_high = close.rolling(n).max().shift(1)
    prev_low = close.rolling(n).min().shift(1)
    return {
        'up': close > prev_high,
        'down': close < prev_low,
    }


# ─── Rolling VWAP ─────────────────────────────────────────────────────────────

def _rolling_vwap(df: pd.DataFrame, window: int) -> pd.Series:
    tp = (df['high'] + df['low'] + df['close']) / 3
    vol = df['volume']
    return (tp * vol).rolling(window).sum() / (vol.rolling(window).sum() + 1e-10)


# ─── Engulfing Candles ────────────────────────────────────────────────────────

def _bull_engulf(df: pd.DataFrame) -> pd.Series:
    curr_bull = df['close'] > df['open']
    prev_bear = df['close'].shift(1) < df['open'].shift(1)
    engulf = (df['open'] < df['close'].shift(1)) & (df['close'] > df['open'].shift(1))
    return (curr_bull & prev_bear & engulf).astype(float)


def _bear_engulf(df: pd.DataFrame) -> pd.Series:
    curr_bear = df['close'] < df['open']
    prev_bull = df['close'].shift(1) > df['open'].shift(1)
    engulf = (df['open'] > df['close'].shift(1)) & (df['close'] < df['open'].shift(1))
    return (curr_bear & prev_bull & engulf).astype(float)
