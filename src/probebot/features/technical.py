"""All classical technical analysis indicators."""
import pandas as pd
import numpy as np


def add_all_technical(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']
    open_ = df['open']

    # --- Moving Averages ---
    for p in [10, 20, 50, 100, 200]:
        df[f'sma_{p}'] = close.rolling(p).mean()
        df[f'ema_{p}'] = close.ewm(span=p, adjust=False).mean()

    df['wma_14'] = _wma(close, 14)
    df['hma_14'] = _hma(close, 14)

    # Distance from EMA (normalized)
    for p in [9, 21, 50, 200]:
        df[f'ema_{p}'] = close.ewm(span=p, adjust=False).mean()
        df[f'dist_ema_{p}'] = (close - df[f'ema_{p}']) / (df[f'ema_{p}'] + 1e-10)

    # EMA alignment score (how many EMAs are stacked bullish)
    df['ema_alignment'] = (
        (df['ema_9'] > df['ema_21']).astype(int) +
        (df['ema_21'] > df['ema_50']).astype(int) +
        (df['ema_50'] > df['ema_200']).astype(int)
    ) - (
        (df['ema_9'] < df['ema_21']).astype(int) +
        (df['ema_21'] < df['ema_50']).astype(int) +
        (df['ema_50'] < df['ema_200']).astype(int)
    )  # range: -3 (full bear) to +3 (full bull)

    # --- RSI variants ---
    df['rsi_7'] = _rsi(close, 7)
    df['rsi_14'] = _rsi(close, 14)
    df['rsi_21'] = _rsi(close, 21)

    # RSI divergence (price slope vs RSI slope over 5 candles)
    df['rsi_divergence'] = _divergence(close, df['rsi_14'], 5)

    # --- MACD ---
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    df['macd'] = macd_line
    df['macd_signal'] = signal_line
    df['macd_hist'] = macd_line - signal_line
    df['macd_hist_slope'] = df['macd_hist'].diff()

    # --- Bollinger Bands ---
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df['bb_upper'] = bb_mid + 2 * bb_std
    df['bb_lower'] = bb_mid - 2 * bb_std
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / (bb_mid + 1e-10)
    df['bb_position'] = (close - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + 1e-10)
    df['bb_squeeze'] = df['bb_width'] < df['bb_width'].rolling(50).mean() * 0.8

    # --- Keltner Channels ---
    atr14 = _atr(df, 14)
    df['atr_14'] = atr14
    kc_mid = close.ewm(span=20, adjust=False).mean()
    df['kc_upper'] = kc_mid + 1.5 * atr14
    df['kc_lower'] = kc_mid - 1.5 * atr14

    # Squeeze: BB inside KC → energy coiling
    df['kc_squeeze'] = (df['bb_upper'] < df['kc_upper']) & (df['bb_lower'] > df['kc_lower'])

    # --- ATR variants ---
    df['atr_7'] = _atr(df, 7)
    df['atr_14'] = _atr(df, 14)
    df['atr_21'] = _atr(df, 21)
    df['atr_pct'] = df['atr_14'] / (close + 1e-10)
    df['atr_z'] = (df['atr_14'] - df['atr_14'].rolling(50).mean()) / (df['atr_14'].rolling(50).std() + 1e-10)

    # --- ADX / DI ---
    adx_result = _adx(df, 14)
    df['adx'] = adx_result['adx']
    df['di_plus'] = adx_result['di_plus']
    df['di_minus'] = adx_result['di_minus']
    df['di_delta'] = df['di_plus'] - df['di_minus']

    # --- Supertrend ---
    st = _supertrend(df, 10, 3.0)
    df['supertrend'] = st['supertrend']
    df['supertrend_dir'] = st['direction']  # 1=bull, -1=bear

    # --- CCI ---
    df['cci_20'] = _cci(df, 20)

    # --- Williams %R ---
    df['willr_14'] = _willr(df, 14)

    # --- ROC / Momentum ---
    df['roc_5'] = (close / close.shift(5) - 1) * 100
    df['roc_10'] = (close / close.shift(10) - 1) * 100
    df['roc_20'] = (close / close.shift(20) - 1) * 100
    df['momentum_10'] = close - close.shift(10)

    # --- Donchian Channel ---
    df['donchian_high_20'] = high.rolling(20).max()
    df['donchian_low_20'] = low.rolling(20).min()
    df['donchian_mid_20'] = (df['donchian_high_20'] + df['donchian_low_20']) / 2
    df['donchian_width'] = (df['donchian_high_20'] - df['donchian_low_20']) / (df['donchian_mid_20'] + 1e-10)
    df['donchian_pos'] = (close - df['donchian_low_20']) / (df['donchian_high_20'] - df['donchian_low_20'] + 1e-10)

    # Breakout signals: price at 20-bar high/low
    df['at_donchian_high'] = (close >= df['donchian_high_20'].shift(1)).astype(int)
    df['at_donchian_low'] = (close <= df['donchian_low_20'].shift(1)).astype(int)

    # --- Stochastic ---
    stoch_k, stoch_d = _stochastic(df, 14, 3)
    df['stoch_k'] = stoch_k
    df['stoch_d'] = stoch_d
    df['stoch_cross'] = np.sign(stoch_k - stoch_d) - np.sign(stoch_k.shift(1) - stoch_d.shift(1))

    # --- MFI (Money Flow Index) ---
    df['mfi_14'] = _mfi(df, 14)

    # --- Log Returns ---
    df['log_return_1'] = np.log(close / close.shift(1))
    df['log_return_3'] = np.log(close / close.shift(3))
    df['log_return_5'] = np.log(close / close.shift(5))
    df['log_return_10'] = np.log(close / close.shift(10))

    # Realized volatility (20-bar rolling std of log returns)
    df['realized_vol_20'] = df['log_return_1'].rolling(20).std() * np.sqrt(252)

    # --- Candle Body features ---
    df['body'] = abs(close - open_)
    df['body_ratio'] = df['body'] / (high - low + 1e-10)
    df['upper_wick'] = high - df[['open', 'close']].max(axis=1)
    df['lower_wick'] = df[['open', 'close']].min(axis=1) - low
    df['candle_dir'] = np.sign(close - open_)  # 1=bull, -1=bear

    # Consecutive same-direction candles
    df['consec_bull'] = _consecutive_same(df['candle_dir'], 1)
    df['consec_bear'] = _consecutive_same(df['candle_dir'], -1)

    # Ichimoku
    ichi = _ichimoku(df)
    df['ichi_tenkan'] = ichi['tenkan']
    df['ichi_kijun'] = ichi['kijun']
    df['ichi_above_cloud'] = ichi['above_cloud'].astype(float)
    df['ichi_tk_cross'] = ichi['tk_cross']

    return df


# ─── Private helpers ──────────────────────────────────────────────────────────

def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift(1)).abs()
    lc = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int) -> dict:
    high = df['high']
    low = df['low']
    close = df['close']

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    dm_plus = (high - high.shift(1)).clip(lower=0)
    dm_minus = (low.shift(1) - low).clip(lower=0)
    dm_plus = dm_plus.where(dm_plus > dm_minus, 0)
    dm_minus = dm_minus.where(dm_minus > dm_plus, 0)

    atr = tr.ewm(span=period, adjust=False).mean()
    di_plus = 100 * dm_plus.ewm(span=period, adjust=False).mean() / (atr + 1e-10)
    di_minus = 100 * dm_minus.ewm(span=period, adjust=False).mean() / (atr + 1e-10)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10)
    adx = dx.ewm(span=period, adjust=False).mean()

    return {'adx': adx, 'di_plus': di_plus, 'di_minus': di_minus}


