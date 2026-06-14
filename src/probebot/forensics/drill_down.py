"""
Multi-timeframe drill-down engine.

For a movement detected at a high timeframe (e.g. 1D), zooms progressively into
lower timeframes (4H → 1H → 15m → 5m → 1m) to:
  1. Identify the structural precursors at each level
  2. Find the earliest detectable signal
  3. Locate the optimal entry point (with rationale)
  4. Characterize what the market looked like at each zoom level
"""
import numpy as np
import pandas as pd
from typing import List, Optional, Tuple

from ..data.loader import DataLoader, DRILL_DOWN_CHAIN
from ..features.engine import compute_all_features
from ..detection.detector import MovementDetector, Movement


ENTRY_SIGNAL_WEIGHTS = {
    # Feature → (direction: 'UP'|'DOWN'|'BOTH', description)
    'rsi_divergence': ('BOTH', 'RSI divergence present'),
    'macd_hist': ('BOTH', 'MACD histogram direction'),
    'ema_alignment': ('BOTH', 'EMA stack alignment'),
    'supertrend_dir': ('BOTH', 'Supertrend direction'),
    'volume_surge': ('BOTH', 'Volume surge (institutional)'),
    'entropy_squeeze': ('BOTH', 'Entropy squeeze (energy coiled)'),
    'kc_squeeze': ('BOTH', 'Keltner Channel squeeze'),
    'move_readiness': ('BOTH', 'Market readiness score'),
    'struct_score': ('BOTH', 'Market structure score'),
    'bear_engulf': ('DOWN', 'Bearish engulfing candle'),
    'bull_engulf': ('UP', 'Bullish engulfing candle'),
    'pin_bar_bear': ('DOWN', 'Bearish pin bar'),
    'pin_bar_bull': ('UP', 'Bullish pin bar'),
    'breakout_down_20': ('DOWN', 'Broke 20-bar low'),
    'breakout_up_20': ('UP', 'Broke 20-bar high'),
    'obv_slope': ('BOTH', 'OBV trend slope'),
    'cvd_divergence': ('BOTH', 'CVD / price divergence'),
    'wpi': ('BOTH', 'Wick pressure imbalance'),
    'hurst_60': ('BOTH', 'Hurst exponent (trend persistence)'),
    'adx': ('BOTH', 'ADX trend strength'),
    'at_donchian_low': ('DOWN', 'At 20-bar Donchian low'),
    'at_donchian_high': ('UP', 'At 20-bar Donchian high'),
}


