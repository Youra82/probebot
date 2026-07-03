"""
Multi-timeframe drill-down engine.

For a movement detected at a high timeframe (e.g. 1D), zooms progressively into
lower timeframes (4H → 1H → 15m → 5m → 1m) to:
  1. Identify the structural precursors at each level
  2. Find the earliest detectable signal
  3. Locate the optimal entry point (with rationale)
  4. Calculate SL and TP levels (structure + ATR + Fibonacci)
  5. Characterize what the market looked like at each zoom level

Fetching is lazy — each timeframe is only loaded when actually reached in the drill.
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
        verbose: bool = True,
    ):
        self.loader = loader
        self.chain = timeframe_chain or DRILL_DOWN_CHAIN
        self.candles_before = candles_before
        self.candles_after = candles_after
        self.detector = MovementDetector()
        self.verbose = verbose

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
            if self.verbose:
                print(f"  [drill-down] zooming into {tf}...")
            try:
                level_result = self._analyze_level(symbol, tf, center_ts, target_direction)
                results[tf] = level_result

                # If we have a high-confidence entry at this level, we can stop
                if level_result.get('entry_confidence', 0) >= 8:
                    results[tf]['_stopped_here'] = True
                    if self.verbose:
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
            df = compute_all_features(df_raw, min_candles=50, verbose=self.verbose, timeframe=timeframe)
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
        entry_idx  = entry_candle if entry_candle is not None else center_idx
        entry_row  = df.iloc[entry_idx]
        entry_price = _safe_float(entry_row.get('close')) or _safe_float(center_row.get('close')) or 0.0

        # SL/TP analysis
        sl_tp = self._calculate_sl_tp(df, entry_idx, direction, entry_price)

        summary = {
            'timeframe': timeframe,
            'center_ts': str(center_ts),
            'entry_ts': str(entry_ts) if entry_ts is not None else None,
            'entry_candle_idx': entry_candle,
            'entry_price': round(float(entry_price), 6) if entry_price else None,
            'entry_confidence': entry_score,
            'entry_signals': entry_signals,
            'precursors': precursors,
            'sl_tp': sl_tp,
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

    def _calculate_sl_tp(
        self,
        df: pd.DataFrame,
        entry_idx: int,
        direction: str,
        entry_price: float,
        atr_mult: float = 1.5,
        rr_targets: tuple = (1.5, 2.5, 4.0),
    ) -> dict:
        """
        Berechnet SL und TP auf dem aktuellen Timeframe-Zoom.

        Drei Ansätze:
          1. ATR-basiert    — SL = entry ± ATR × atr_mult
          2. Struktur-basiert — SL hinter letztem Swing High/Low
          3. Fibonacci      — TP basierend auf dem vorherigen Swing

        Nur der Timeframe-eigene Datenschnitt wird verwendet —
        kein separater API-Call nötig.
        """
        if not entry_price or entry_price <= 0 or entry_idx < 0:
            return {}

        try:
            window_start = max(0, entry_idx - 50)
            window = df.iloc[window_start: entry_idx + 1]

            if len(window) < 5:
                return {}

            # ── ATR ──────────────────────────────────────────────────────────
            atr_col = 'atr_14' if 'atr_14' in df.columns else None
            if atr_col:
                atr_val = _safe_float(df.iloc[entry_idx].get(atr_col)) or 0.0
            else:
                # Manuelle Schätzung: avg(H-L) der letzten 14 Kerzen
                if 'high' in window.columns and 'low' in window.columns:
                    atr_val = float(np.mean(
                        (window['high'] - window['low']).values[-14:]
                    ))
                else:
                    atr_val = entry_price * 0.015  # 1.5% Fallback

            sl_atr = (entry_price - atr_val * atr_mult
                      if direction == 'UP'
                      else entry_price + atr_val * atr_mult)

            # ── Swing High / Low (Struktur-SL) ────────────────────────────────
            swing_sl, swing_ts = self._find_swing_sl(window, direction, entry_idx, window_start)

            # ── Kombinierter SL (das engere / konservativere Level) ───────────
            if swing_sl is not None:
                if direction == 'UP':
                    # Long: SL unter Entry → höherer Wert = enger am Entry
                    sl_used  = max(sl_atr, swing_sl)
                    sl_label = 'ATR' if sl_atr >= swing_sl else 'Struktur'
                else:
                    # Short: SL über Entry → niedrigerer Wert = enger am Entry
                    sl_used  = min(sl_atr, swing_sl)
                    sl_label = 'ATR' if sl_atr <= swing_sl else 'Struktur'
            else:
                sl_used  = sl_atr
                sl_label = 'ATR'

            sl_dist = abs(entry_price - sl_used)
            if sl_dist < 1e-10:
                return {}

            sl_pct  = round(sl_dist / entry_price * 100, 3)

            # ── TP-Levels (R:R) ───────────────────────────────────────────────
            tp_levels = []
            for rr in rr_targets:
                if direction == 'UP':
                    tp = entry_price + sl_dist * rr
                else:
                    tp = entry_price - sl_dist * rr
                tp_pct = round(sl_dist * rr / entry_price * 100, 3)
                tp_levels.append({
                    'rr':     rr,
                    'price':  round(tp, 6),
                    'pct':    tp_pct,
                    'label':  f'TP{int(rr)} (1:{rr} R:R)',
                })

            # ── Fibonacci-TPs ─────────────────────────────────────────────────
            fib_tps = self._fibonacci_tps(window, direction, entry_price)

            # ── Nearest S/R als Zonen ─────────────────────────────────────────
            sr_levels = self._find_sr_levels(window, entry_price, direction)

            return {
                'direction':   direction,
                'entry_price': round(entry_price, 6),
                'sl': {
                    'price':      round(sl_used, 6),
                    'atr_price':  round(sl_atr, 6),
                    'swing_price':round(swing_sl, 6) if swing_sl is not None else None,
                    'swing_ts':   swing_ts,
                    'used_method':sl_label,
                    'distance_pct':sl_pct,
                    'atr_value':  round(atr_val, 6),
                    'atr_mult':   atr_mult,
                },
                'tp': tp_levels,
                'fibonacci': fib_tps,
                'sr_zones': sr_levels,
                'risk_reward_summary': (
                    f"SL -{sl_pct:.2f}%  |  "
                    f"TP1 +{tp_levels[0]['pct']:.2f}%  "
                    f"TP2 +{tp_levels[1]['pct']:.2f}%  "
                    f"TP3 +{tp_levels[2]['pct']:.2f}%"
                    if len(tp_levels) >= 3 else f"SL -{sl_pct:.2f}%"
                ),
            }
        except Exception as e:
            return {'error': str(e)}

    def _find_swing_sl(
        self,
        window: pd.DataFrame,
        direction: str,
        entry_idx: int,
        window_start: int,
    ):
        """
        Findet den letzten signifikanten Swing Low (für Long) oder
        Swing High (für Short) im Lookback-Fenster.
        Gibt (price, timestamp_str) zurück.
        """
        if len(window) < 5:
            return None, None

        col = 'low' if direction == 'UP' else 'high'
        if col not in window.columns:
            col = 'close'

        prices = window[col].values
        n = len(prices)

        # Pivot-Erkennung: lokales Minimum/Maximum (Fenster ±2 Kerzen)
        pivot_size = 2
        best_price = None
        best_ts    = None

        for i in range(pivot_size, n - pivot_size):
            p = prices[i]
            window_prices = prices[i - pivot_size: i + pivot_size + 1]
            if direction == 'UP':
                # Suche Swing Low: tiefster Punkt in Umgebung
                if p == min(window_prices):
                    if best_price is None or p > best_price:  # tiefstes der Kandidaten
                        best_price = p
                        ts_col = window.columns[0] if 'timestamp' not in window.columns else 'timestamp'
                        if 'timestamp' in window.columns:
                            best_ts = str(window['timestamp'].iloc[i])[:16]
            else:
                # Suche Swing High: höchster Punkt
                if p == max(window_prices):
                    if best_price is None or p < best_price:  # höchstes der Kandidaten
                        best_price = p
                        if 'timestamp' in window.columns:
                            best_ts = str(window['timestamp'].iloc[i])[:16]

        return best_price, best_ts

    def _fibonacci_tps(
        self,
        window: pd.DataFrame,
        direction: str,
        entry_price: float,
    ) -> list:
        """
        Fibonacci-Extension TPs basierend auf dem letzten Swing im Fenster.
        """
        if len(window) < 10:
            return []

        try:
            if 'high' in window.columns and 'low' in window.columns:
                swing_high = float(window['high'].max())
                swing_low  = float(window['low'].min())
            else:
                swing_high = float(window['close'].max())
                swing_low  = float(window['close'].min())

            swing_range = swing_high - swing_low
            if swing_range < 1e-10:
                return []

            fibs = [0.618, 1.0, 1.272, 1.618, 2.618]
            result = []
            for fib in fibs:
                if direction == 'UP':
                    tp = swing_low + swing_range * fib
                else:
                    tp = swing_high - swing_range * fib

                if direction == 'UP' and tp > entry_price:
                    pct = (tp - entry_price) / entry_price * 100
                    result.append({'fib': fib, 'price': round(tp, 6), 'pct': round(pct, 3)})
                elif direction == 'DOWN' and tp < entry_price:
                    pct = (entry_price - tp) / entry_price * 100
                    result.append({'fib': fib, 'price': round(tp, 6), 'pct': round(pct, 3)})

            return result[:4]
        except Exception:
            return []

    def _find_sr_levels(
        self,
        window: pd.DataFrame,
        entry_price: float,
        direction: str,
        max_levels: int = 4,
    ) -> list:
        """
        Findet die nächsten Support/Resistance-Levels im Fenster.
        Nur Levels die als TP infrage kommen (in Tradrichtung vor Entry).
        """
        if len(window) < 10:
            return []

        try:
            close = window['close'].values if 'close' in window.columns else None
            if close is None:
                return []

            # Cluster nahe Preispunkte als S/R-Zonen
            pivot_size = 3
            levels = []
            for i in range(pivot_size, len(close) - pivot_size):
                c = close[i]
                neighborhood = close[i - pivot_size: i + pivot_size + 1]
                # Local max → Resistance
                if c == max(neighborhood) and c > entry_price and direction == 'UP':
                    levels.append({'price': round(float(c), 6), 'type': 'resistance',
                                   'pct': round((c - entry_price) / entry_price * 100, 3)})
                # Local min → Support
                elif c == min(neighborhood) and c < entry_price and direction == 'DOWN':
                    levels.append({'price': round(float(c), 6), 'type': 'support',
                                   'pct': round((entry_price - c) / entry_price * 100, 3)})

            # Deduplizieren (Levels mit < 0.3% Abstand zusammenfassen)
            merged = []
            for lv in sorted(levels, key=lambda x: x['price']):
                if not merged or abs(lv['price'] - merged[-1]['price']) / merged[-1]['price'] > 0.003:
                    merged.append(lv)

            # Nächste Levels in Tradrichtung
            if direction == 'UP':
                merged = [l for l in merged if l['price'] > entry_price]
                merged.sort(key=lambda x: x['price'])
            else:
                merged = [l for l in merged if l['price'] < entry_price]
                merged.sort(key=lambda x: -x['price'])

            return merged[:max_levels]
        except Exception:
            return []

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
