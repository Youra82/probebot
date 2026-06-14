"""
Statistical correlator: finds which features consistently preceded specific movements.

Method: For each movement type, compare feature values N candles before
        the movement vs. all other candles using Welch's t-test.
        Significant t-statistics indicate predictive features.
        Lift factor = (hit_rate / base_rate) — how much more likely
        the feature predicts the event vs random.
"""
import numpy as np
import pandas as pd
from scipy import stats
from typing import List, Optional

from ..detection.detector import Movement
from ..features.engine import feature_vector
from .database import ForensicsDB


class Correlator:
    def __init__(self, db: ForensicsDB, lookback: int = 5):
        self.db = db
        self.lookback = lookback

    def analyze(
        self,
        df: pd.DataFrame,
        movements: List[Movement],
        symbol: str,
        timeframe: str,
        move_types: Optional[List[str]] = None,
    ) -> dict:
        """
        Run correlation analysis for all (or specified) movement types.
        Returns dict keyed by move_type with ranked feature list.
        """
        if move_types is None:
            move_types = list({m.move_type for m in movements})

        results = {}
        for mtype in move_types:
            subset = [m for m in movements if m.move_type == mtype]
            if len(subset) < 3:
                continue
            print(f"  [correlator] analyzing {mtype} ({len(subset)} events)...")
            report = self._analyze_type(df, subset, symbol, timeframe, mtype)
            results[mtype] = report

        return results

    def _analyze_type(
        self,
        df: pd.DataFrame,
        movements: List[Movement],
        symbol: str,
        timeframe: str,
        move_type: str,
    ) -> List[dict]:
        # Collect feature values before each movement
        event_indices = [m.idx for m in movements]
        direction = movements[0].direction

        # Feature matrix for events (average of lookback candles before)
        event_features = []
        for idx in event_indices:
            fvs = []
            for offset in range(1, self.lookback + 1):
                j = idx - offset
                if j >= 0:
                    fvs.append(feature_vector(df, j))
            if fvs:
                avg = _average_fvs(fvs)
                event_features.append(avg)

        if not event_features:
            return []

        # Feature matrix for ALL candles (baseline)
        all_features = []
        event_set = set(event_indices)
        step = max(1, len(df) // 500)  # sample up to 500 background candles
        for i in range(50, len(df), step):
            # Exclude candles too close to events
            too_close = any(abs(i - e) <= self.lookback + 2 for e in event_set)
            if not too_close:
                all_features.append(feature_vector(df, i))

        if not all_features:
            return []

        # Get common keys
        event_keys = set(event_features[0].keys())
        bg_keys = set(all_features[0].keys())
        common_keys = event_keys & bg_keys

        ranked = []
        for feat in common_keys:
            event_vals = np.array([
                fv[feat] for fv in event_features
                if isinstance(fv.get(feat), float) and not np.isnan(fv[feat])
            ])
            bg_vals = np.array([
                fv[feat] for fv in all_features
                if isinstance(fv.get(feat), float) and not np.isnan(fv[feat])
            ])

            if len(event_vals) < 2 or len(bg_vals) < 2:
                continue
            if np.std(event_vals) < 1e-10 and np.std(bg_vals) < 1e-10:
                continue

            # Welch's t-test
            t_stat, p_val = stats.ttest_ind(event_vals, bg_vals, equal_var=False)

            mean_event = float(np.mean(event_vals))
            std_event = float(np.std(event_vals))
            mean_bg = float(np.mean(bg_vals))
            std_bg = float(np.std(bg_vals))

            # Lift factor: how much higher/lower vs baseline (normalized)
            lift = (mean_event - mean_bg) / (abs(mean_bg) + 1e-10)

            # Effect size (Cohen's d)
            pooled_std = np.sqrt((std_event ** 2 + std_bg ** 2) / 2)
            cohens_d = (mean_event - mean_bg) / (pooled_std + 1e-10)

            # Predictive pct: what % of event candles had this feature above/below baseline?
            threshold = mean_bg + np.sign(t_stat) * std_bg
            if t_stat > 0:
                predictive_pct = float(np.mean(event_vals > threshold) * 100)
            else:
                predictive_pct = float(np.mean(event_vals < threshold) * 100)

            record = {
                'symbol': symbol,
                'timeframe': timeframe,
                'move_type': move_type,
                'direction': direction,
                'feature': feat,
                'n_events': len(event_vals),
                'mean_before': round(mean_event, 6),
                'std_before': round(std_event, 6),
                'mean_all': round(mean_bg, 6),
                'std_all': round(std_bg, 6),
                't_statistic': round(float(t_stat), 4),
                'p_value': round(float(p_val), 6),
                'cohens_d': round(float(cohens_d), 4),
                'lift_factor': round(float(lift), 4),
                'predictive_pct': round(predictive_pct, 1),
            }
            ranked.append(record)
            # Persist significant ones
            if abs(t_stat) >= 1.5:
                self.db.upsert_commonality(record)

        # Sort by absolute t-statistic
        ranked.sort(key=lambda r: abs(r['t_statistic']), reverse=True)
        return ranked

    def cluster_movements(
        self,
        df: pd.DataFrame,
        movements: List[Movement],
        n_clusters: int = 4,
    ) -> dict:
        """
        Group movements by similarity of pre-condition vectors using K-Means.
        Returns dict: cluster_id → {movements, centroid, key_features}
        """
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import RobustScaler

        if len(movements) < n_clusters:
            n_clusters = max(1, len(movements) // 2)

        fvs = []
        for m in movements:
            vecs = [feature_vector(df, m.idx - o) for o in range(1, 6) if m.idx - o >= 0]
            fvs.append(_average_fvs(vecs) if vecs else {})

        if not fvs:
            return {}

        keys = sorted(fvs[0].keys())
        mat = np.array([[fv.get(k, 0.0) for k in keys] for fv in fvs])
        # Replace NaN
        mat = np.nan_to_num(mat, nan=0.0)

        try:
            scaler = RobustScaler()
            mat_scaled = scaler.fit_transform(mat)
            km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            labels = km.fit_predict(mat_scaled)
        except Exception as e:
            print(f"  [cluster] failed: {e}")
            return {}

        clusters = {}
        for cid in range(n_clusters):
            mask = labels == cid
            cluster_movements = [m for m, l in zip(movements, labels) if l == cid]
            # Find key distinguishing features for this cluster
            cluster_mat = mat[mask]
            other_mat = mat[~mask]
            key_features = []
            for j, k in enumerate(keys):
                cv = cluster_mat[:, j]
                ov = other_mat[:, j] if len(other_mat) > 0 else np.array([0.0])
                if np.std(cv) < 1e-10:
                    continue
                t, p = stats.ttest_ind(cv, ov, equal_var=False) if len(ov) > 0 else (0, 1)
                if abs(t) > 1.5:
                    key_features.append({
                        'feature': k,
                        't_stat': round(float(t), 3),
                        'cluster_mean': round(float(np.mean(cv)), 4),
                        'other_mean': round(float(np.mean(ov)), 4),
                    })
            key_features.sort(key=lambda x: abs(x['t_stat']), reverse=True)

            directions = [m.direction for m in cluster_movements]
            dominant_dir = 'UP' if directions.count('UP') > directions.count('DOWN') else 'DOWN'

            clusters[cid] = {
                'n': int(mask.sum()),
                'dominant_direction': dominant_dir,
                'movement_types': list({m.move_type for m in cluster_movements}),
                'timestamps': [str(m.timestamp) for m in cluster_movements],
                'key_features': key_features[:10],
                'avg_magnitude_pct': round(float(np.mean([m.magnitude_pct for m in cluster_movements])), 3),
            }

        return clusters


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _average_fvs(vectors: List[dict]) -> dict:
    if not vectors:
        return {}
    keys = vectors[0].keys()
    result = {}
    for k in keys:
        vals = [float(v[k]) for v in vectors
                if isinstance(v.get(k), (int, float)) and not np.isnan(float(v[k]))]
        result[k] = float(np.mean(vals)) if vals else np.nan
    return result