class DrillDownEngine:
    def __init__(
        self,
        loader: DataLoader,
        timeframe_chain: Optional[List[str]] = None,
        candles_before: int = 60,
        candles_after: int = 20,
    ):
        self.loader = loader
        self.chain = timeframe_chain or DRILL_DOWN_CHAIN
        self.candles_before = candles_before
        self.candles_after = candles_after
        self.detector = MovementDetector()

    def drill(
        self,
        symbol: str,
        movement: Movement,
        target_direction: str,
        start_tf: str = '1d',
    ) -> dict:
        """
        Full drill-down for a single movement event.
        Returns structured analysis per timeframe.
        """
        center_ts = movement.timestamp
        results = {}

        # Find starting point in chain
        try:
            start_idx = self.chain.index(start_tf)
        except ValueError:
            start_idx = 0

        for tf in self.chain[start_idx + 1:]:
            print(f"  [drill-down] zooming into {tf}...")
            try:
                level_result = self._analyze_level(symbol, tf, center_ts, target_direction)
                results[tf] = level_result

                # If we have a high-confidence entry at this level, we can stop
                if level_result.get('entry_confidence', 0) >= 8:
                    results[tf]['_stopped_here'] = True
                    print(f"  [drill-down] high-confidence entry found at {tf}, stopping")
                    break
            except Exception as e:
                results[tf] = {'error': str(e)}

        return results

    def _analyze_level(
        self,
        symbol: str,
        timeframe: str,
        center_ts: pd.Timestamp,
        direction: str,
    ) -> dict:
        # Load data around the event
        df_raw = self.loader.fetch_window_around(
            symbol, timeframe, center_ts,
            candles_before=self.candles_before,
            candles_after=self.candles_after,
        )
        if len(df_raw) < 50:
            return {'error': f'insufficient data ({len(df_raw)} candles)'}

        # Compute features
        try:
            df = compute_all_features(df_raw, min_candles=50)
        except Exception as e:
            return {'error': f'feature computation failed: {e}'}

        # Find the candle closest to center_ts
        ts_arr = df['timestamp'].values
        center_ms = pd.Timestamp(center_ts).value
        diffs = np.abs(pd.to_datetime(ts_arr).asi8 - center_ms)
        center_idx = int(np.argmin(diffs))

        # Detect sub-movements in this window
        sub_movements = self.detector.detect(df)

        # Find entry candle: last signal aligned with target direction before center
        entry_candle = self._find_entry(df, center_idx, direction)
        entry_ts = df['timestamp'].iloc[entry_candle] if entry_candle is not None else None

        # Score all signals in the lookback window
        entry_score, entry_signals = self._score_entry(df, entry_candle or center_idx, direction)

        # Identify precursor patterns
        precursors = self._identify_precursors(df, center_idx, direction)

        # Summary stats at center candle
        center_row = df.iloc[center_idx]
        summary = {
            'timeframe': timeframe,
            'center_ts': str(center_ts),
            'entry_ts': str(entry_ts) if entry_ts is not None else None,
            'entry_candle_idx': entry_candle,
            'entry_confidence': entry_score,
            'entry_signals': entry_signals,
            'precursors': precursors,
            'n_sub_movements': len(sub_movements),
            'sub_movements': [
                {
                    'ts': str(sm.timestamp),
                    'type': sm.move_type,
                    'dir': sm.direction,
                    'mag': sm.magnitude_pct,
                }
                for sm in sub_movements[:10]
            ],
            'regime': str(center_row.get('regime', 'UNKNOWN')),
            'trend_score': _safe_float(center_row.get('trend_score')),
            'momentum_score': _safe_float(center_row.get('momentum_score')),
            'rsi_14': _safe_float(center_row.get('rsi_14')),
            'adx': _safe_float(center_row.get('adx')),
            'entropy_20': _safe_float(center_row.get('entropy_20')),
            'hurst_60': _safe_float(center_row.get('hurst_60')),
            'volume_ratio': _safe_float(center_row.get('volume_ratio')),
            'atr_pct': _safe_float(center_row.get('atr_pct')),
            'ema_alignment': _safe_float(center_row.get('ema_alignment')),
            'move_readiness': _safe_float(center_row.get('move_readiness')),
        }
        return summary

    def _find_entry(self, df: pd.DataFrame, center_idx: int, direction: str) -> Optional[int]:
        """
        Find the optimal entry candle: scan backwards from center, looking for
        the first candle that aligns with expected direction AND has entry signals.
        """
        best_idx = None
        best_score = 0

        scan_start = max(0, center_idx - 20)
        for i in range(center_idx, scan_start, -1):
            score, _ = self._score_entry(df, i, direction)
            if score > best_score:
                best_score = score
                best_idx = i

        return best_idx

    def _score_entry(
        self, df: pd.DataFrame, idx: int, direction: str
    ) -> Tuple[int, List[str]]:
        """Score how good an entry this candle represents. Returns (score 0-10, signal list)."""
        if idx >= len(df) or idx < 0:
            return 0, []

        row = df.iloc[idx]
        signals = []
        score = 0

        for feat, (feat_dir, desc) in ENTRY_SIGNAL_WEIGHTS.items():
            if feat not in df.columns:
                continue
            val = row[feat]
            if isinstance(val, float) and np.isnan(val):
                continue

            hit = False
            if feat == 'rsi_divergence':
                if direction == 'DOWN' and val < -1:  # bearish divergence
                    hit = True
                elif direction == 'UP' and val > 1:
                    hit = True
            elif feat == 'ema_alignment':
                if direction == 'DOWN' and val <= -1:
                    hit = True
                elif direction == 'UP' and val >= 1:
                    hit = True
            elif feat == 'supertrend_dir':
                if direction == 'DOWN' and val == -1:
                    hit = True
                elif direction == 'UP' and val == 1:
                    hit = True
            elif feat == 'struct_score':
                if direction == 'DOWN' and val <= -1:
                    hit = True
                elif direction == 'UP' and val >= 1:
                    hit = True
            elif feat == 'hurst_60':
                # H < 0.45: mean-reverting (useful for reversal signals)
                # H > 0.55: trending (useful for momentum signals)
                if not np.isnan(float(val)):
                    hit = True  # always score it
            elif feat == 'adx':
                if float(val) > 25:
                    hit = True
            elif feat in ('entropy_squeeze', 'kc_squeeze', 'volume_surge',
                          'bear_engulf', 'bull_engulf', 'pin_bar_bear', 'pin_bar_bull',
                          'breakout_down_20', 'breakout_up_20', 'at_donchian_low', 'at_donchian_high'):
                if float(val) >= 1.0:
                    # Check direction match
                    if feat_dir == 'BOTH' or feat_dir == direction:
                        hit = True
            elif feat in ('macd_hist', 'obv_slope', 'cvd_divergence', 'wpi',
                          'move_readiness'):
                if direction == 'DOWN' and float(val) < 0:
                    hit = True
                elif direction == 'UP' and float(val) > 0:
                    hit = True

            if hit:
                score += 1
                signals.append(f"{desc} ({feat}={round(float(val), 3)})")

        return min(score, 10), signals

    def _identify_precursors(
        self, df: pd.DataFrame, center_idx: int, direction: str
    ) -> List[str]:
        """Human-readable precursor descriptions in the N candles before the event."""
        precursors = []
        lookback = min(10, center_idx)
        window = df.iloc[max(0, center_idx - lookback): center_idx]

        # Volume trend
        if 'volume' in window.columns and len(window) >= 3:
            vol_slope = (window['volume'].iloc[-1] - window['volume'].iloc[0]) / (window['volume'].iloc[0] + 1e-10)
            if vol_slope < -0.2:
                precursors.append("Volume declining steadily (-{:.0f}%)".format(abs(vol_slope) * 100))
            elif vol_slope > 0.5:
                precursors.append("Volume increasing (+{:.0f}%)".format(vol_slope * 100))

        # Entropy trend
        if 'entropy_20' in window.columns:
            ent = window['entropy_20'].dropna()
            if len(ent) >= 3:
                ent_slope = ent.iloc[-1] - ent.iloc[0]
                if ent_slope > 0.1:
                    precursors.append(f"Entropy rising (chaos building): {ent.iloc[-1]:.3f}")
                elif ent_slope < -0.1:
                    precursors.append(f"Entropy falling (order forming): {ent.iloc[-1]:.3f}")

        # Hurst
        if 'hurst_60' in window.columns:
            h = window['hurst_60'].dropna()
            if len(h) > 0:
                h_val = h.iloc[-1]
                if h_val < 0.45:
                    precursors.append(f"Hurst < 0.45 (mean-reverting regime): H={h_val:.3f}")
                elif h_val > 0.55:
                    precursors.append(f"Hurst > 0.55 (trending regime): H={h_val:.3f}")

        # RSI extremes
        if 'rsi_14' in window.columns:
            rsi = window['rsi_14'].dropna()
            if len(rsi) > 0:
                r = rsi.iloc[-1]
                if r > 70:
                    precursors.append(f"RSI overbought ({r:.1f})")
                elif r < 30:
                    precursors.append(f"RSI oversold ({r:.1f})")

        # Squeeze
        if 'kc_squeeze' in window.columns:
            sq = window['kc_squeeze'].sum()
            if sq >= 3:
                precursors.append(f"Keltner squeeze active for {int(sq)} candles")

        # EMA cross
        if 'ema_alignment' in window.columns:
            align_vals = window['ema_alignment']
            if align_vals.iloc[-1] != align_vals.iloc[0]:
                precursors.append(f"EMA alignment changed: {int(align_vals.iloc[0])} → {int(align_vals.iloc[-1])}")

        # Structure
        if 'struct_score' in window.columns:
            sc = window['struct_score'].iloc[-1]
            if sc <= -1:
                precursors.append("Market structure bearish (LH/LL pattern)")
            elif sc >= 1:
                precursors.append("Market structure bullish (HH/HL pattern)")

        # OBV divergence
        if 'obv_slope' in window.columns and 'close' in window.columns:
            obv_dir = np.sign(window['obv_slope'].mean())
            price_dir = np.sign(window['close'].iloc[-1] - window['close'].iloc[0])
            if obv_dir != price_dir and price_dir != 0:
                precursors.append("OBV divergence detected (smart money disagreeing with price)")

        return precursors


def _safe_float(val) -> Optional[float]:
    try:
        v = float(val)
        return None if np.isnan(v) else round(v, 4)
    except (TypeError, ValueError):
        return None
