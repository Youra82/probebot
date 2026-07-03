"""
Live Scanner — erkennt aktuelle Marktbewegungen und erklärt ihre Ursachen.

Fließt durch:
  1. Lade letzte N Kerzen (genug für Feature-Berechnung)
  2. Prüfe ob die LETZTE(N) Kerze(n) eine signifikante Bewegung darstellen
  3. Erkläre WARUM: welche Features waren vorher abnormal?
  4. Vergleiche mit historischen DB-Einträgen (cosine similarity)
  5. Schätze weiteren Verlauf basierend auf ähnlichen historischen Events
  6. MTF Drill-Down: wo stehen wir JETZT gerade?
  7. Sende alles per Telegram
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..data.loader import DataLoader
from ..features.engine import compute_all_features, feature_vector
from ..detection.detector import MovementDetector, Movement
from ..forensics.database import ForensicsDB
from ..forensics.miner import PatternMiner
from ..forensics.drill_down import DrillDownEngine


class LiveScanner:
    def __init__(
        self,
        loader: DataLoader,
        db: ForensicsDB,
        drill_engine: DrillDownEngine,
        min_move_pct: float = 1.5,
        lookback_candles: int = 300,
        recent_candles: int = 5,
    ):
        self.loader = loader
        self.db = db
        self.drill_engine = drill_engine
        self.detector = MovementDetector(atr_impulse=1.5, breakout_bars=20)
        self.miner = PatternMiner(db, lookback=10)
        self.min_move_pct = min_move_pct
        self.lookback_candles = lookback_candles
        self.recent_candles = recent_candles

    def scan(
        self,
        symbol: str,
        timeframe: str,
        drill_down_tfs: list = None,
    ) -> list:
        """
        Haupt-Scan: lädt aktuelle Daten, prüft letzte Kerzen, gibt Liste von Alerts zurück.
        """
        print(f"  [live] Lade letzte {self.lookback_candles} {timeframe}-Kerzen für {symbol}...")
        df_raw = self._fetch_recent(symbol, timeframe)
        if df_raw is None or len(df_raw) < 50:
            print(f"  [live] Zu wenig Daten ({len(df_raw) if df_raw is not None else 0} Kerzen)")
            return []

        print(f"  [live] Berechne Features...")
        df = compute_all_features(df_raw, min_candles=50, timeframe=timeframe)

        # Alle Bewegungen finden
        all_movements = self.detector.detect(df)

        # Nur Bewegungen in den LETZTEN N Kerzen betrachten
        recent_threshold = len(df) - self.recent_candles
        recent_movements = [
            m for m in all_movements
            if m.idx >= recent_threshold and abs(m.magnitude_pct) >= self.min_move_pct
        ]

        if not recent_movements:
            print(f"  [live] Keine signifikante Bewegung in den letzten {self.recent_candles} Kerzen.")
            return []

        print(f"  [live] {len(recent_movements)} aktuelle Bewegung(en) erkannt!")
        alerts = []
        for m in recent_movements:
            print(f"  [live] Analysiere: {m.move_type} {m.direction} {m.magnitude_pct:+.2f}%")
            alert = self._analyze_movement(df, m, symbol, timeframe, drill_down_tfs)
            # include df + all movements so alerter can render charts
            alert['df'] = df
            alert['all_movements'] = all_movements
            alerts.append(alert)

        return alerts

    def _analyze_movement(
        self,
        df: pd.DataFrame,
        movement: Movement,
        symbol: str,
        timeframe: str,
        drill_down_tfs: list,
    ) -> dict:
        idx = movement.idx

        # ── 1. Kausal-Analyse: Was war VOR dieser Bewegung abnormal? ──────────
        cause = self._explain_cause(df, idx, movement.direction, symbol, timeframe)

        # ── 2. Ähnliche historische Events finden ────────────────────────────
        similar = self.miner.find_similar(
            df, idx, symbol, timeframe,
            move_type=movement.move_type, top_n=5
        )

        # ── 3. Prognose aus ähnlichen Events ─────────────────────────────────
        outlook = _build_outlook(similar, movement.direction)

        # ── 4. MTF Drill-Down ab aktuellem Zeitpunkt ─────────────────────────
        dd = {}
        if drill_down_tfs:
            print(f"  [live] MTF Drill-Down...")
            dd = self.drill_engine.drill(
                symbol, movement, movement.direction, start_tf=timeframe
            )

        # ── 5. Aktueller Markt-Zustand (letzte Kerze) ────────────────────────
        current_state = _current_state(df)

        return {
            'movement': movement,
            'cause': cause,
            'similar': similar,
            'outlook': outlook,
            'drill_down': dd,
            'current_state': current_state,
            'symbol': symbol,
            'timeframe': timeframe,
            'scanned_at': datetime.utcnow().isoformat(),
        }

    def _explain_cause(
        self,
        df: pd.DataFrame,
        move_idx: int,
        direction: str,
        symbol: str,
        timeframe: str,
    ) -> list:
        """
        Erklärt WARUM diese Bewegung gerade passiert ist.
        Vergleicht die Feature-Werte der letzten N Kerzen mit:
          a) dem historischen Durchschnitt dieser Features
          b) den bekannten DB-Commonalities für diesen Bewegungstyp
        Gibt geordnete Liste von Ursachen zurück (wichtigste zuerst).
        """
        causes = []
        lookback = min(10, move_idx)
        window = df.iloc[max(0, move_idx - lookback): move_idx]
        last = df.iloc[move_idx]

        # Hole bekannte Prädiktoren aus DB
        known_predictors = {}
        for mtype in ['BREAKDOWN', 'BREAKOUT_UP', 'IMPULSE_DOWN', 'IMPULSE_UP',
                      'REVERSAL_DOWN', 'REVERSAL_UP', 'SQUEEZE_RELEASE_DOWN',
                      'SQUEEZE_RELEASE_UP', 'ACCELERATION_DOWN', 'ACCELERATION_UP']:
            db_rows = self.db.get_commonalities(symbol, timeframe, mtype,
                                                direction=direction, min_t_stat=2.0, top_n=10)
            for r in db_rows:
                feat = r['feature']
                if feat not in known_predictors or abs(r['t_statistic']) > abs(known_predictors[feat]['t_statistic']):
                    known_predictors[feat] = r

        # ── Entropy-Analyse ───────────────────────────────────────────────────
        if 'entropy_20' in window.columns:
            ent = window['entropy_20'].dropna()
            if len(ent) >= 3:
                ent_slope = ent.iloc[-1] - ent.iloc[0]
                ent_val = ent.iloc[-1]
                ent_mean_all = df['entropy_20'].dropna().mean()
                if ent_val > ent_mean_all * 1.15:
                    causes.append({
                        'priority': 1,
                        'category': 'Chaos/Entropy',
                        'text': f"Entropy erhöht ({ent_val:.3f} vs Ø {ent_mean_all:.3f}) — Markt war ungeordnet, Energie hat sich aufgebaut",
                        'feature': 'entropy_20',
                        'value': round(ent_val, 4),
                        'baseline': round(ent_mean_all, 4),
                    })
                elif ent_slope < -0.1:
                    causes.append({
                        'priority': 2,
                        'category': 'Entropy Squeeze',
                        'text': f"Entropy fiel vor Move ({ent.iloc[0]:.3f}→{ent_val:.3f}) — Markt ordnete sich, Richtungsentscheid stand bevor",
                        'feature': 'entropy_20',
                        'value': round(ent_val, 4),
                        'baseline': round(ent_mean_all, 4),
                    })

        # ── Hurst-Regime ──────────────────────────────────────────────────────
        if 'hurst_60' in window.columns:
            h = window['hurst_60'].dropna()
            if len(h) > 0:
                h_val = h.iloc[-1]
                if h_val < 0.42:
                    causes.append({
                        'priority': 1,
                        'category': 'Regime',
                        'text': f"Hurst {h_val:.3f} — Mean-Reverting Regime: Trend war überdehnt, Korrektur war fällig",
                        'feature': 'hurst_60',
                        'value': round(h_val, 4),
                        'baseline': 0.5,
                    })
                elif h_val > 0.58:
                    causes.append({
                        'priority': 2,
                        'category': 'Regime',
                        'text': f"Hurst {h_val:.3f} — Trending Regime: Momentum hatte klare Richtung, Move ist Trend-Fortsetzung",
                        'feature': 'hurst_60',
                        'value': round(h_val, 4),
                        'baseline': 0.5,
                    })

        # ── RSI-Divergenz ─────────────────────────────────────────────────────
        if 'rsi_divergence' in df.columns:
            rsi_div = float(df['rsi_divergence'].iloc[move_idx])
            rsi_val = float(df['rsi_14'].iloc[move_idx]) if 'rsi_14' in df.columns else None
            if direction == 'DOWN' and rsi_div < -1:
                causes.append({
                    'priority': 1,
                    'category': 'RSI Divergenz',
                    'text': f"Bärische RSI-Divergenz (Preis stieg, RSI fiel) — versteckter Verkaufsdruck schon vorher sichtbar",
                    'feature': 'rsi_divergence',
                    'value': round(rsi_div, 3),
                    'baseline': 0,
                })
            elif direction == 'UP' and rsi_div > 1:
                causes.append({
                    'priority': 1,
                    'category': 'RSI Divergenz',
                    'text': f"Bullische RSI-Divergenz (Preis fiel, RSI stieg) — versteckter Kaufdruck war schon da",
                    'feature': 'rsi_divergence',
                    'value': round(rsi_div, 3),
                    'baseline': 0,
                })
            if rsi_val is not None:
                if rsi_val > 75 and direction == 'DOWN':
                    causes.append({
                        'priority': 2,
                        'category': 'RSI Überkauft',
                        'text': f"RSI war überkauft ({rsi_val:.1f}) — Gewinnmitnahmen und Shorts wurden aktiv",
                        'feature': 'rsi_14',
                        'value': round(rsi_val, 2),
                        'baseline': 50,
                    })
                elif rsi_val < 25 and direction == 'UP':
                    causes.append({
                        'priority': 2,
                        'category': 'RSI Überverkauft',
                        'text': f"RSI war überverkauft ({rsi_val:.1f}) — Schnäppchenkäufer und Short-Cover aktiv",
                        'feature': 'rsi_14',
                        'value': round(rsi_val, 2),
                        'baseline': 50,
                    })

        # ── EMA-Struktur ──────────────────────────────────────────────────────
        if 'ema_alignment' in df.columns:
            align_now = float(df['ema_alignment'].iloc[move_idx])
            align_before = window['ema_alignment'].iloc[0] if len(window) > 0 else align_now
            if align_now != align_before:
                desc = 'bearish' if direction == 'DOWN' else 'bullisch'
                causes.append({
                    'priority': 2,
                    'category': 'EMA Struktur',
                    'text': f"EMA-Stack kippte auf {desc} ({int(align_before):+d} → {int(align_now):+d}) — institutionelle Trendbestätigung",
                    'feature': 'ema_alignment',
                    'value': round(align_now, 0),
                    'baseline': 0,
                })
            elif direction == 'DOWN' and align_now <= -2:
                causes.append({
                    'priority': 3,
                    'category': 'EMA Struktur',
                    'text': f"EMA-Stack vollständig bearish ({int(align_now)}/−3) — starkes Trendumfeld begünstigt Short",
                    'feature': 'ema_alignment',
                    'value': round(align_now, 0),
                    'baseline': 0,
                })

        # ── Volume ────────────────────────────────────────────────────────────
        if 'volume_ratio' in df.columns:
            vol_ratio = float(df['volume_ratio'].iloc[move_idx])
            if vol_ratio > 2.5:
                causes.append({
                    'priority': 1,
                    'category': 'Volumen',
                    'text': f"Volumen-Spike beim Move ({vol_ratio:.1f}× Durchschnitt) — institutioneller Markteingriff",
                    'feature': 'volume_ratio',
                    'value': round(vol_ratio, 2),
                    'baseline': 1.0,
                })
            if 'vol_declining_3' in df.columns and float(df['vol_declining_3'].iloc[max(0, move_idx-1)]) > 0:
                causes.append({
                    'priority': 2,
                    'category': 'Volumen',
                    'text': "Volume sank 3+ Kerzen vor dem Move — Erschöpfung / mangelnde Käufer/Verkäufer begünstigt Umkehr",
                    'feature': 'vol_declining_3',
                    'value': 1.0,
                    'baseline': 0,
                })

        # ── CVD Divergenz ─────────────────────────────────────────────────────
        if 'cvd_divergence' in df.columns:
            cvd_div = float(df['cvd_divergence'].iloc[move_idx])
            if (direction == 'DOWN' and cvd_div < -1) or (direction == 'UP' and cvd_div > 1):
                side = 'Smart Money hat schon verkauft' if direction == 'DOWN' else 'Smart Money hat schon gekauft'
                causes.append({
                    'priority': 1,
                    'category': 'CVD / Order Flow',
                    'text': f"CVD-Divergenz erkannt — {side} (Order Flow ging gegen Preis)",
                    'feature': 'cvd_divergence',
                    'value': round(cvd_div, 3),
                    'baseline': 0,
                })

        # ── Squeeze ───────────────────────────────────────────────────────────
        if 'kc_squeeze' in window.columns:
            squeeze_count = int(window['kc_squeeze'].sum())
            if squeeze_count >= 2:
                causes.append({
                    'priority': 1,
                    'category': 'Volatilitäts-Squeeze',
                    'text': f"Keltner-Squeeze war {squeeze_count} Kerzen aktiv — aufgestaute Energie entlud sich jetzt",
                    'feature': 'kc_squeeze',
                    'value': float(squeeze_count),
                    'baseline': 0,
                })

        # ── WPI / Wick Pressure ───────────────────────────────────────────────
        if 'memory_pressure' in df.columns:
            mp = float(df['memory_pressure'].iloc[move_idx])
            if direction == 'DOWN' and mp < -0.3:
                causes.append({
                    'priority': 2,
                    'category': 'Wick-Druck',
                    'text': f"Memory Pressure negativ ({mp:.3f}) — akkumulierter Verkaufsdruck aus Dochten",
                    'feature': 'memory_pressure',
                    'value': round(mp, 4),
                    'baseline': 0,
                })
            elif direction == 'UP' and mp > 0.3:
                causes.append({
                    'priority': 2,
                    'category': 'Wick-Druck',
                    'text': f"Memory Pressure positiv ({mp:.3f}) — akkumulierter Kaufdruck aus Dochten",
                    'feature': 'memory_pressure',
                    'value': round(mp, 4),
                    'baseline': 0,
                })

        # ── Market Structure ──────────────────────────────────────────────────
        if 'breakout_down_20' in df.columns and float(df['breakout_down_20'].iloc[move_idx]) > 0:
            causes.append({
                'priority': 1,
                'category': 'Struktur-Bruch',
                'text': "20-Bar Unterstützung gebrochen — Stop-Loss Cascade ausgelöst",
                'feature': 'breakout_down_20',
                'value': 1.0,
                'baseline': 0,
            })
        if 'breakout_up_20' in df.columns and float(df['breakout_up_20'].iloc[move_idx]) > 0:
            causes.append({
                'priority': 1,
                'category': 'Struktur-Bruch',
                'text': "20-Bar Widerstand gebrochen — Stop-Loss Buys + FOMO ausgelöst",
                'feature': 'breakout_up_20',
                'value': 1.0,
                'baseline': 0,
            })
        if 'bear_engulf' in df.columns and float(df['bear_engulf'].iloc[move_idx]) > 0:
            causes.append({
                'priority': 2,
                'category': 'Kerzen-Muster',
                'text': "Bärische Engulfing-Kerze — komplette Ablehnung des vorherigen Anstiegs",
                'feature': 'bear_engulf',
                'value': 1.0,
                'baseline': 0,
            })
        if 'bull_engulf' in df.columns and float(df['bull_engulf'].iloc[move_idx]) > 0:
            causes.append({
                'priority': 2,
                'category': 'Kerzen-Muster',
                'text': "Bullische Engulfing-Kerze — kompletter Absorb des vorherigen Rückgangs",
                'feature': 'bull_engulf',
                'value': 1.0,
                'baseline': 0,
            })

        # ── DB-bekannte Prädiktoren ────────────────────────────────────────────
        for feat, row in known_predictors.items():
            if feat in df.columns and not any(c['feature'] == feat for c in causes):
                current_val = float(df[feat].iloc[move_idx])
                if np.isnan(current_val):
                    continue
                expected_dir = 'erhöht' if row['t_statistic'] > 0 else 'erniedrigt'
                actual_vs_base = current_val - row['mean_all']
                if abs(actual_vs_base) > row['std_all'] * 0.5:
                    causes.append({
                        'priority': 3,
                        'category': 'Historisches Muster',
                        'text': (
                            f"{feat} war {expected_dir} ({current_val:.4f} vs Ø {row['mean_all']:.4f}) — "
                            f"laut DB in {row['predictive_pct']:.0f}% ähnlicher Events so"
                        ),
                        'feature': feat,
                        'value': round(current_val, 4),
                        'baseline': round(row['mean_all'], 4),
                    })

        # Sortiere nach Priorität (1=wichtigste)
        causes.sort(key=lambda c: c['priority'])
        return causes[:10]

    def _fetch_recent(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Lädt die letzten lookback_candles Kerzen bis jetzt."""
        from ..data.loader import TIMEFRAME_MINUTES
        tf_min = TIMEFRAME_MINUTES.get(timeframe, 60)
        end_dt = datetime.utcnow()
        start_dt = end_dt - timedelta(minutes=tf_min * self.lookback_candles)
        start_str = start_dt.strftime('%Y-%m-%d')
        try:
            return self.loader.fetch(symbol, timeframe, start_str)
        except Exception as e:
            print(f"  [live] Fehler beim Datenladen: {e}")
            return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _build_outlook(similar: list, direction: str) -> dict:
    """Prognose-Statistik basierend auf ähnlichen historischen Events."""
    if not similar:
        return {}

    magnitudes = [abs(s.get('magnitude_pct', 0)) for s in similar]
    directions = [s.get('direction', '') for s in similar]
    same_dir = sum(1 for d in directions if d == direction)
    hit_rate = same_dir / len(similar) if similar else 0

    return {
        'n_similar': len(similar),
        'hit_rate_pct': round(hit_rate * 100, 1),
        'median_magnitude': round(float(np.median(magnitudes)), 2) if magnitudes else 0,
        'max_magnitude': round(float(np.max(magnitudes)), 2) if magnitudes else 0,
        'best_match': similar[0] if similar else None,
        'summary': (
            f"Von {len(similar)} ähnlichen Events: {same_dir}× gleiche Richtung "
            f"({hit_rate:.0%} Hit-Rate), medianer Move: {np.median(magnitudes):.1f}%"
        ) if similar else "Keine historischen Vergleichsdaten",
    }


def _current_state(df: pd.DataFrame) -> dict:
    """Snapshot des aktuellen Markt-Zustands (letzte Kerze)."""
    row = df.iloc[-1]
    state = {}
    for feat in ['regime', 'trend_score', 'momentum_score', 'move_readiness',
                 'rsi_14', 'adx', 'entropy_20', 'hurst_60', 'atr_pct',
                 'volume_ratio', 'ema_alignment', 'supertrend_dir',
                 'macd_hist', 'bb_position', 'wpi', 'memory_pressure',
                 'phase_regime', 'dna_code']:
        if feat in df.columns:
            val = row[feat]
            state[feat] = str(val) if isinstance(val, str) else (
                float(val) if not (isinstance(val, float) and np.isnan(val)) else None
            )
    state['close'] = float(df['close'].iloc[-1])
    state['timestamp'] = str(df['timestamp'].iloc[-1]) if 'timestamp' in df.columns else ''
    return state
