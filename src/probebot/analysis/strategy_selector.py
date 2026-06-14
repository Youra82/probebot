"""
Strategy type selector — derived from forensics results.
Shared between html_report.py (visualization) and optimizer.py (config).
"""

_MOMENTUM_FEATS = {
    'momentum_score', 'rsi_14', 'rsi_7', 'rsi_21', 'stoch_k', 'stoch_d',
    'willr_14', 'cci_20', 'roc_10', 'roc_5', 'roc_20', 'macd_hist',
    'kalman_vel', 'velocity', 'dist_ema_9', 'dist_ema_21',
}
_VOLUME_FEATS = {
    'cvd', 'cvd_slope', 'obv_z', 'obv_slope', 'buy_pressure', 'sell_pressure',
    'vol_confirm', 'volume_z', 'volume_ratio', 'mfi_14', 'cum_pressure_slope',
    'mfi_divergence',
}
_STRUCTURE_FEATS = {
    'struct_hh', 'struct_hl', 'struct_lh', 'struct_ll',
    'breakout_up_10', 'breakout_up_20', 'breakout_down_10', 'breakout_down_20',
    'donchian_pos', 'bb_position', 'range_position_50',
    'at_donchian_high', 'at_donchian_low',
    'fvg_bull', 'fvg_bear', 'bull_ob', 'bear_ob', 'ichi_above_cloud',
}
_COMPLEXITY_FEATS = {
    'entropy_10', 'entropy_20', 'entropy_40', 'hurst_30', 'hurst_60', 'hurst_100',
    'higuchi_fd', 'dfa_alpha', 'variance_ratio', 'wpi', 'memory_pressure',
    'cct', 'lyapunov', 'ear_entropy', 'autocorr_1', 'realized_vol_20',
    'atr_pct', 'entropy_squeeze',
}

STRATEGY_DESCRIPTIONS = {
    'BREAKOUT':   'Ausbruch aus Konsolidierung mit Structure-Bestätigung (range_position, donchian, ichimoku)',
    'MOMENTUM':   'Momentum-Entry wenn Bewegung bereits läuft (RSI, MACD, EMA-Distanz)',
    'ORDERFLOW':  'CVD/OBV-Divergenz als primäres Signal — institutioneller Kauf-/Verkaufsdruck',
    'COMPLEXITY': 'Entropy & Hurst als Regime-Filter — Entry nur wenn Regime passt',
    'MEAN_REV':   'Fade Extrembewegungen — Preis kehrt zum Mittelwert zurück (Hurst < 0.5)',
    'SQUEEZE':    'Warte auf Volatilitätskompression → Ausbruch in eine Richtung',
    'HYBRID':     'Mehrere Ansätze gleichwertig — kombinierter Feature-Score',
}


def select_strategy(move_stats: dict, correlations: dict, movements: list,
                    validation_results: dict = None) -> tuple:
    """
    Determine the best trading strategy from forensics results.

    Args:
        move_stats:         {mtype: {'n': count, ...}}
        correlations:       {mtype: list_of_rows} where rows have t_statistic, predictive_pct, feature
        movements:          list of Movement objects
        validation_results: {mtype: {'reliability': {'label': ...}, 'use_in_bot': bool, ...}}

    Returns:
        strategy (str):              winning strategy type
        type_scores (dict):          score per strategy type
        tradeable_move_types (list): [{'move_type': str, 'direction': str}] — ROBUST/STABIL only
    """
    total = sum(s.get('n', 0) for s in move_stats.values()) or 1

    def _n(*keys):
        return sum(move_stats.get(k, {}).get('n', 0) for k in keys)

    impulse_n   = _n('IMPULSE_DOWN', 'IMPULSE_UP')
    breakdown_n = _n('BREAKDOWN', 'BREAKOUT_UP')
    reversal_n  = _n('REVERSAL_DOWN', 'REVERSAL_UP')
    squeeze_n   = _n('SQUEEZE_RELEASE_DOWN', 'SQUEEZE_RELEASE_UP')
    accel_n     = _n('ACCELERATION_DOWN', 'ACCELERATION_UP')

    cat_scores = {
        'MOMENTUM': 0.0, 'BREAKOUT': 0.0, 'MEAN_REV': 0.0,
        'SQUEEZE':  0.0, 'ORDERFLOW': 0.0, 'COMPLEXITY': 0.0,
    }

    for mtype, ranked_or_dict in correlations.items():
        ranked = (ranked_or_dict.get('rows', ranked_or_dict)
                  if isinstance(ranked_or_dict, dict) else ranked_or_dict)
        for r in ranked:
            t    = r.get('t', r.get('t_statistic', 0))
            hit  = r.get('hit', r.get('predictive_pct', 0))
            feat = r.get('feature', '')
            if abs(t) < 4.0 or hit < 35:
                continue
            weight = abs(t) * (hit / 100)
            if feat in _MOMENTUM_FEATS:
                cat_scores['MOMENTUM']  += weight
            elif feat in _VOLUME_FEATS:
                cat_scores['ORDERFLOW'] += weight
            elif feat in _STRUCTURE_FEATS:
                cat_scores['BREAKOUT']  += weight
            elif feat in _COMPLEXITY_FEATS:
                cat_scores['COMPLEXITY'] += weight

    if squeeze_n / total > 0.10:
        cat_scores['SQUEEZE'] += squeeze_n * 2

    for mtype, ranked_or_dict in correlations.items():
        ranked = (ranked_or_dict.get('rows', ranked_or_dict)
                  if isinstance(ranked_or_dict, dict) else ranked_or_dict)
        for r in ranked[:15]:
            if r.get('feature', '') in ('hurst_30', 'hurst_60', 'variance_ratio', 'autocorr_1'):
                cat_scores['MEAN_REV'] += abs(r.get('t', r.get('t_statistic', 0))) * 0.5

    type_scores = {
        'MOMENTUM':   (impulse_n + accel_n) / total * 100 + cat_scores['MOMENTUM'],
        'BREAKOUT':   breakdown_n / total * 100 + cat_scores['BREAKOUT'],
        'MEAN_REV':   reversal_n  / total * 100 + cat_scores['MEAN_REV'],
        'SQUEEZE':    squeeze_n   / total * 100 + cat_scores['SQUEEZE'],
        'ORDERFLOW':  cat_scores['ORDERFLOW'],
        'COMPLEXITY': cat_scores['COMPLEXITY'],
    }

    strategy = max(type_scores, key=lambda k: type_scores[k])
    top2 = sorted(type_scores.values(), reverse=True)[:2]
    if len(top2) > 1 and top2[0] > 0 and top2[1] / top2[0] > 0.75:
        strategy = 'HYBRID'

    # Tradeable types: only ROBUST or STABIL from OOS validation
    tradeable = []
    if validation_results:
        for mtype, vr in validation_results.items():
            rl = vr.get('reliability', {})
            label = rl.get('label', '') if isinstance(rl, dict) else str(rl)
            if label in ('ROBUST', 'STABIL'):
                direction = 'LONG' if ('UP' in mtype) else 'SHORT'
                tradeable.append({'move_type': mtype, 'direction': direction})

    # Fallback: all types with ≥20 events
    if not tradeable:
        for mtype, stats in move_stats.items():
            if stats.get('n', 0) >= 20:
                direction = 'LONG' if ('UP' in mtype) else 'SHORT'
                tradeable.append({'move_type': mtype, 'direction': direction})

    return strategy, type_scores, tradeable
