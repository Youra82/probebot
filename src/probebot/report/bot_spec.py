"""
Bot-Spec Generator — strukturierte JSON-Datei für die Entwicklung eines neuen Bots.
Enthält: Entry-Bedingungen, Feature-Schwellenwerte, Regime-Info, Cluster-Fingerabdrücke.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def generate_bot_spec(
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    movements: list,
    correlations: dict,
    clusters: dict,
    drill_down_results: dict,
    output_path: str,
    validation_results: dict = None,
    correlations_meta: dict = None,
    selected_strategy: dict = None,
    split_date: str = None,
    split_idx: int = None,
) -> str:
    """Erzeugt strukturierte Bot-Spec JSON und gibt den Pfad zurück."""

    spec = {
        'meta': {
            'generated_at': datetime.utcnow().isoformat(),
            'source': 'probebot Market Forensics Engine',
            'symbol': symbol,
            'timeframe': timeframe,
            'period': {'start': start_date, 'end': end_date},
            'split_date': split_date or '',
            'split_idx': split_idx or 0,
            'n_movements': len(movements),
            'oos_validation': 'STRICT 70/30 temporal split — test data never seen by learning algorithms',
            'usage': (
                'Verwende diese Datei als Grundlage für einen neuen Trading-Bot. '
                'Die entry_conditions pro Bewegungstyp sind statistisch validierte '
                'Vorbedingungen (Welch t-Test, p<0.05, 70% Trainingsdaten). '
                'oos_validation zeigt ob die Signale auf den unsichtbaren 30% standhalten. '
                'Nur ROBUST/STABIL Signale für den Bot verwenden!'
            ),
        },
        'selected_strategy': selected_strategy or {},
        'movement_statistics': _build_movement_stats(movements),
        'entry_conditions': _build_entry_conditions(correlations, correlations_meta),
        'oos_validation': _build_oos_section(validation_results),
        'composite_signals': _build_composite_section(correlations_meta),
        'regime_profile': _build_regime_profile(movements),
        'cluster_fingerprints': _build_cluster_fingerprints(clusters),
        'mtf_entry_timing': _build_mtf_timing(drill_down_results, movements),
        'signal_logic_template': _build_signal_template(correlations),
    }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding='utf-8')
    return str(path)


def _build_movement_stats(movements: list) -> dict:
    from collections import Counter
    type_counts = Counter(m.move_type for m in movements)
    stats = {}
    for mtype, cnt in type_counts.items():
        type_mvts = [m for m in movements if m.move_type == mtype]
        mags = [abs(m.magnitude_pct) for m in type_mvts]
        atrs = [m.atr_multiple for m in type_mvts]
        stats[mtype] = {
            'n_events': cnt,
            'direction': type_mvts[0].direction if type_mvts else '?',
            'magnitude_pct': {
                'min': round(min(mags), 2),
                'max': round(max(mags), 2),
                'avg': round(sum(mags) / len(mags), 2),
            },
            'atr_multiple': {
                'min': round(min(atrs), 2),
                'max': round(max(atrs), 2),
                'avg': round(sum(atrs) / len(atrs), 2),
            },
        }
    return stats


def _build_oos_section(validation_results: dict) -> dict:
    """Out-of-Sample Validierungsergebnisse — nur stabile Signale sollten im Bot verwendet werden."""
    if not validation_results:
        return {'note': 'Keine OOS-Validierung durchgeführt.'}
    result = {}
    for mtype, vr in validation_results.items():
        rl = vr.get('reliability', {})
        result[mtype] = {
            'reliability':    rl.get('label', '?'),
            'insample_hit':   vr.get('insample_hit', 0),
            'oos_recall_pct': vr.get('recall_pct', 0),
            'oos_precision_pct': vr.get('precision_pct', 0),
            'degradation':    vr.get('degradation', 0),
            'n_train':        vr.get('n_train', 0),
            'n_test':         vr.get('n_test', 0),
            'use_in_bot':     rl.get('label') in ('ROBUST', 'STABIL'),
            'warning':        rl.get('text', ''),
            'per_feature': [
                {
                    'feature':   c['feature'],
                    'train_hit': c['train_hit'],
                    'oos_hit':   c['test_hit'],
                    'delta':     round(c['test_hit'] - c['train_hit'], 1),
                }
                for c in vr.get('cond_performance', [])
            ],
        }
    return result


def _build_composite_section(correlations_meta: dict) -> dict:
    """Composite-Signale — Kombination der stärksten Features."""
    if not correlations_meta:
        return {}
    result = {}
    for mtype, meta in correlations_meta.items():
        comp = meta.get('composite', {})
        if comp:
            result[mtype] = comp
    return result


def _build_entry_conditions(correlations: dict, correlations_meta: dict = None) -> dict:
    """
    Pro Bewegungstyp: die statistisch stärksten Vorbedingungen als
    direkt verwendbare Entry-Checks mit Schwellenwerten.
    """
    conditions = {}
    for mtype, ranked_or_dict in correlations.items():
        # Support both old (list) and new (dict with 'rows') format
        ranked = ranked_or_dict.get('rows', ranked_or_dict) if isinstance(ranked_or_dict, dict) else ranked_or_dict
        strong = [r for r in ranked if abs(r.get('t_statistic', 0)) >= 2.5]
        if not strong:
            continue

        must_have = []   # t >= 5, hit >= 40%
        should_have = [] # t >= 3, hit >= 30%
        supporting = []  # t >= 2.5

        for r in strong[:50]:
            t = r['t_statistic']
            feat = r['feature']
            mean_before = r.get('mean_before', 0)
            mean_all = r.get('mean_all', 0)
            hit = r.get('predictive_pct', 0)
            direction = 'above' if t > 0 else 'below'

            cond = {
                'feature': feat,
                'direction': direction,
                'threshold': round(float(mean_before), 6),
                'baseline_avg': round(float(mean_all), 6),
                'deviation_pct': round(
                    abs(mean_before - mean_all) / (abs(mean_all) + 1e-9) * 100, 1
                ),
                't_statistic': round(t, 3),
                'hit_rate_pct': round(hit, 1),
                'signal': f'{feat} {">" if t > 0 else "<"} {_fmt_threshold(mean_before)}',
            }

            if abs(t) >= 5 and hit >= 40:
                must_have.append(cond)
            elif abs(t) >= 3 and hit >= 30:
                should_have.append(cond)
            else:
                supporting.append(cond)

        conditions[mtype] = {
            'description': _describe_move_type(mtype),
            'must_have': must_have[:8],
            'should_have': should_have[:10],
            'supporting': supporting[:10],
            'min_conditions_for_signal': max(2, len(must_have)),
            'scoring_hint': (
                f'Score += 3 pro must_have-Bedingung, '
                f'+2 pro should_have, +1 pro supporting. '
                f'Signal bei Score >= {max(6, len(must_have)*3)}'
            ),
        }

    return conditions


def _build_regime_profile(movements: list) -> dict:
    """Welche Regime traten bei welchen Bewegungstypen auf?"""
    from collections import Counter, defaultdict
    regime_by_type = defaultdict(list)
    for m in movements:
        ctx = m.context or {}
        regime = ctx.get('regime', 'UNKNOWN')
        regime_by_type[m.move_type].append(regime)

    result = {}
    for mtype, regimes in regime_by_type.items():
        counts = Counter(regimes)
        total = len(regimes)
        result[mtype] = {
            'dominant_regime': counts.most_common(1)[0][0] if counts else 'UNKNOWN',
            'regime_distribution': {
                r: round(c / total * 100, 1)
                for r, c in counts.most_common()
            },
            'avg_rsi': _avg_ctx_field(movements, mtype, 'rsi_14'),
            'avg_adx': _avg_ctx_field(movements, mtype, 'adx'),
            'avg_entropy': _avg_ctx_field(movements, mtype, 'entropy_20'),
            'avg_hurst': _avg_ctx_field(movements, mtype, 'hurst_60'),
        }
    return result


def _build_cluster_fingerprints(clusters: dict) -> dict:
    """Cluster-Fingerabdrücke als Bot-verwendbare Feature-Profile."""
    if not clusters:
        return {}
    result = {}
    for cid, cdata in clusters.items():
        top_features = cdata.get('top_features', [])
        result[str(cid)] = {
            'n_events': cdata.get('n', 0),
            'move_types': cdata.get('move_types', {}),
            'distinguishing_features': [
                {
                    'feature': f.get('feature'),
                    'cluster_value': round(float(f.get('cluster_mean', 0)), 4),
                    'global_value': round(float(f.get('global_mean', 0)), 4),
                    'diff': round(float(f.get('diff', 0)), 4),
                    'direction': 'higher_than_avg' if f.get('diff', 0) > 0 else 'lower_than_avg',
                }
                for f in top_features[:12]
            ],
        }
    return result


def _build_mtf_timing(drill_down_results: dict, movements: list) -> dict:
    """
    Aus Drill-Down-Ergebnissen: Wo ist der beste Entry-Zeitrahmen?
    """
    if not drill_down_results:
        return {'note': 'Kein Drill-Down ausgeführt.'}

    tf_confidence = {}
    for ts_str, dd in drill_down_results.items():
        if not isinstance(dd, dict):
            continue
        for tf, level in dd.items():
            if not isinstance(level, dict) or 'error' in level:
                continue
            conf = level.get('entry_confidence', 0)
            if tf not in tf_confidence:
                tf_confidence[tf] = []
            tf_confidence[tf].append(conf)

    result = {}
    for tf, confs in tf_confidence.items():
        avg = sum(confs) / len(confs)
        result[tf] = {
            'avg_entry_confidence': round(avg, 2),
            'max_entry_confidence': max(confs),
            'n_events': len(confs),
            'recommendation': (
                'STRONG ENTRY' if avg >= 7 else
                'VALID ENTRY' if avg >= 5 else
                'WEAK ENTRY' if avg >= 3 else
                'SKIP'
            ),
        }

    best_tf = max(result.items(), key=lambda x: x[1]['avg_entry_confidence'])[0] if result else None
    return {
        'per_timeframe': result,
        'best_entry_timeframe': best_tf,
        'recommendation': (
            f'Verwende {best_tf} für den Entry-Trigger nach Bewegungserkennung auf {movements[0].move_type if movements else "?"}.'
            if best_tf else 'Nicht genug Daten.'
        ),
    }


def _build_signal_template(correlations: dict) -> dict:
    """
    Pseudocode-Template für die Bot-Implementierung.
    """
    templates = {}
    for mtype, ranked_or_dict in correlations.items():
        ranked = ranked_or_dict.get('rows', ranked_or_dict) if isinstance(ranked_or_dict, dict) else ranked_or_dict
        top5 = [r for r in ranked if abs(r.get('t_statistic', 0)) >= 4.0][:5]
        if not top5:
            continue

        checks = []
        for r in top5:
            t = r['t_statistic']
            feat = r['feature']
            threshold = r.get('mean_before', 0)
            op = '>' if t > 0 else '<'
            checks.append(f"features['{feat}'] {op} {_fmt_threshold(threshold)}")

        direction = 'LONG' if ('UP' in mtype or 'BREAKOUT' in mtype) else 'SHORT'
        templates[mtype] = {
            'direction': direction,
            'pseudocode': [
                f'# Signal: {mtype}',
                f'score = 0',
            ] + [f'if {c}: score += 1  # t={ranked[i]["t_statistic"]:+.1f}' for i, c in enumerate(checks)] + [
                f'if score >= {max(2, len(checks)-1)}: place_{direction.lower()}_order()',
            ],
            'key_features': [r['feature'] for r in top5],
            'estimated_hit_rate': round(
                sum(r.get('predictive_pct', 0) for r in top5) / len(top5), 1
            ) if top5 else 0,
        }
    return templates


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _avg_ctx_field(movements, mtype, field):
    vals = []
    for m in movements:
        if m.move_type != mtype:
            continue
        ctx = m.context or {}
        v = ctx.get(field)
        if v is not None:
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass
    return round(sum(vals) / len(vals), 3) if vals else None


def _fmt_threshold(val) -> str:
    try:
        v = float(val)
        if abs(v) >= 1000:
            return f'{v:.0f}'
        elif abs(v) >= 10:
            return f'{v:.2f}'
        else:
            return f'{v:.4f}'
    except (TypeError, ValueError):
        return str(val)


def _describe_move_type(mtype: str) -> str:
    descriptions = {
        'BREAKDOWN': 'Schlusskurs bricht unter N-Bar-Konsolidierung — starkes Short-Signal',
        'BREAKOUT_UP': 'Schlusskurs bricht über N-Bar-Konsolidierung — starkes Long-Signal',
        'IMPULSE_DOWN': 'Einzelne Kerze > 2× ATR nach unten — Momentum-Short',
        'IMPULSE_UP': 'Einzelne Kerze > 2× ATR nach oben — Momentum-Long',
        'REVERSAL_DOWN': 'Trendwende nach Aufwärtstrend — Mean-Reversion Short',
        'REVERSAL_UP': 'Trendwende nach Abwärtstrend — Mean-Reversion Long',
        'SQUEEZE_RELEASE_DOWN': 'Volatilitätskompression → Ausdehnung nach unten',
        'SQUEEZE_RELEASE_UP': 'Volatilitätskompression → Ausdehnung nach oben',
        'ACCELERATION_DOWN': 'Momentum-Surge im laufenden Abwärtstrend',
        'ACCELERATION_UP': 'Momentum-Surge im laufenden Aufwärtstrend',
        'GAP_DOWN': 'Lücke zwischen Close und nächstem Open nach unten',
        'GAP_UP': 'Lücke zwischen Close und nächstem Open nach oben',
    }
    return descriptions.get(mtype, mtype)
