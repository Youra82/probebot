"""Feature engine: combines all feature modules into one enriched DataFrame."""
import pandas as pd
import numpy as np

from .technical import add_all_technical
from .physics import add_all_physics
from .structure import add_all_structure
from .volume import add_all_volume
from .scaling import timeframe_scale, sp


def compute_all_features(df: pd.DataFrame, min_candles: int = 200, verbose: bool = True,
                          timeframe: str = '1h', scale_multiplier: float = 1.0) -> pd.DataFrame:
    """
    Compute the full feature set for the given OHLCV DataFrame.
    Returns the enriched DataFrame (all NaN rows from warmup period preserved).

    timeframe: skaliert alle Indikator-Perioden auf dieselbe reale Zeitspanne
    wie bei 1h (Baseline, scale=1.0 → unveraendertes Verhalten).
    scale_multiplier: zusaetzlicher Faktor obendrauf (z.B. 0.5/1.5/2.0) — fuer
    die Perioden-Kandidatensuche in run.py (nicht alle Timeframes haben
    zwingend die "1h linear skaliert"-Periode als beste Wahl).
    """
    if len(df) < min_candles:
        raise ValueError(f"Need at least {min_candles} candles, got {len(df)}")

    df = df.copy()
    scale = timeframe_scale(timeframe) * scale_multiplier

    if verbose:
        print("  [features] computing technical indicators...")
    df = add_all_technical(df, scale=scale)

    if verbose:
        print("  [features] computing physics/complexity indicators...")
    df = add_all_physics(df, scale=scale)

    if verbose:
        print("  [features] computing market structure...")
    df = add_all_structure(df, scale=scale)

    if verbose:
        print("  [features] computing volume analysis...")
    df = add_all_volume(df, scale=scale)

    # DNA-style candle encoding (inspired by dnabot)
    df['dna_code'] = _dna_encode(df, scale=scale)

    # Regime label (multi-method consensus)
    df['regime'] = _regime_consensus(df, scale=scale)

    # Composite trend score (-10 to +10)
    df['trend_score'] = _trend_score(df)

    # Composite momentum score (-10 to +10)
    df['momentum_score'] = _momentum_score(df, scale=scale)

    # Market readiness score (0-10: how "ready" is market for a big move?)
    df['move_readiness'] = _move_readiness(df, scale=scale)

    return df


def feature_vector(df: pd.DataFrame, idx: int) -> dict:
    """
    Extract the feature vector at index `idx` as a flat dict.
    Used for pattern mining and correlation.
    """
    row = df.iloc[idx]
    # Only include numeric, non-index columns
    skip = {'timestamp', 'open', 'high', 'low', 'close', 'volume'}
    result = {}
    for col in df.columns:
        if col in skip:
            continue
        val = row[col]
        if isinstance(val, (int, float, np.integer, np.floating)):
            result[col] = float(val)
        elif isinstance(val, (bool, np.bool_)):
            # np.bool_ (was df.iloc[idx] fuer bool-Spalten liefert) ist KEINE
            # Unterklasse von Python bool — ohne np.bool_ wurden bool-Feature-
            # Spalten (z.B. swing_low/high, bb_squeeze, kc_squeeze) hier bisher
            # komplett stillschweigend verworfen, nie Teil irgendeiner
            # Korrelations-/Forensik-Analyse.
            result[col] = float(val)
    return result


def feature_vectors_bulk(df: pd.DataFrame, indices: list) -> list:
    """
    Wie feature_vector(), aber fuer viele Indizes auf einmal.

    feature_vector() macht pro Aufruf einen eigenen df.iloc[idx]-Zugriff plus
    eine Python-Schleife ueber alle Spalten — bei vielen Indizes (z.B. correlator.py's
    Event-/Hintergrund-Stichproben, oft tausende Aufrufe pro Analyse) ist genau
    das der dominante Kostenpunkt, nicht die eigentliche Statistik (siehe
    correlator.py's _vectorized_ttests() docstring). Hier: EIN Bulk-Zugriff via
    df.iloc[indices] statt vieler Einzelzugriffe, Spaltenfilter per Dtype statt
    Wert-fuer-Wert-isinstance (fuer die hier durchgehend gleichmaessig typisierten
    Feature-Spalten aequivalent — per Parity-Test gegen feature_vector() geprueft).

    Reihenfolge der Rueckgabe entspricht der Reihenfolge von `indices`.
    """
    skip = {'timestamp', 'open', 'high', 'low', 'close', 'volume'}
    cols = [c for c in df.columns if c not in skip and (
        pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_bool_dtype(df[c])
    )]
    sub = df.iloc[indices][cols].astype(float)
    return sub.to_dict('records')


# ─── Composite Scores ─────────────────────────────────────────────────────────

def _dna_encode(df: pd.DataFrame, scale: float = 1.0) -> pd.Series:
    """Simple dnabot-style candle encoding: direction + body + volatility."""
    close = df['close']
    open_ = df['open']
    atr = df.get('atr_14', (df['high'] - df['low']).rolling(sp(14, scale)).mean())

    direction = np.where(close >= open_, 'B', 'S')
    body = (close - open_).abs()
    body_size = np.where(body < 0.3 * atr, '1', np.where(body < 0.8 * atr, '2', '3'))
    candle_range = df['high'] - df['low']
    vol_label = np.where(candle_range < atr, 'L', 'H')

    codes = pd.Series(
        [f"{d}{b}{v}" for d, b, v in zip(direction, body_size, vol_label)],
        index=df.index
    )
    return codes


