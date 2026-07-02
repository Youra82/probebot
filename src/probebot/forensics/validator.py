"""
Out-of-Sample Validator — strenge 70/30 Datentrennung.

Ablauf:
  Training (70%): Signale lernen via Korrelation + Mining
  Test    (30%): Signale validieren — diese Daten wurden NIE für das Lernen verwendet

Für jeden Bewegungstyp wird gemessen:
  - Recall:    In wie vielen Test-Events war das Signal vorher aktiv?
  - Precision: Wenn das Signal feuert — folgt wirklich die Bewegung?
  - Degradation: Wie stark fällt die Hit-Rate von In-Sample auf Out-of-Sample?
"""
import numpy as np
from typing import Optional


class OutOfSampleValidator:
    def __init__(self, lookback: int = 3, signal_window: int = 2):
        """
        lookback:      Wie viele Kerzen vor dem Event auf Signale prüfen
        signal_window: Wie viele Kerzen nach dem Signal auf ein Event warten
        """
        self.lookback      = lookback
        self.signal_window = signal_window

    def validate(
        self,
        df,              # Vollständiger DataFrame (alle Features bereits berechnet)
        split_idx: int,  # Index wo Train endet / Test beginnt
        movements_test:  list,  # Bewegungen NUR aus dem Test-Zeitraum
        correlations:    dict,  # Aus TRAINING gelernte Korrelationen
    ) -> dict:
        """
        Validiert Training-Signale gegen den unsichtbaren Test-Zeitraum.
        df wird nur für Pre-Event-Features genutzt (rolling = kein Look-Ahead).
        """
        results = {}

        for mtype, ranked_or_dict in correlations.items():
            # Support both old (list) and new (dict with 'rows') format
            ranked = ranked_or_dict.get('rows', ranked_or_dict) if isinstance(ranked_or_dict, dict) else ranked_or_dict
            # Nur starke Trainings-Signale als Validierungsbedingungen
            must_have = [
                r for r in ranked
                if abs(r.get('t_statistic', 0)) >= 3.5
                and r.get('predictive_pct', 0) >= 35
            ][:6]
            if not must_have:
                continue

            test_mvts = [m for m in movements_test if m.move_type == mtype]
            n_test    = len(test_mvts)

            # ── A) RECALL: Bei bekannten Test-Events — war Signal vorher aktiv? ──
            recall_hits = 0
            recall_details = []
            for m in test_mvts:
                conditions_met = 0
                for cond in must_have:
                    # Prüfe 1..lookback Kerzen VOR dem Event
                    for pre in range(1, self.lookback + 1):
                        pre_idx = m.idx - pre
                        if pre_idx < 0 or pre_idx >= len(df):
                            continue
                        met = self._check_condition(df, pre_idx, cond)
                        if met:
                            conditions_met += 1
                            break
                coverage = conditions_met / len(must_have) if must_have else 0
                recall_details.append({
                    'timestamp': str(m.timestamp)[:16],
                    'magnitude_pct': round(m.magnitude_pct, 2),
                    'conditions_coverage': round(coverage * 100),
                })
                if coverage >= 0.5:  # mind. 50% der Bedingungen erfüllt
                    recall_hits += 1

            recall_pct = round(recall_hits / n_test * 100) if n_test else 0

            # ── B) PRECISION: Wenn Signal feuert — folgt die Bewegung? ──────────
            signal_fires      = 0
            signal_confirmed  = 0
            test_event_indices = {m.idx for m in test_mvts}

            for idx in range(split_idx, min(len(df), split_idx + len(df))):
                # Signalbedingungen prüfen (alle Trainings-must_have)
                conditions_met = sum(
                    1 for cond in must_have
                    if self._check_condition(df, idx, cond)
                )
                if conditions_met / len(must_have) < 0.5:
                    continue
                signal_fires += 1
                # Folgt in den nächsten signal_window Kerzen ein Event dieses Typs?
                if any(idx + 1 <= ei <= idx + self.signal_window
                       for ei in test_event_indices):
                    signal_confirmed += 1

            precision_pct = round(signal_confirmed / signal_fires * 100) if signal_fires else 0

            # ── C) In-Sample Hit-Rate aus Trainings-Korrelationen ────────────────
            # Gewichteter Durchschnitt der must_have Hit-Rates (aus Training)
            insample_hit = round(
                sum(r.get('predictive_pct', 0) for r in must_have) / len(must_have)
            ) if must_have else 0

            # ── D) Degradation ──────────────────────────────────────────────────
            degradation = insample_hit - recall_pct
            reliability = _reliability_label(recall_pct, degradation, precision_pct)

            # ── E) Bestes & schlechtestes Signal ───────────────────────────────
            cond_performance = []
            for cond in must_have:
                feat = cond['feature']
                hits = 0
                for m in test_mvts:
                    for pre in range(1, self.lookback + 1):
                        pre_idx = m.idx - pre
                        if 0 <= pre_idx < len(df):
                            if self._check_condition(df, pre_idx, cond):
                                hits += 1
                                break
                cond_performance.append({
                    'feature': feat,
                    'train_hit': round(cond.get('predictive_pct', 0), 1),
                    'test_hit': round(hits / n_test * 100, 1) if n_test else 0,
                    't_statistic': round(cond.get('t_statistic', 0), 2),
                })

            results[mtype] = {
                'n_train':         sum(1 for _ in []),  # wird von run.py gesetzt
                'n_test':          n_test,
                'insample_hit':    insample_hit,
                'recall_pct':      recall_pct,
                'precision_pct':   precision_pct,
                'signal_fires':    signal_fires,
                'signal_confirmed':signal_confirmed,
                'degradation':     degradation,
                'reliability':     reliability,
                'cond_performance':cond_performance,
                'recall_details':  recall_details[:5],  # erste 5 für HTML
                'n_conditions':    len(must_have),
            }

        return results

    def _check_condition(self, df, idx: int, cond: dict) -> bool:
        """Prüft ob eine Trainings-Bedingung zum Zeitpunkt idx erfüllt ist."""
        feat      = cond.get('feature', '')
        t_stat    = cond.get('t_statistic', 0)
        threshold = cond.get('mean_before', cond.get('mean_all', 0))

        if feat not in df.columns:
            return False

        val = df.iloc[idx].get(feat)
        if val is None:
            return False
        try:
            val = float(val)
            if np.isnan(val):
                return False
        except (TypeError, ValueError):
            return False

        # t > 0: Feature war erhöht vor Move → prüfe ob aktuell > Schwellenwert
        # t < 0: Feature war erniedrigt → prüfe ob aktuell < Schwellenwert
        if t_stat > 0:
            return val > threshold * 0.9  # 10% Toleranz
        else:
            return val < threshold * 1.1


