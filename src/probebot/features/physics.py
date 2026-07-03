"""Physics, complexity and information-theoretic market indicators.

Sources: mbot (MDEF entropy/velocity), dbot/oraclebot (Hurst, Higuchi, Kalman, WPI, CCT),
zerobot (EAR entropy), apexbot (Hurst+Entropy scoring), flowbot (Hilbert phase).
"""
import numpy as np
import pandas as pd
from scipy.signal import hilbert

from .scaling import sp


# ─── Public entry point ───────────────────────────────────────────────────────

def add_all_physics(df: pd.DataFrame, scale: float = 1.0) -> pd.DataFrame:
    """scale: siehe technical.add_all_technical — skaliert alle Fenster/Lags."""
    df = df.copy()
    close = df['close']
    log_ret = np.log(close / close.shift(1))

    # Velocity & Acceleration (MBOT-style)
    df['velocity'] = close.diff()
    df['acceleration'] = df['velocity'].diff()
    df['jerk'] = df['acceleration'].diff()  # 3rd derivative
    df['energy'] = df['velocity'] ** 2

    # Shannon Entropy (multiple windows)
    for w in [10, 20, 40]:
        df[f'entropy_{w}'] = _rolling_entropy(log_ret, sp(w, scale))

    # Hurst Exponent (R/S analysis, multiple windows)
    for w in [30, 60, 100]:
        df[f'hurst_{w}'] = _rolling_hurst(close, sp(w, scale))

    # Higuchi Fractal Dimension
    df['higuchi_fd'] = _rolling_higuchi(close, window=sp(30, scale), kmax=6)

    # DFA alpha (Detrended Fluctuation Analysis)
    df['dfa_alpha'] = _rolling_dfa(log_ret, window=sp(60, scale))

    # Kalman velocity estimate
    df['kalman_vel'] = _kalman_velocity(close)

    # Autocorrelation at lags 1, 5, 10
    _ac_window = sp(20, scale)
    for lag in [1, 5, 10]:
        lag_s = sp(lag, scale, minimum=1)
        df[f'autocorr_{lag}'] = log_ret.rolling(_ac_window).apply(
            lambda x, lag_s=lag_s: pd.Series(x).autocorr(lag=lag_s) if len(x) > lag_s else np.nan,
            raw=False
        )

    # Variance Ratio (Lo & MacKinlay) — mean reversion detector
    df['variance_ratio'] = _rolling_variance_ratio(log_ret, window=sp(40, scale), q=sp(4, scale, minimum=2))

    # Wick Pressure Imbalance (oraclebot/dbot)
    df['wpi'] = _wick_pressure_imbalance(df)

    # Memory Pressure (exp-weighted WPI accumulation)
    df['memory_pressure'] = _memory_pressure(df['wpi'], decay=0.9)

    # Candle Compression Tension
    df['cct'] = _candle_compression_tension(df, window=sp(10, scale))

    # FFT dominant period (mbot-style cycle detection)
    df['fft_dominant_period'] = _rolling_fft_period(close, window=sp(64, scale))

    # Hilbert Transform Phase (flowbot-style)
    df['hilbert_phase'] = _rolling_hilbert_phase(close, window=sp(32, scale))
    df['hilbert_phase_cos'] = np.cos(df['hilbert_phase'])
    df['hilbert_phase_sin'] = np.sin(df['hilbert_phase'])

    # EAR Entropy per candle (zerobot-style)
    df['ear_entropy'] = _ear_entropy(df, window=sp(10, scale))

    # Phase space regime (mbot-style): 1=trend, 0=range, -1=chaos
    df['phase_regime'] = _phase_regime(df, window=sp(20, scale))

    # Lyapunov exponent approximation
    df['lyapunov'] = _rolling_lyapunov(close, window=sp(30, scale))

    # Normalized energy (kinetic energy proxy, z-score)
    _ez_w = sp(50, scale)
    df['energy_z'] = (df['energy'] - df['energy'].rolling(_ez_w).mean()) / (df['energy'].rolling(_ez_w).std() + 1e-10)

    # Entropy squeeze: entropy below rolling mean (order building up)
    df['entropy_squeeze'] = (df['entropy_20'] < df['entropy_20'].rolling(sp(50, scale)).mean() * 0.8).astype(float)

    # Entropy trend: is entropy rising or falling?
    df['entropy_slope'] = df['entropy_20'].diff(sp(3, scale, minimum=1))

    return df


