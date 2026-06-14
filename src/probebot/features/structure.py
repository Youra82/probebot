"""Market structure: swing highs/lows, supply/demand zones, fair value gaps, order blocks."""
import numpy as np
import pandas as pd


def add_all_structure(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Swing highs and lows (pivot detection)
    df['swing_high'] = _swing_high(df['high'], left=3, right=3)
    df['swing_low'] = _swing_low(df['low'], left=3, right=3)

    # HH / HL / LL / LH structure
    market_struct = _market_structure(df)
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

    # Order blocks (last opposing candle before impulse)
    ob = _order_blocks(df)
    df['bull_ob'] = ob['bull'].astype(float)
    df['bear_ob'] = ob['bear'].astype(float)

    # Breakout of consolidation (N-bar range)
    for n in [10, 20]:
        breakout = _range_breakout(df, n)
        df[f'breakout_up_{n}'] = breakout['up'].astype(float)
        df[f'breakout_down_{n}'] = breakout['down'].astype(float)

    # Gap detection (open vs prior close)
    df['gap_up'] = (df['open'] > df['close'].shift(1) * 1.002).astype(float)
    df['gap_down'] = (df['open'] < df['close'].shift(1) * 0.998).astype(float)
    df['gap_pct'] = (df['open'] - df['close'].shift(1)) / (df['close'].shift(1) + 1e-10)

    # Price vs VWAP approximation (rolling)
    df['vwap_20'] = _rolling_vwap(df, 20)
    df['price_vs_vwap'] = (df['close'] - df['vwap_20']) / (df['vwap_20'] + 1e-10)

    # Range compression: is current range < N-bar avg?
    for n in [10, 20]:
        avg_range = (df['high'] - df['low']).rolling(n).mean()
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

    # Price position in 50-bar range
    high_50 = df['high'].rolling(50).max()
    low_50 = df['low'].rolling(50).min()
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

def _market_structure(df: pd.DataFrame) -> dict:
    close = df['close'].values
    n = len(close)

    hh = np.zeros(n, dtype=bool)
    hl = np.zeros(n, dtype=bool)
    ll = np.zeros(n, dtype=bool)
    lh = np.zeros(n, dtype=bool)

    for i in range(10, n):
        window = close[i - 10:i]
        prev_high = np.max(window[:-5]) if len(window) >= 5 else np.max(window)
        prev_low = np.min(window[:-5]) if len(window) >= 5 else np.min(window)
        curr = close[i]
        p_high = np.max(close[max(0, i - 5):i])
        p_low = np.min(close[max(0, i - 5):i])

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

def _order_blocks(df: pd.DataFrame, impulse_threshold: float = 0.015) -> dict:
    """
    Detect order blocks: last opposing candle before a strong impulse move.
    Bull OB: last bearish candle before strong bullish impulse.
    Bear OB: last bullish candle before strong bearish impulse.
    """
    n = len(df)
    bull_ob = np.zeros(n, dtype=bool)
    bear_ob = np.zeros(n, dtype=bool)
    close = df['close'].values
    open_ = df['open'].values

    for i in range(3, n):
        move = (close[i] - close[i - 1]) / (close[i - 1] + 1e-10)
        if move > impulse_threshold:
            # Bullish impulse - last bearish candle is bull OB
            for j in range(i - 1, max(i - 5, 0), -1):
                if close[j] < open_[j]:  # bearish candle
                    bull_ob[j] = True
                    break
        elif move < -impulse_threshold:
            # Bearish impulse - last bullish candle is bear OB
            for j in range(i - 1, max(i - 5, 0), -1):
                if close[j] > open_[j]:  # bullish candle
                    bear_ob[j] = True
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
