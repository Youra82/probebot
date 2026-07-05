"""Physics, complexity and information-theoretic market indicators.

Sources: mbot (MDEF entropy/velocity), dbot/oraclebot (Hurst, Higuchi, Kalman, WPI, CCT),
zerobot (EAR entropy), apexbot (Hurst+Entropy scoring), flowbot (Hilbert phase).

Performance: die rollierenden Fenster-Berechnungen (Hurst, DFA, Higuchi, Entropie,
Autokorrelation, Variance-Ratio, Lyapunov) sind mit Numba JIT-kompiliert. Das ist
reine Performance-Optimierung — die Formeln sind unveraendert, nur die Ausfuehrung
ist ca. 100x schneller (siehe Validierung: max. Abweichung zum reinen Python-Code
liegt im Bereich von Gleitkomma-Rauschen, < 1e-13). DFA allein macht ueblicherweise
~70% der gesamten Feature-Berechnungszeit aus.
"""
import numpy as np
import numba
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
        df[f'autocorr_{lag}'] = _rolling_autocorr(log_ret, _ac_window, lag_s)

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

@numba.njit(cache=True)
def _entropy_single(chunk: np.ndarray, bins: int) -> float:
    valid = chunk[~np.isnan(chunk)]
    if len(valid) < len(chunk) // 2:
        return np.nan
    mn = np.min(valid)
    mx = np.max(valid)
    if mx == mn:
        return np.nan
    width = (mx - mn) / bins
    counts = np.zeros(bins)
    for v in valid:
        b = int((v - mn) / width)
        if b >= bins:
            b = bins - 1
        if b < 0:
            b = 0
        counts[b] += 1
    total = np.sum(counts)
    ent = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            ent -= p * np.log(p + 1e-12)
    return ent


@numba.njit(cache=True)
def _rolling_entropy_jit(arr: np.ndarray, window: int, bins: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window, n):
        result[i] = _entropy_single(arr[i - window:i], bins)
    return result


def _rolling_entropy(log_ret: pd.Series, window: int, bins: int = 10) -> pd.Series:
    return pd.Series(_rolling_entropy_jit(log_ret.values, window, bins), index=log_ret.index)


# ─── Hurst Exponent ───────────────────────────────────────────────────────────

@numba.njit(cache=True)
def _rolling_hurst_jit(log_r: np.ndarray, window: int) -> np.ndarray:
    n = len(log_r)
    result = np.full(n, np.nan)
    for i in range(window, n):
        chunk = log_r[i - window:i]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) < window // 2:
            continue
        m = np.mean(valid)
        cum_dev = np.cumsum(valid - m)
        R = np.max(cum_dev) - np.min(cum_dev)
        var = np.sum((valid - m) ** 2) / (len(valid) - 1)
        S = np.sqrt(var)
        if S > 0 and R > 0:
            result[i] = np.log(R / S) / np.log(len(valid))
    return result


def _rolling_hurst(prices: pd.Series, window: int) -> pd.Series:
    log_r = np.log(prices / prices.shift(1))
    return pd.Series(_rolling_hurst_jit(log_r.values, window), index=prices.index)


# ─── Higuchi Fractal Dimension ────────────────────────────────────────────────

@numba.njit(cache=True)
def _higuchi_fd_single(x: np.ndarray, kmax: int = 6) -> float:
    N = len(x)
    L = np.empty(kmax)
    for k in range(1, kmax + 1):
        Lk_sum = 0.0
        Lk_cnt = 0
        for m in range(1, k + 1):
            n_max = int(np.floor((N - m) / k))
            if n_max < 1:
                continue
            cnt = 0
            lm = 0.0
            for cc in range(1, n_max + 1):
                cur = m - 1 + cc * k
                prev = cur - k
                if cur >= N:
                    break
                lm += abs(x[cur] - x[prev])
                cnt += 1
            if cnt > 0:
                lm = lm * (N - 1) / (cnt * k)
                Lk_sum += lm
                Lk_cnt += 1
        L[k - 1] = Lk_sum / Lk_cnt if Lk_cnt > 0 else np.nan

    valid_cnt = 0
    for k in range(kmax):
        if not np.isnan(L[k]) and L[k] > 0:
            valid_cnt += 1
    if valid_cnt < 2:
        return np.nan

    kv = np.empty(valid_cnt)
    lv = np.empty(valid_cnt)
    j = 0
    for k in range(kmax):
        if not np.isnan(L[k]) and L[k] > 0:
            kv[j] = k + 1
            lv[j] = L[k]
            j += 1
    log_k = np.log(kv)
    log_l = np.log(lv)
    mk = np.mean(log_k)
    ml = np.mean(log_l)
    num = np.sum((log_k - mk) * (log_l - ml))
    den = np.sum((log_k - mk) ** 2)
    if den == 0:
        return np.nan
    return -(num / den)


@numba.njit(cache=True)
def _rolling_higuchi_jit(arr: np.ndarray, window: int, kmax: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window, n):
        chunk = arr[i - window:i]
        has_nan = False
        for v in chunk:
            if np.isnan(v):
                has_nan = True
                break
        if has_nan:
            continue
        result[i] = _higuchi_fd_single(chunk, kmax)
    return result


def _rolling_higuchi(prices: pd.Series, window: int = 30, kmax: int = 6) -> pd.Series:
    arr = prices.values.astype(np.float64)
    return pd.Series(_rolling_higuchi_jit(arr, window, kmax), index=prices.index)


# ─── Detrended Fluctuation Analysis ──────────────────────────────────────────