# ─── Shannon Entropy ──────────────────────────────────────────────────────────

def _rolling_entropy(log_ret: pd.Series, window: int, bins: int = 10) -> pd.Series:
    result = pd.Series(np.nan, index=log_ret.index)
    arr = log_ret.values
    for i in range(window, len(arr)):
        chunk = arr[i - window:i]
        chunk = chunk[~np.isnan(chunk)]
        if len(chunk) < window // 2:
            continue
        hist, _ = np.histogram(chunk, bins=bins, density=True)
        hist = hist[hist > 0]
        # Normalize to probability distribution
        p = hist / hist.sum()
        result.iloc[i] = float(-np.sum(p * np.log(p + 1e-12)))
    return result


# ─── Hurst Exponent ───────────────────────────────────────────────────────────

def _rolling_hurst(prices: pd.Series, window: int) -> pd.Series:
    result = pd.Series(np.nan, index=prices.index)
    log_r = np.log(prices / prices.shift(1))
    arr = log_r.values

    for i in range(window, len(arr)):
        chunk = arr[i - window:i]
        chunk = chunk[~np.isnan(chunk)]
        if len(chunk) < window // 2:
            continue
        mean = np.mean(chunk)
        cum_dev = np.cumsum(chunk - mean)
        R = np.max(cum_dev) - np.min(cum_dev)
        S = np.std(chunk, ddof=1)
        if S > 0 and R > 0:
            result.iloc[i] = np.log(R / S) / np.log(len(chunk))
    return result


# ─── Higuchi Fractal Dimension ────────────────────────────────────────────────

def _higuchi_fd_single(x: np.ndarray, kmax: int = 6) -> float:
    N = len(x)
    L = []
    x_arr = np.array(x)
    for k in range(1, kmax + 1):
        L_k = []
        for m in range(1, k + 1):
            n_max = int(np.floor((N - m) / k))
            if n_max < 1:
                continue
            indices = m - 1 + np.arange(1, n_max + 1) * k
            if indices[-1] >= N:
                indices = indices[indices < N]
            prev_indices = indices - k
            if len(indices) == 0:
                continue
            lm = np.sum(np.abs(x_arr[indices] - x_arr[prev_indices]))
            lm *= (N - 1) / (n_max * k)
            L_k.append(lm)
        if L_k:
            L.append(np.mean(L_k))
        else:
            L.append(np.nan)

    L = np.array(L, dtype=float)
    valid = ~np.isnan(L) & (L > 0)
    if valid.sum() < 2:
        return np.nan
    k_vals = np.arange(1, kmax + 1)[valid]
    slope, _ = np.polyfit(np.log(k_vals), np.log(L[valid]), 1)
    return -slope


def _rolling_higuchi(prices: pd.Series, window: int = 30, kmax: int = 6) -> pd.Series:
    arr = prices.values.astype(float)
    result = pd.Series(np.nan, index=prices.index)
    for i in range(window, len(arr)):
        chunk = arr[i - window:i]
        if np.any(np.isnan(chunk)):
            continue
        result.iloc[i] = _higuchi_fd_single(chunk, kmax)
    return result


# ─── Detrended Fluctuation Analysis ──────────────────────────────────────────