def _reliability_label(recall_pct: float, degradation: float, precision_pct: float = 100.0) -> dict:
    """
    Bewertet die Out-of-Sample-Zuverlässigkeit — Recall, Degradation UND Precision.
    Precision ist entscheidend: hoher Recall bei niedriger Precision heißt nur,
    dass das Signal fast immer feuert, nicht dass es die Bewegung tatsächlich vorhersagt.
    Schwellenwerte gemäß README (ROBUST >=50% Precision, STABIL >=35% Precision).
    """
    if recall_pct >= 60 and precision_pct >= 50 and degradation <= 10:
        return {'label': 'ROBUST',    'color': '#26a69a', 'icon': '✅',
                'text': 'Signal hält Out-of-Sample — gut generalisiert'}
    elif recall_pct >= 45 and precision_pct >= 35 and degradation <= 20:
        return {'label': 'STABIL',    'color': '#f5a623', 'icon': '🟡',
                'text': 'Signal leicht geschwächt — noch verwendbar'}
    elif recall_pct >= 30 and degradation <= 35:
        return {'label': 'SCHWACH',   'color': '#ef8c00', 'icon': '⚠️',
                'text': 'Deutliche Degradation oder Precision zu niedrig — mit Vorsicht nutzen'}
    else:
        return {'label': 'OVERFITTED','color': '#ef5350', 'icon': '❌',
                'text': 'Signal bricht OOS zusammen — wahrscheinlich Overfitting'}