def _regime_consensus(df: pd.DataFrame, scale: float = 1.0) -> pd.Series:
    """
    Consensus regime from multiple methods.
    Returns: 'TREND', 'RANGE', 'CHAOS', 'UNKNOWN'
    """
    votes = pd.DataFrame(index=df.index)

    if 'adx' in df.columns:
        votes['adx_trend'] = (df['adx'] > 25).astype(int)
        votes['adx_range'] = (df['adx'] < 20).astype(int)

    if 'hurst_60' in df.columns:
        votes['hurst_trend'] = (df['hurst_60'] > 0.55).astype(int)
        votes['hurst_range'] = (df['hurst_60'] < 0.45).astype(int)

    if 'phase_regime' in df.columns:
        votes['phase_trend'] = (df['phase_regime'] == 1).astype(int)
        votes['phase_chaos'] = (df['phase_regime'] == -1).astype(int)

    if 'entropy_20' in df.columns:
        _w = sp(50, scale)
        votes['ent_order'] = (df['entropy_20'] < df['entropy_20'].rolling(_w).mean() * 0.8).astype(int)
        votes['ent_chaos'] = (df['entropy_20'] > df['entropy_20'].rolling(_w).mean() * 1.2).astype(int)

    trend_votes = sum(votes[c] for c in votes.columns if 'trend' in c)
    range_votes = sum(votes[c] for c in votes.columns if 'range' in c)
    chaos_votes = sum(votes[c] for c in votes.columns if 'chaos' in c)

    regime = pd.Series('UNKNOWN', index=df.index)
    max_vote = pd.concat([trend_votes, range_votes, chaos_votes], axis=1).max(axis=1)
    regime[trend_votes == max_vote] = 'TREND'
    regime[range_votes == max_vote] = 'RANGE'
    regime[chaos_votes == max_vote] = 'CHAOS'
    return regime


def _trend_score(df: pd.DataFrame) -> pd.Series:
    """Composite trend score from -10 to +10."""
    score = pd.Series(0.0, index=df.index)

    if 'ema_alignment' in df.columns:
        score += df['ema_alignment']  # -3 to +3

    if 'supertrend_dir' in df.columns:
        score += df['supertrend_dir'] * 2  # -2 or +2

    if 'adx' in df.columns:
        adx_factor = (df['adx'] - 20).clip(0, 30) / 30  # 0 to 1
        if 'di_delta' in df.columns:
            score += np.sign(df['di_delta']) * adx_factor * 2

    if 'ichi_above_cloud' in df.columns:
        score += (df['ichi_above_cloud'] * 2 - 1)  # +1 or -1

    if 'struct_score' in df.columns:
        score += df['struct_score'].clip(-2, 2) * 0.5

    return score.clip(-10, 10)


def _momentum_score(df: pd.DataFrame, scale: float = 1.0) -> pd.Series:
    """Composite momentum score from -10 to +10."""
    score = pd.Series(0.0, index=df.index)

    if 'rsi_14' in df.columns:
        score += (df['rsi_14'] - 50) / 10  # -5 to +5

    if 'macd_hist' in df.columns:
        macd_std = df['macd_hist'].rolling(sp(50, scale)).std()
        score += (df['macd_hist'] / (macd_std + 1e-10)).clip(-3, 3)

    if 'stoch_k' in df.columns:
        score += (df['stoch_k'] - 50) / 25  # -2 to +2

    if 'cci_20' in df.columns:
        score += (df['cci_20'] / 100).clip(-2, 2)

    return score.clip(-10, 10)


def _move_readiness(df: pd.DataFrame, scale: float = 1.0) -> pd.Series:
    """
    How coiled is the market? Higher score = more energy ready to release.
    Combines: squeeze, entropy compression, low volume, CCT, Hurst approaching 0.5.
    Range: 0 to 10.
    """
    score = pd.Series(0.0, index=df.index)

    if 'kc_squeeze' in df.columns:
        score += df['kc_squeeze'].astype(float) * 2

    if 'entropy_squeeze' in df.columns:
        score += df['entropy_squeeze'].astype(float) * 2

    if 'cct' in df.columns:
        _w = sp(50, scale)
        cct_z = (df['cct'] - df['cct'].rolling(_w).mean()) / (df['cct'].rolling(_w).std() + 1e-10)
        score += cct_z.clip(0, 2)

    if 'volume_dry_up' in df.columns:
        score += df['volume_dry_up'] * 1.5

    if 'atr_z' in df.columns:
        # Low ATR relative to history = compression
        score += (-df['atr_z']).clip(0, 2)

    if 'hurst_60' in df.columns:
        # Hurst near 0.5 = transition point
        score += (1 - (df['hurst_60'] - 0.5).abs() * 4).clip(0, 1)

    return score.clip(0, 10)