def _dfa_single(x: np.ndarray, scales=None) -> float:
    N = len(x)
    y = np.cumsum(x - np.mean(x))
    if scales is None:
        scales = np.unique(np.logspace(np.log10(4), np.log10(N // 4), 15, dtype=int))

    F = []
    valid_scales = []
    for s in scales:
        if s < 2:
            continue
        n_seg = N // s
        if n_seg < 1:
            continue
        flucts = []
        for j in range(n_seg):
            seg = y[j * s:(j + 1) * s]
            t = np.arange(s)
            c = np.polyfit(t, seg, 1)
            trend = np.polyval(c, t)
            flucts.append(np.mean((seg - trend) ** 2))
        F.append(np.sqrt(np.mean(flucts)))
        valid_scales.append(s)

    if len(valid_scales) < 2:
        return np.nan
    alpha, _ = np.polyfit(np.log(valid_scales), np.log(np.array(F) + 1e-12), 1)
    return alpha


def _rolling_dfa(log_ret: pd.Series, window: int = 60) -> pd.Series:
    arr = log_ret.values
    result = pd.Series(np.nan, index=log_ret.index)
    for i in range(window, len(arr)):
        chunk = arr[i - window:i]
        chunk = chunk[~np.isnan(chunk)]
        if len(chunk) < 20:
            continue
        result.iloc[i] = _dfa_single(chunk)
    return result


# ─── Kalman Filter Velocity ───────────────────────────────────────────────────

def _kalman_velocity(prices: pd.Series, q: float = 0.001, r: float = 0.01) -> pd.Series:
    vals = prices.values.astype(float)
    n = len(vals)
    x = np.array([vals[0], 0.0])
    P = np.eye(2) * 0.1
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q = np.array([[q, 0.0], [0.0, q]])
    R_mat = np.array([[r]])

    velocities = [0.0]
    for i in range(1, n):
        x = F @ x
        P = F @ P @ F.T + Q
        K = P @ H.T @ np.linalg.inv(H @ P @ H.T + R_mat)
        innovation = np.array([[vals[i]]]) - H @ x
        x = x + K.flatten() * innovation.flatten()
        P = (np.eye(2) - np.outer(K.flatten(), H.flatten())) @ P
        velocities.append(float(x[1]))

    return pd.Series(velocities, index=prices.index)


# ─── Variance Ratio (Lo & MacKinlay) ─────────────────────────────────────────

def _rolling_variance_ratio(log_ret: pd.Series, window: int = 40, q: int = 4) -> pd.Series:
    arr = log_ret.values
    result = pd.Series(np.nan, index=log_ret.index)
    for i in range(window, len(arr)):
        chunk = arr[i - window:i]
        chunk = chunk[~np.isnan(chunk)]
        if len(chunk) < q + 2:
            continue
        var1 = np.var(chunk, ddof=1)
        # q-period returns
        q_ret = np.array([chunk[j:j+q].sum() for j in range(len(chunk) - q + 1)])
        varq = np.var(q_ret, ddof=1) / q
        vr = varq / (var1 + 1e-12)
        result.iloc[i] = vr
    return result  # >1 trending, <1 mean-reverting, =1 random walk


# ─── Wick Pressure Imbalance ──────────────────────────────────────────────────

def _wick_pressure_imbalance(df: pd.DataFrame) -> pd.Series:
    upper_wick = df['high'] - df[['open', 'close']].max(axis=1)
    lower_wick = df[['open', 'close']].min(axis=1) - df['low']
    total_range = df['high'] - df['low']
    # Positive = lower wick dominant = buy pressure; Negative = upper wick dominant = sell pressure
    return (lower_wick - upper_wick) / (total_range + 1e-10)


# ─── Memory Pressure ─────────────────────────────────────────────────────────

def _memory_pressure(wpi: pd.Series, decay: float = 0.9) -> pd.Series:
    mp = pd.Series(0.0, index=wpi.index)
    vals = wpi.values
    for i in range(1, len(vals)):
        prev = mp.iloc[i - 1]
        mp.iloc[i] = decay * prev + (vals[i] if not np.isnan(vals[i]) else 0.0)
    return mp


# ─── Candle Compression Tension ───────────────────────────────────────────────

def _candle_compression_tension(df: pd.DataFrame, window: int = 10) -> pd.Series:
    body = (df['close'] - df['open']).abs()
    total_range = df['high'] - df['low']
    body_ratio = body / (total_range + 1e-10)
    atr = total_range.rolling(window).mean()
    vol_ratio = total_range / (atr + 1e-10)
    # High CCT = wide candle but tiny body = directional uncertainty = coiled spring
    return (1 - body_ratio) * vol_ratio


# ─── FFT Dominant Period ──────────────────────────────────────────────────────

def _rolling_fft_period(prices: pd.Series, window: int = 64) -> pd.Series:
    arr = prices.values.astype(float)
    result = pd.Series(np.nan, index=prices.index)
    for i in range(window, len(arr)):
        chunk = arr[i - window:i]
        chunk = chunk - np.mean(chunk)
        freqs = np.fft.rfftfreq(window)
        fft_mag = np.abs(np.fft.rfft(chunk))
        # Ignore DC component
        fft_mag[0] = 0
        valid = freqs > 0
        if not valid.any():
            continue
        dominant_idx = np.argmax(fft_mag[valid])
        dom_freq = freqs[valid][dominant_idx]
        if dom_freq > 0:
            result.iloc[i] = 1.0 / dom_freq
    return result


# ─── Hilbert Transform Phase ──────────────────────────────────────────────────

def _rolling_hilbert_phase(prices: pd.Series, window: int = 32) -> pd.Series:
    arr = prices.values.astype(float)
    result = pd.Series(np.nan, index=prices.index)
    for i in range(window, len(arr)):
        chunk = arr[i - window:i]
        chunk = chunk - np.mean(chunk)
        if np.std(chunk) < 1e-10:
            continue
        analytic = hilbert(chunk)
        phase = np.angle(analytic)
        result.iloc[i] = float(phase[-1])
    return result


# ─── EAR Entropy (zerobot-style per-candle) ──────────────────────────────────

def _ear_entropy(df: pd.DataFrame, window: int = 10) -> pd.Series:
    total = df['high'] - df['low']
    p_bull = (df['close'] - df['low']) / (total + 1e-10)
    p_bear = (df['high'] - df['close']) / (total + 1e-10)
    # Shannon entropy for 2-outcome system
    h = -(
        p_bull.clip(1e-10, 1 - 1e-10) * np.log2(p_bull.clip(1e-10, 1 - 1e-10)) +
        p_bear.clip(1e-10, 1 - 1e-10) * np.log2(p_bear.clip(1e-10, 1 - 1e-10))
    )
    return h.rolling(window).mean()


# ─── Phase Space Regime ───────────────────────────────────────────────────────

def _phase_regime(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    Classify market state using velocity & acceleration (mbot MDEF-style).
    Returns: 1=trend, 0=range, -1=chaos
    """
    vel = df['velocity'] if 'velocity' in df.columns else df['close'].diff()
    acc = vel.diff()
    vel_z = (vel - vel.rolling(window).mean()) / (vel.rolling(window).std() + 1e-10)
    acc_z = (acc - acc.rolling(window).mean()) / (acc.rolling(window).std() + 1e-10)

    regime = pd.Series(0, index=df.index)
    # Trend: strong velocity, low acceleration change
    trend_mask = (vel_z.abs() > 1.5) & (acc_z.abs() < 1.0)
    # Chaos: high acceleration, direction flipping
    chaos_mask = (acc_z.abs() > 2.0)
    regime[trend_mask] = 1
    regime[chaos_mask] = -1
    return regime


# ─── Lyapunov Exponent (approx) ──────────────────────────────────────────────

def _rolling_lyapunov(prices: pd.Series, window: int = 30) -> pd.Series:
    """Approximate maximum Lyapunov exponent via divergence of nearby trajectories."""
    arr = prices.values.astype(float)
    result = pd.Series(np.nan, index=prices.index)
    for i in range(window, len(arr)):
        chunk = arr[i - window:i]
        if np.any(np.isnan(chunk)):
            continue
        # Normalize
        std = np.std(chunk)
        if std < 1e-10:
            continue
        norm = (chunk - np.mean(chunk)) / std
        # Sum of log distances between consecutive points
        diffs = np.abs(np.diff(norm))
        diffs = diffs[diffs > 0]
        if len(diffs) == 0:
            continue
        result.iloc[i] = np.mean(np.log(diffs + 1e-10))
    return result