def _supertrend(df: pd.DataFrame, period: int, multiplier: float) -> dict:
    atr = _atr(df, period)
    mid = (df['high'] + df['low']) / 2
    upper_band = mid + multiplier * atr
    lower_band = mid - multiplier * atr

    supertrend = pd.Series(np.nan, index=df.index)
    direction = pd.Series(1, index=df.index)

    for i in range(1, len(df)):
        prev_ub = upper_band.iloc[i - 1]
        prev_lb = lower_band.iloc[i - 1]
        close = df['close'].iloc[i]

        # Adjust bands
        if lower_band.iloc[i] < prev_lb or df['close'].iloc[i - 1] < prev_lb:
            lower_band.iloc[i] = lower_band.iloc[i]
        else:
            lower_band.iloc[i] = prev_lb

        if upper_band.iloc[i] > prev_ub or df['close'].iloc[i - 1] > prev_ub:
            upper_band.iloc[i] = upper_band.iloc[i]
        else:
            upper_band.iloc[i] = prev_ub

        prev_dir = direction.iloc[i - 1]
        if prev_dir == 1 and close < lower_band.iloc[i]:
            direction.iloc[i] = -1
        elif prev_dir == -1 and close > upper_band.iloc[i]:
            direction.iloc[i] = 1
        else:
            direction.iloc[i] = prev_dir

        supertrend.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == 1 else upper_band.iloc[i]

    return {'supertrend': supertrend, 'direction': direction}


