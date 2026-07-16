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
from ..features.engine import feature_vector, feature_vectors_bulk
from .database import ForensicsDB

_MIN_EVENTS = 20          # Skip move types with fewer training events
_DEDUP_CORR  = 0.85      # Feature pairs with |r| > this → keep stronger one
_BOOTSTRAP_N = 200       # Bootstrap resamples for CI


class Correlator:
    def __init__(self, db: ForensicsDB, lookback: int = 5, verbose: bool = True):
        self.db = db
        self.lookback = lookback
        self.verbose = verbose

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
                if self.verbose:
                    print(f"  [correlator] skip {mtype}: only {len(subset)} events")
                continue

            if len(subset) < _MIN_EVENTS:
                if self.verbose:
                    print(f"  [correlator] ⚠️  {mtype}: {len(subset)} events (< {_MIN_EVENTS}) — low statistical power")
                meta[mtype] = {'low_count_warning': True, 'n_events': len(subset)}

            if self.verbose:
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
        # Bulk statt einem feature_vector()-Aufruf pro (Event, Lookback-Offset) —
        # war zusammen mit dem Hintergrund-Sampling unten der dominante Kostenpunkt
        # der gesamten Korrelationsanalyse (siehe _bulk_averaged_feature_vectors()).
        event_features = [fv for fv in _bulk_averaged_feature_vectors(df, event_indices, self.lookback) if fv]

        if not event_features:
            return [], {}

        # ── Feature matrix: background (all candles, excluding event windows) ──
        event_set = set(event_indices)
        step = max(1, len(df) // 600)
        bg_indices = [i for i in range(50, len(df), step)
                      if not any(abs(i - e) <= self.lookback + 2 for e in event_set)]
        all_features = feature_vectors_bulk(df, bg_indices) if bg_indices else []

        if not all_features:
            return [], {}

        common_keys = sorted(set(event_features[0].keys()) & set(all_features[0].keys()))

        # ── Welch t-test + Cohen's d + hit-rate — vektorisiert ──────────────────
        # War vorher eine Python-Schleife mit einem eigenen scipy.stats.ttest_ind-
        # Aufruf PRO Feature (100+ unabhaengige, kleine Berechnungen — strukturell
        # dasselbe Muster wie der alte Optimizer-Engpass). Jetzt: alle Features
        # gleichzeitig als (n_events, n_features)/(n_bg, n_features)-Matrizen,
        # Welch-t-Statistik + Freiheitsgrade + p-Wert per Spalte in einem Rutsch.
        stats_by_feat = _vectorized_ttests(event_features, all_features, common_keys)

        ranked = []
        event_vals_cache = {}
        bg_vals_cache    = {}

        for feat, s in stats_by_feat.items():
            record = {
                'symbol':          symbol,
                'timeframe':       timeframe,
                'move_type':       move_type,
                'direction':       direction,
                'feature':         feat,
                'n_events':        s['n_ev'],
                'mean_before':     round(s['mean_ev'], 6),
                'std_before':      round(s['std_ev'], 6),
                'mean_all':        round(s['mean_bg'], 6),
                'std_all':         round(s['std_bg'], 6),
                't_statistic':     round(s['t_stat'], 4),
                'p_value':         round(s['p_val'], 6),
                'cohens_d':        round(s['cohens_d'], 4),
                'lift_factor':     round(s['lift'], 4),
                'predictive_pct':  round(s['pred_pct'], 1),
                'threshold':       round(s['threshold'], 6),
                'hit_rate_ci_low': 0.0,
                'hit_rate_ci_high':0.0,
            }
            ranked.append(record)
            event_vals_cache[feat] = s['ev']
            bg_vals_cache[feat]    = s['bg']

            if abs(s['t_stat']) >= 1.5:
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
            if self.verbose:
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

        fvs = _bulk_averaged_feature_vectors(df, [m.idx for m in movements], 5)

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

def _bulk_averaged_feature_vectors(df: pd.DataFrame, indices: List[int], lookback: int) -> List[dict]:
    """
    Fuer jeden Index in `indices`: Feature-Vektor gemittelt ueber die `lookback`
    Kerzen davor (idx-1 .. idx-lookback) — via EINEM feature_vectors_bulk()-Aufruf
    fuer alle benoetigten Zeilen zusammen, statt einem feature_vector()-Einzel-
    aufruf pro (Event, Offset)-Paar. War (zusammen mit dem Hintergrund-Sampling
    in _analyze_type()) der mit Abstand teuerste Teil der Korrelationsanalyse —
    nicht die Statistik selbst (siehe _vectorized_ttests()).

    Gibt eine Liste zurueck, EIN Eintrag pro Element in `indices` (leeres dict
    {} wenn kein einziger Offset gueltig war, z.B. idx zu nah am Datenanfang) —
    Aufrufer die leere Eintraege ueberspringen wollen, filtern selbst per
    `if fv:` (wie zuvor per `if fvs: append(...)`).
    """
    pairs = []  # (Position in `indices`, tatsaechlicher df-Zeilenindex)
    for pos, idx in enumerate(indices):
        for o in range(1, lookback + 1):
            if idx - o >= 0:
                pairs.append((pos, idx - o))

    result = [{} for _ in indices]
    if not pairs:
        return result

    bulk_fvs = feature_vectors_bulk(df, [p[1] for p in pairs])

    grouped: dict = {}
    for (pos, _), fv in zip(pairs, bulk_fvs):
        grouped.setdefault(pos, []).append(fv)

    for pos, fvs in grouped.items():
        result[pos] = _average_fvs(fvs)

    return result


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
    """Bootstrap 90% CI for hit-rate. Ein (n, size)-Draw statt n Einzelaufrufen
    von np.random.choice — dieselbe Resampling-Verteilung, nur vektorisiert."""
    size = len(event_vals)
    samples = np.random.choice(event_vals, size=(n, size), replace=True)
    if t_stat > 0:
        hit_rates = np.mean(samples > threshold, axis=1) * 100
    else:
        hit_rates = np.mean(samples < threshold, axis=1) * 100
    return (round(float(np.percentile(hit_rates, 5)), 1),
            round(float(np.percentile(hit_rates, 95)), 1))


def _vectorized_ttests(event_features: List[dict], all_features: List[dict],
                        feat_list: List[str]) -> dict:
    """
    Welch's t-test + Cohen's d + hit-rate fuer ALLE Features gleichzeitig statt
    einer Python-Schleife mit einem scipy.stats.ttest_ind()-Aufruf pro Feature.

    Repliziert _analyze_type()'s alte Pro-Feature-Logik exakt, inkl. der beiden
    unterschiedlichen Standardabweichungs-Definitionen die dort verwendet wurden:
      - std_ev/std_bg (reportiert + fuer Cohen's d): np.std() mit ddof=0
        (Populations-Standardabweichung).
      - Die interne Varianz der Welch-t-Statistik: scipy.stats.ttest_ind nutzt
        dafuer ddof=1 (Stichproben-Varianz) — hier per np.nanvar(..., ddof=1)
        nachgebildet, mit Welch-Satterthwaite-Freiheitsgraden fuer den p-Wert.

    Returns: dict[feature] -> {t_stat, p_val, mean_ev, std_ev, mean_bg, std_bg,
    cohens_d, lift, threshold, pred_pct, n_ev, ev (array), bg (array)} — nur
    fuer Features die die Mindest-Kriterien erfuellen (>=2 Werte je Seite,
    nicht beide Seiten quasi-konstant), wie im Original.
    """
    ev_df = pd.DataFrame(event_features).reindex(columns=feat_list)
    bg_df = pd.DataFrame(all_features).reindex(columns=feat_list)
    ev_mat = ev_df.apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)
    bg_mat = bg_df.apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)

    n_ev = np.sum(~np.isnan(ev_mat), axis=0)
    n_bg = np.sum(~np.isnan(bg_mat), axis=0)

    with np.errstate(invalid='ignore', divide='ignore'):
        mean_ev = np.nanmean(ev_mat, axis=0)
        mean_bg = np.nanmean(bg_mat, axis=0)
        std_ev  = np.nanstd(ev_mat, axis=0, ddof=0)   # reportiert + Cohen's d
        std_bg  = np.nanstd(bg_mat, axis=0, ddof=0)
        var_ev1 = np.nanvar(ev_mat, axis=0, ddof=1)   # fuer Welch-t-Statistik
        var_bg1 = np.nanvar(bg_mat, axis=0, ddof=1)

        se = np.sqrt(var_ev1 / n_ev + var_bg1 / n_bg)
        t_stat = (mean_ev - mean_bg) / se

        num = (var_ev1 / n_ev + var_bg1 / n_bg) ** 2
        den = (var_ev1 / n_ev) ** 2 / (n_ev - 1) + (var_bg1 / n_bg) ** 2 / (n_bg - 1)
        dof = num / den
        p_val = 2.0 * stats.t.sf(np.abs(t_stat), dof)

        lift     = (mean_ev - mean_bg) / (np.abs(mean_bg) + 1e-10)
        pooled   = np.sqrt((std_ev ** 2 + std_bg ** 2) / 2)
        cohens_d = (mean_ev - mean_bg) / (pooled + 1e-10)
        threshold = mean_bg + np.sign(t_stat) * std_bg

    valid = (n_ev >= 2) & (n_bg >= 2) & ~((std_ev < 1e-10) & (std_bg < 1e-10))

    out = {}
    for j, feat in enumerate(feat_list):
        if not valid[j]:
            continue
        ev_col = ev_mat[:, j]
        ev_clean = ev_col[~np.isnan(ev_col)]
        bg_col = bg_mat[:, j]
        bg_clean = bg_col[~np.isnan(bg_col)]
        t = float(t_stat[j])
        thr = float(threshold[j])
        pred_pct = float(np.mean(ev_clean > thr) * 100) if t > 0 \
            else float(np.mean(ev_clean < thr) * 100)
        out[feat] = {
            't_stat': t, 'p_val': float(p_val[j]),
            'mean_ev': float(mean_ev[j]), 'std_ev': float(std_ev[j]),
            'mean_bg': float(mean_bg[j]), 'std_bg': float(std_bg[j]),
            'cohens_d': float(cohens_d[j]), 'lift': float(lift[j]),
            'threshold': thr, 'pred_pct': pred_pct,
            'n_ev': int(n_ev[j]), 'ev': ev_clean, 'bg': bg_clean,
        }
    return out