@numba.njit(cache=True)
def _dfa_single(x: np.ndarray) -> float:
    N = len(x)
    y = np.cumsum(x - np.mean(x))
    n4 = N // 4
    if n4 < 4:
        return np.nan

    lo = np.log10(4.0)
    hi = np.log10(float(n4))
    step = (hi - lo) / 14.0
    scales_f = np.empty(15)
    for k in range(15):
        scales_f[k] = 10.0 ** (lo + k * step)
    scales_int = scales_f.astype(np.int64)
    scales_unique = np.unique(scales_int)

    F = np.empty(len(scales_unique))
    valid_scales = np.empty(len(scales_unique), dtype=np.int64)
    n_valid = 0
    for si in range(len(scales_unique)):
        s = scales_unique[si]
        if s < 2:
            continue
        n_seg = N // s
        if n_seg < 1:
            continue
        flucts = np.empty(n_seg)
        for j in range(n_seg):
            seg = y[j * s:(j + 1) * s]
            t = np.arange(s).astype(np.float64)
            t_mean = np.mean(t)
            seg_mean = np.mean(seg)
            num = np.sum((t - t_mean) * (seg - seg_mean))
            den = np.sum((t - t_mean) ** 2)
            a = num / den if den != 0 else 0.0
            b = seg_mean - a * t_mean
            trend = a * t + b
            flucts[j] = np.mean((seg - trend) ** 2)
        F[n_valid] = np.sqrt(np.mean(flucts))
        valid_scales[n_valid] = s
        n_valid += 1

    if n_valid < 2:
        return np.nan
    log_s = np.log(valid_scales[:n_valid].astype(np.float64))
    log_F = np.log(F[:n_valid] + 1e-12)
    ls_mean = np.mean(log_s)
    lf_mean = np.mean(log_F)
    num = np.sum((log_s - ls_mean) * (log_F - lf_mean))
    den = np.sum((log_s - ls_mean) ** 2)
    if den == 0:
        return np.nan
    return num / den


@numba.njit(cache=True)
def _rolling_dfa_jit(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window, n):
        chunk = arr[i - window:i]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) < 20:
            continue
        result[i] = _dfa_single(valid)
    return result


def _rolling_dfa(log_ret: pd.Series, window: int = 60) -> pd.Series:
    return pd.Series(_rolling_dfa_jit(log_ret.values, window), index=log_ret.index)


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


# ─── Autocorrelation ──────────────────────────────────────────────────────────

@numba.njit(cache=True)
def _autocorr_single(chunk: np.ndarray, lag: int) -> float:
    n = len(chunk)
    a = chunk[:n - lag]
    b = chunk[lag:]
    ma = np.mean(a)
    mb = np.mean(b)
    da = a - ma
    db = b - mb
    num = np.sum(da * db)
    dda = np.sum(da ** 2)
    ddb = np.sum(db ** 2)
    if dda == 0 or ddb == 0:
        return np.nan
    return num / np.sqrt(dda * ddb)


@numba.njit(cache=True)
def _rolling_autocorr_jit(arr: np.ndarray, window: int, lag: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    if window <= lag:
        return result
    for i in range(window - 1, n):
        chunk = arr[i - window + 1:i + 1]
        has_nan = False
        for v in chunk:
            if np.isnan(v):
                has_nan = True
                break
        if has_nan:
            continue
        result[i] = _autocorr_single(chunk, lag)
    return result


def _rolling_autocorr(log_ret: pd.Series, window: int, lag: int) -> pd.Series:
    """Entspricht log_ret.rolling(window).apply(lambda x: pd.Series(x).autocorr(lag=lag)), nur JIT-schnell."""
    return pd.Series(_rolling_autocorr_jit(log_ret.values, window, lag), index=log_ret.index)


# ─── Variance Ratio (Lo & MacKinlay) ─────────────────────────────────────────

@numba.njit(cache=True)
def _rolling_variance_ratio_jit(arr: np.ndarray, window: int, q: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window, n):
        chunk = arr[i - window:i]
        valid = chunk[~np.isnan(chunk)]
        if len(valid) < q + 2:
            continue
        m = np.mean(valid)
        var1 = np.sum((valid - m) ** 2) / (len(valid) - 1)
        n_q = len(valid) - q + 1
        q_ret = np.empty(n_q)
        for j in range(n_q):
            q_ret[j] = np.sum(valid[j:j + q])
        mq = np.mean(q_ret)
        varq = np.sum((q_ret - mq) ** 2) / (len(q_ret) - 1) / q
        result[i] = varq / (var1 + 1e-12)
    return result


def _rolling_variance_ratio(log_ret: pd.Series, window: int = 40, q: int = 4) -> pd.Series:
    return pd.Series(_rolling_variance_ratio_jit(log_ret.values, window, q), index=log_ret.index)
    # >1 trending, <1 mean-reverting, =1 random walk


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

@numba.njit(cache=True)
def _rolling_lyapunov_jit(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    for i in range(window, n):
        chunk = arr[i - window:i]
        has_nan = False
        for v in chunk:
            if np.isnan(v):
                has_nan = True
                break
        if has_nan:
            continue
        std = np.std(chunk)
        if std < 1e-10:
            continue
        m = np.mean(chunk)
        norm = (chunk - m) / std
        total = 0.0
        cnt = 0
        for k in range(len(norm) - 1):
            d = abs(norm[k + 1] - norm[k])
            if d > 0:
                total += np.log(d + 1e-10)
                cnt += 1
        if cnt == 0:
            continue
        result[i] = total / cnt
    return result


def _rolling_lyapunov(prices: pd.Series, window: int = 30) -> pd.Series:
    """Approximate maximum Lyapunov exponent via divergence of nearby trajectories."""
    arr = prices.values.astype(np.float64)
    return pd.Series(_rolling_lyapunov_jit(arr, window), index=prices.index)