def _cci(df: pd.DataFrame, period: int) -> pd.Series:
    tp = (df['high'] + df['low'] + df['close']) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (tp - sma) / (0.015 * mad + 1e-10)


def _willr(df: pd.DataFrame, period: int) -> pd.Series:
    hh = df['high'].rolling(period).max()
    ll = df['low'].rolling(period).min()
    return -100 * (hh - df['close']) / (hh - ll + 1e-10)


def _stochastic(df: pd.DataFrame, k_period: int, d_period: int):
    ll = df['low'].rolling(k_period).min()
    hh = df['high'].rolling(k_period).max()
    k = 100 * (df['close'] - ll) / (hh - ll + 1e-10)
    d = k.rolling(d_period).mean()
    return k, d


def _mfi(df: pd.DataFrame, period: int) -> pd.Series:
    tp = (df['high'] + df['low'] + df['close']) / 3
    mf = tp * df['volume']
    pos_mf = mf.where(tp > tp.shift(1), 0).rolling(period).sum()
    neg_mf = mf.where(tp < tp.shift(1), 0).rolling(period).sum()
    return 100 - 100 / (1 + pos_mf / (neg_mf + 1e-10))


def _wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)
    return series.rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def _hma(series: pd.Series, period: int) -> pd.Series:
    half = _wma(series, period // 2)
    full = _wma(series, period)
    raw = 2 * half - full
    return _wma(raw, int(np.sqrt(period)))


def _divergence(price: pd.Series, indicator: pd.Series, window: int) -> pd.Series:
    """Positive = bullish divergence (price down, indicator up). Negative = bearish."""
    price_slope = price.diff(window)
    ind_slope = indicator.diff(window)
    divergence = np.sign(ind_slope) - np.sign(price_slope)
    return divergence  # +2=bullish div, -2=bearish div, 0=no div


def _consecutive_same(dir_series: pd.Series, target: int) -> pd.Series:
    result = pd.Series(0, index=dir_series.index)
    count = 0
    for i in range(len(dir_series)):
        if dir_series.iloc[i] == target:
            count += 1
        else:
            count = 0
        result.iloc[i] = count
    return result


def _ichimoku(df: pd.DataFrame) -> dict:
    high = df['high']
    low = df['low']
    close = df['close']

    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)

    above_cloud = (close > senkou_a) & (close > senkou_b)
    tk_cross = np.sign(tenkan - kijun) - np.sign(tenkan.shift(1) - kijun.shift(1))

    return {
        'tenkan': tenkan,
        'kijun': kijun,
        'above_cloud': above_cloud,
        'tk_cross': tk_cross,
    }
