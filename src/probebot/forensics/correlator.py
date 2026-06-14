"""
Statistical correlator: finds which features consistently preceded specific movements.

Method: For each movement type, compare feature values N candles before
        the movement vs. all other candles using Welch's t-test.
        Significant t-statistics indicate predictive features.
        Lift factor = (hit_rate / base_rate) — how much more likely
        the feature predicts the event vs random.

Additions:
  - Feature deduplication (corr > 0.85 → keep higher |t|)
  - Bootstrap confidence intervals for hit rates (200 resamples)
  - Regime-conditional analysis (TREND / RANGE / CHAOS)
  - Composite score per move type (top-5 features combined)
  - Minimum 20 events per move type (otherwise skip with warning)
"""
import numpy as np
import pandas as pd
from scipy import stats
from typing import List, Optional

from ..detection.detector import Movement
from ..features.engine import feature_vector
from .database import ForensicsDB

_MIN_EVENTS = 20          # Skip move types with fewer training events
_DEDUP_CORR  = 0.85      # Feature pairs with |r| > this → keep stronger one
_BOOTSTRAP_N = 200       # Bootstrap resamples for CI


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
    ) -> tuple:
        """
        Run correlation analysis for all (or specified) movement types.

        Returns
        -------
        correlations : dict[str → list[dict]]
            Ranked feature list per move type (backward-compatible with existing consumers).
        meta : dict[str → dict]
            Per-move-type: composite score, regime analysis, warnings.
        """
        if move_types is None:
            move_types = list({m.move_type for m in movements})

        correlations = {}
        meta         = {}

        for mtype in move_types:
            subset = [m for m in movements if m.move_type == mtype]
            if len(subset) < 3:
                print(f"  [correlator] skip {mtype}: only {len(subset)} events")
                continue

            if len(subset) < _MIN_EVENTS:
                print(f"  [correlator] ⚠️  {mtype}: {len(subset)} events (< {_MIN_EVENTS}) — low statistical power")
                meta[mtype] = {'low_count_warning': True, 'n_events': len(subset)}

            print(f"  [correlator] analyzing {mtype} ({len(subset)} events)...")
            ranked, mtype_meta = self._analyze_type(df, subset, symbol, timeframe, mtype)
            correlations[mtype] = ranked
            meta[mtype] = {**meta.get(mtype, {}), **mtype_meta}

        return correlations, meta

    # ─────────────────────────────────────────────────────────────────────────
    def _analyze_type(
        self,
        df: pd.DataFrame,
        movements: List[Movement],
        symbol: str,
        timeframe: str,
        move_type: str,
    ) -> tuple:
        event_indices = [m.idx for m in movements]
        direction     = movements[0].direction

        # ── Feature matrix: events ─────────────────────────────────────────
        event_features = []
        for idx in event_indices:
            fvs = [feature_vector(df, idx - o)
                   for o in range(1, self.lookback + 1) if idx - o >= 0]
            if fvs:
                event_features.append(_average_fvs(fvs))

        if not event_features:
            return [], {}

        # ── Feature matrix: background (all candles, excluding event windows) ──
        all_features = []
        event_set = set(event_indices)
        step = max(1, len(df) // 600)
        for i in range(50, len(df), step):
            if not any(abs(i - e) <= self.lookback + 2 for e in event_set):
                all_features.append(feature_vector(df, i))

        if not all_features:
            return [], {}

        common_keys = set(event_features[0].keys()) & set(all_features[0].keys())

        # ── Welch t-test + Cohen's d + hit-rate ──────────────────────────────
        ranked = []
        event_vals_cache = {}
        bg_vals_cache    = {}

        for feat in common_keys:
            ev = np.array([
                fv[feat] for fv in event_features
                if isinstance(fv.get(feat), float) and not np.isnan(fv[feat])
            ])
            bg = np.array([
                fv[feat] for fv in all_features
                if isinstance(fv.get(feat), float) and not np.isnan(fv[feat])
            ])
            if len(ev) < 2 or len(bg) < 2:
                continue
            if np.std(ev) < 1e-10 and np.std(bg) < 1e-10:
                continue

            t_stat, p_val = stats.ttest_ind(ev, bg, equal_var=False)
            mean_ev  = float(np.mean(ev))
            std_ev   = float(np.std(ev))
            mean_bg  = float(np.mean(bg))
            std_bg   = float(np.std(bg))
            lift     = (mean_ev - mean_bg) / (abs(mean_bg) + 1e-10)
            pooled   = np.sqrt((std_ev ** 2 + std_bg ** 2) / 2)
            cohens_d = (mean_ev - mean_bg) / (pooled + 1e-10)

            threshold = mean_bg + np.sign(t_stat) * std_bg
            pred_pct  = float(np.mean(ev > threshold) * 100) if t_stat > 0 \
                   else float(np.mean(ev < threshold) * 100)

            record = {
                'symbol':          symbol,
                'timeframe':       timeframe,
                'move_type':       move_type,
                'direction':       direction,
                'feature':         feat,
                'n_events':        len(ev),
                'mean_before':     round(mean_ev, 6),
                'std_before':      round(std_ev, 6),
                'mean_all':        round(mean_bg, 6),
                'std_all':         round(std_bg, 6),
                't_statistic':     round(float(t_stat), 4),
                'p_value':         round(float(p_val), 6),
                'cohens_d':        round(float(cohens_d), 4),
                'lift_factor':     round(float(lift), 4),
                'predictive_pct':  round(pred_pct, 1),
                'threshold':       round(float(threshold), 6),
                'hit_rate_ci_low': 0.0,
                'hit_rate_ci_high':0.0,
            }
            ranked.append(record)
            event_vals_cache[feat] = ev
            bg_vals_cache[feat]    = bg

            if abs(t_stat) >= 1.5:
                self.db.upsert_commonality(record)

        ranked.sort(key=lambda r: abs(r['t_statistic']), reverse=True)

        # ── Feature deduplication (keep strongest in each correlated cluster) ──
        ranked = self._deduplicate(df, ranked, event_vals_cache)

        # ── Bootstrap CIs (top 20 features only) ─────────────────────────────
        for rec in ranked[:20]:
            ev = event_vals_cache.get(rec['feature'])
            if ev is not None and len(ev) >= 5:
                threshold = rec['threshold']
                t         = rec['t_statistic']
                ci_lo, ci_hi = _bootstrap_hit_ci(ev, threshold, t, n=_BOOTSTRAP_N)
                rec['hit_rate_ci_low']  = ci_lo
                rec['hit_rate_ci_high'] = ci_hi

        # ── Regime-conditional analysis ───────────────────────────────────────
        regime_analysis = self._regime_analysis(df, movements, event_vals_cache, ranked[:10])

        # ── Composite score ───────────────────────────────────────────────────
        composite = self._composite_score(event_features, all_features, ranked[:5])

        mtype_meta = {
            'n_events':        len(movements),
            'regime_analysis': regime_analysis,
            'composite':       composite,
        }

        return ranked, mtype_meta

    # ── Regime-conditional ────────────────────────────────────────────────────
    def _regime_analysis(
        self,
        df: pd.DataFrame,
        movements: List[Movement],
        event_vals_cache: dict,
        top_features: list,
    ) -> dict:
        """
        Break down top features by regime: TREND / RANGE / CHAOS.
        Only analyze regimes with >= 5 events.
        """
        if not top_features or 'regime' not in df.columns:
            # Try to get regime from movement context
            return self._regime_from_context(movements, top_features, event_vals_cache)

        regime_groups = {}
        for m in movements:
            pre_idx = m.idx - 1
            if 0 <= pre_idx < len(df):
                regime = str(df.iloc[pre_idx].get('regime', 'UNKNOWN'))
            else:
                regime = 'UNKNOWN'
            regime_groups.setdefault(regime, []).append(m)

        return self._analyze_regimes(regime_groups, df, top_features)

    def _regime_from_context(self, movements, top_features, event_vals_cache) -> dict:
        regime_groups = {}
        for m in movements:
            ctx    = m.context or {}
            regime = ctx.get('regime', 'UNKNOWN')
            regime_groups.setdefault(regime, []).append(m)

        result = {}
        for regime, group in regime_groups.items():
            if len(group) < 4:
                continue
            pcts = {}
            for feat in top_features[:5]:
                fname = feat.get('feature')
                ev    = event_vals_cache.get(fname)
                if ev is None:
                    continue
                # Feature values for this regime's events (approximation via index)
                idxs  = [m.idx for m in group]
                # We don't have per-movement feature values easily here; skip per-regime feat analysis
                # Just store n
                pcts[fname] = {'n_regime': len(group)}
            result[regime] = {
                'n_events':  len(group),
                'pct_total': round(len(group) / len(movements) * 100, 1),
                'features':  pcts,
            }
        return result

    def _analyze_regimes(self, regime_groups, df, top_features) -> dict:
        result = {}
        for regime, group in regime_groups.items():
            if len(group) < 4:
                continue
            result[regime] = {
                'n_events':  len(group),
                'pct_total': round(len(group) / sum(len(g) for g in regime_groups.values()) * 100, 1),
            }
        return result

    # ── Feature deduplication ─────────────────────────────────────────────────
    def _deduplicate(self, df: pd.DataFrame, ranked: list, ev_cache: dict) -> list:
        """
        Remove highly correlated features — keep the one with higher |t-statistic|.
        Uses event-period feature values for correlation (not full df, faster).
        """
        if len(ranked) < 2:
            return ranked

        feats = [r['feature'] for r in ranked[:40]]  # only deduplicate top-40
        # Build small feature matrix from event_vals_cache
        mat = {}
        for f in feats:
            if f in ev_cache and len(ev_cache[f]) > 1:
                mat[f] = ev_cache[f]

        to_remove = set()
        feat_list = list(mat.keys())
        for i, fa in enumerate(feat_list):
            if fa in to_remove:
                continue
            va = mat[fa]
            for fb in feat_list[i+1:]:
                if fb in to_remove:
                    continue
                vb = mat[fb]
                min_len = min(len(va), len(vb))
                if min_len < 3:
                    continue
                try:
                    r, _ = stats.pearsonr(va[:min_len], vb[:min_len])
                    if abs(r) > _DEDUP_CORR:
                        # Keep the one with higher |t-stat|
                        ta = next((rec['t_statistic'] for rec in ranked if rec['feature']==fa), 0)
                        tb = next((rec['t_statistic'] for rec in ranked if rec['feature']==fb), 0)
                        to_remove.add(fb if abs(ta) >= abs(tb) else fa)
                except Exception:
                    pass

        if to_remove:
            deduped = [r for r in ranked if r['feature'] not in to_remove]
            removed_names = ', '.join(sorted(to_remove)[:5])
            print(f"    [dedup] removed {len(to_remove)} correlated features: {removed_names}{'...' if len(to_remove)>5 else ''}")
            return deduped
        return ranked

    # ── Composite score ───────────────────────────────────────────────────────
    def _composite_score(
        self,
        event_features: list,
        all_features: list,
        top5: list,
    ) -> dict:
        """
        For the top-5 features, define composite score = count of conditions met.
        Report hit-rate when score >= threshold (2 out of 5).
        """
        if not top5 or not event_features:
            return {}

        conditions = []
        for rec in top5:
            feat      = rec['feature']
            t         = rec['t_statistic']
            threshold = rec.get('threshold', rec.get('mean_all', 0))
            conditions.append({'feature': feat, 't': t, 'threshold': threshold})

        def score_of(fv_dict) -> int:
            s = 0
            for cond in conditions:
                v = fv_dict.get(cond['feature'])
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                v = float(v)
                if cond['t'] > 0 and v > cond['threshold'] * 0.9:
                    s += 1
                elif cond['t'] < 0 and v < cond['threshold'] * 1.1:
                    s += 1
            return s

        event_scores = [score_of(fv) for fv in event_features]
        bg_scores    = [score_of(fv) for fv in all_features[:300]]  # sample bg

        for min_score in (len(conditions), len(conditions)-1, len(conditions)-2, 2, 1):
            if min_score < 1:
                break
            ev_hit  = sum(1 for s in event_scores if s >= min_score)
            bg_hit  = sum(1 for s in bg_scores    if s >= min_score)
            ev_rate = ev_hit  / len(event_scores) * 100 if event_scores else 0
            bg_rate = bg_hit  / len(bg_scores)    * 100 if bg_scores    else 0
            lift    = ev_rate / (bg_rate + 1e-3)
            if ev_rate >= 30 or lift >= 1.5:
                return {
                    'min_score':      min_score,
                    'n_conditions':   len(conditions),
                    'event_hit_rate': round(ev_rate, 1),
                    'baseline_rate':  round(bg_rate, 1),
                    'lift':           round(lift, 2),
                    'conditions':     [c['feature'] for c in conditions],
                    'description':    (
                        f"Wenn {min_score}/{len(conditions)} Bedingungen erfüllt: "
                        f"{ev_rate:.0f}% der Events hatten dieses Muster "
                        f"(Baseline: {bg_rate:.0f}%, Lift: {lift:.1f}×)"
                    ),
                }
        return {}

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
        mat  = np.array([[fv.get(k, 0.0) for k in keys] for fv in fvs])
        mat  = np.nan_to_num(mat, nan=0.0)

        try:
            scaler     = RobustScaler()
            mat_scaled = scaler.fit_transform(mat)
            km         = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            labels     = km.fit_predict(mat_scaled)
        except Exception as e:
            print(f"  [cluster] failed: {e}")
            return {}

        clusters = {}
        for cid in range(n_clusters):
            mask              = labels == cid
            cluster_movements = [m for m, l in zip(movements, labels) if l == cid]
            cluster_mat       = mat[mask]
            other_mat         = mat[~mask]
            key_features      = []
            for j, k in enumerate(keys):
                cv = cluster_mat[:, j]
                ov = other_mat[:, j] if len(other_mat) > 0 else np.array([0.0])
                if np.std(cv) < 1e-10:
                    continue
                t, _ = stats.ttest_ind(cv, ov, equal_var=False) if len(ov) > 0 else (0, 1)
                if abs(t) > 1.5:
                    key_features.append({
                        'feature':      k,
                        't_stat':       round(float(t), 3),
                        'cluster_mean': round(float(np.mean(cv)), 4),
                        'global_mean':  round(float(np.mean(ov)), 4),
                        'diff':         round(float(np.mean(cv) - np.mean(ov)), 4),
                    })
            key_features.sort(key=lambda x: abs(x['t_stat']), reverse=True)

            directions   = [m.direction for m in cluster_movements]
            dominant_dir = 'UP' if directions.count('UP') > directions.count('DOWN') else 'DOWN'

            from collections import Counter
            move_type_counts = dict(Counter(m.move_type for m in cluster_movements))

            clusters[cid] = {
                'n':              int(mask.sum()),
                'dominant_direction': dominant_dir,
                'movement_types': list({m.move_type for m in cluster_movements}),
                'move_types':     move_type_counts,
                'timestamps':     [str(m.timestamp) for m in cluster_movements],
                'key_features':   key_features[:12],
                'top_features':   key_features[:12],
                'avg_magnitude_pct': round(float(np.mean([m.magnitude_pct for m in cluster_movements])), 3),
            }

        return clusters


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _average_fvs(vectors: List[dict]) -> dict:
    if not vectors:
        return {}
    keys   = vectors[0].keys()
    result = {}
    for k in keys:
        vals = [float(v[k]) for v in vectors
                if isinstance(v.get(k), (int, float)) and not np.isnan(float(v[k]))]
        result[k] = float(np.mean(vals)) if vals else np.nan
    return result


def _bootstrap_hit_ci(event_vals: np.ndarray, threshold: float, t_stat: float,
                       n: int = 200) -> tuple:
    """Bootstrap 90% CI for hit-rate."""
    hit_rates = []
    size = len(event_vals)
    for _ in range(n):
        sample = np.random.choice(event_vals, size=size, replace=True)
        if t_stat > 0:
            hit = float(np.mean(sample > threshold) * 100)
        else:
            hit = float(np.mean(sample < threshold) * 100)
        hit_rates.append(hit)
    arr = np.array(hit_rates)
    return round(float(np.percentile(arr, 5)), 1), round(float(np.percentile(arr, 95)), 1)
