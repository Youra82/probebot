"""Volume analysis: OBV, CVD approximation, volume profile, volume entropy."""
import numpy as np
import pandas as pd


def add_all_volume(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # OBV (On-Balance Volume)
    df['obv'] = _obv(df)
    df['obv_slope'] = df['obv'].diff(5)
    df['obv_z'] = (df['obv'] - df['obv'].rolling(20).mean()) / (df['obv'].rolling(20).std() + 1e-10)

    # Volume ratio vs moving average
    vol_ma = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / (vol_ma + 1e-10)
    df['volume_z'] = (df['volume'] - vol_ma) / (df['volume'].rolling(20).std() + 1e-10)

    # Volume surge detection
    df['volume_surge'] = (df['volume_ratio'] > 2.0).astype(float)
    df['volume_dry_up'] = (df['volume_ratio'] < 0.5).astype(float)

    # Consecutive declining volume
    df['vol_declining_3'] = (
        (df['volume'] < df['volume'].shift(1)) &
        (df['volume'].shift(1) < df['volume'].shift(2)) &
        (df['volume'].shift(2) < df['volume'].shift(3))
    ).astype(float)

    # CVD approximation (Cumulative Volume Delta)
    # Positive volume = buy (close > open), negative = sell
    delta = df['volume'] * np.sign(df['close'] - df['open'])
    df['cvd'] = delta.cumsum()
    df['cvd_slope'] = df['cvd'].diff(5)
    df['cvd_divergence'] = _divergence(df['close'], df['cvd'], 5)

    # Volume-weighted price move (is volume confirming direction?)
    df['vol_confirm'] = np.sign(df['close'] - df['open']) * df['volume_ratio']

    # Volume entropy (how randomly distributed is volume across sessions?)
    df['volume_entropy'] = _rolling_entropy(df['volume'], 20)

    # Volume profile: price level with most volume in last N candles
    for n in [20, 50]:
        df[f'vol_poc_{n}'] = _rolling_poc(df, n)  # Point of Control
        df[f'price_vs_poc_{n}'] = (df['close'] - df[f'vol_poc_{n}']) / (df[f'vol_poc_{n}'] + 1e-10)

    # MFI divergence
    if 'mfi_14' in df.columns:
        df['mfi_divergence'] = _divergence(df['close'], df['mfi_14'], 5)

    # Buying pressure vs selling pressure (high-low midpoint method)
    mid = (df['high'] + df['low']) / 2
    df['buy_pressure'] = df['volume'] * ((df['close'] - df['low']) / (df['high'] - df['low'] + 1e-10))
    df['sell_pressure'] = df['volume'] * ((df['high'] - df['close']) / (df['high'] - df['low'] + 1e-10))
    df['pressure_ratio'] = df['buy_pressure'] / (df['sell_pressure'] + 1e-10)
    df['cum_pressure_slope'] = (df['buy_pressure'] - df['sell_pressure']).rolling(10).sum()

    # Large candle + large volume = institutional candle
    body = (df['close'] - df['open']).abs()
    total = df['high'] - df['low']
    df['institutional_candle'] = (
        (df['volume_ratio'] > 2.0) &
        (body / (total + 1e-10) > 0.6)
    ).astype(float)

    # Volume slope (trend)
    df['vol_slope_5'] = df['volume'].diff(5)
    df['vol_slope_10'] = df['volume'].diff(10)

    return df


def _obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df['close'] - df['close'].shift(1))
    vol_signed = df['volume'] * direction
    return vol_signed.cumsum()


def _rolling_entropy(series: pd.Series, window: int, bins: int = 8) -> pd.Series:
    result = pd.Series(np.nan, index=series.index)
    arr = series.values
    for i in range(window, len(arr)):
        chunk = arr[i - window:i]
        chunk = chunk[~np.isnan(chunk)]
        if len(chunk) < window // 2:
            continue
        hist, _ = np.histogram(chunk, bins=bins, density=True)
        p = hist[hist > 0]
        p = p / p.sum()
        result.iloc[i] = float(-np.sum(p * np.log(p + 1e-12)))
    return result


def _divergence(price: pd.Series, indicator: pd.Series, window: int) -> pd.Series:
    price_slope = price.diff(window)
    ind_slope = indicator.diff(window)
    return np.sign(ind_slope) - np.sign(price_slope)


def _rolling_poc(df: pd.DataFrame, window: int, bins: int = 20) -> pd.Series:
    """Point of Control: price level with highest volume in rolling window."""
    result = pd.Series(np.nan, index=df.index)
    close = df['close'].values
    volume = df['volume'].values
    low = df['low'].values
    high = df['high'].values

    for i in range(window, len(df)):
        w_close = close[i - window:i]
        w_vol = volume[i - window:i]
        w_low = low[i - window:i]
        w_high = high[i - window:i]

        price_min = np.min(w_low)
        price_max = np.max(w_high)
        if price_max <= price_min:
            continue

        bin_edges = np.linspace(price_min, price_max, bins + 1)
        vol_at_level = np.zeros(bins)
        for j in range(len(w_close)):
            idx = min(int((w_close[j] - price_min) / (price_max - price_min) * bins), bins - 1)
            vol_at_level[idx] += w_vol[j]

        poc_bin = np.argmax(vol_at_level)
        poc_price = (bin_edges[poc_bin] + bin_edges[poc_bin + 1]) / 2
        result.iloc[i] = poc_price

    return result
