"""
HTML-Report Generator — Interaktives Dark-Theme Dashboard.
Tabs, sortierbare Tabellen, Suchbox, t-Stat-Filter, CSV-Export.
Funktioniert als standalone heruntergeladene Datei (kein Internet nötig).
"""
from pathlib import Path
from datetime import datetime
from collections import Counter
import json


def generate_html_report(
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    movements: list,
    correlations: dict,
    clusters: dict,
    output_path: str,
    validation_results: dict = None,
    correlations_meta: dict = None,
    split_date: str = '',
    movements_train_n: int = 0,
    movements_test_n: int = 0,
) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # ── Alle Daten als JSON für JS aufbereiten ────────────────────────────────
    type_counts = Counter(m.move_type for m in movements)
    up_count    = sum(1 for m in movements if m.direction == 'UP')
    dn_count    = len(movements) - up_count

    move_stats = {}
    for mtype, cnt in type_counts.items():
        ms = [m for m in movements if m.move_type == mtype]
        mags = [abs(m.magnitude_pct) for m in ms]
        move_stats[mtype] = {
            'n': cnt,
            'direction': ms[0].direction if ms else '?',
            'avg_mag': round(sum(mags)/len(mags), 2) if mags else 0,
            'max_mag': round(max(mags), 2) if mags else 0,
        }

    corr_data = {}
    for mtype, ranked_or_dict in correlations.items():
        ranked = ranked_or_dict.get('rows', ranked_or_dict) if isinstance(ranked_or_dict, dict) else ranked_or_dict
        meta_m   = (correlations_meta or {}).get(mtype, {})
        composite = meta_m.get('composite', {})
        corr_data[mtype] = {
            'rows': [
                {
                    'feature':   r['feature'],
                    't':         round(r['t_statistic'], 3),
                    'before':    round(float(r.get('mean_before', 0)), 5),
                    'baseline':  round(float(r.get('mean_all', 0)), 5),
                    'hit':       round(r.get('predictive_pct', 0), 1),
                    'n':         r.get('n_events', 0),
                    'ci_low':    round(r.get('hit_rate_ci_low', 0), 1),
                    'ci_high':   round(r.get('hit_rate_ci_high', 0), 1),
                    'cohens_d':  round(r.get('cohens_d', 0), 3),
                }
                for r in ranked if abs(r.get('t_statistic', 0)) >= 2.0
            ],
            'composite': composite,
            'low_count_warning': meta_m.get('low_count_warning', False),
            'n_events_train': meta_m.get('n_events', 0),
        }

    cluster_data = {}
    for cid, cdata in (clusters or {}).items():
        cluster_data[str(cid)] = {
            'n':     cdata.get('n', 0),
            'types': cdata.get('move_types', {}),
            'top':   cdata.get('top_features', [])[:10],
        }

    # Validation data für JS
    val_data = {}
    if validation_results:
        for mtype, vr in validation_results.items():
            val_data[mtype] = {
                'n_train':       vr.get('n_train', 0),
                'n_test':        vr.get('n_test', 0),
                'insample_hit':  vr.get('insample_hit', 0),
                'recall':        vr.get('recall_pct', 0),
                'precision':     vr.get('precision_pct', 0),
                'degradation':   vr.get('degradation', 0),
                'signal_fires':  vr.get('signal_fires', 0),
                'reliability':   vr.get('reliability', {}),
                'cond_perf':     vr.get('cond_performance', []),
                'n_conditions':  vr.get('n_conditions', 0),
            }

    fazit = _generate_fazit(symbol, timeframe, move_stats, correlations, up_count, dn_count,
                             movements, validation_results)

    payload = {
        'symbol':       symbol,
        'timeframe':    timeframe,
        'period':       f'{start_date} → {end_date}',
        'generated':    datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        'n_total':      len(movements),
        'up_count':     up_count,
        'dn_count':     dn_count,
        'move_stats':   move_stats,
        'correlations': corr_data,
        'clusters':     cluster_data,
        'fazit':        fazit,
        'validation':   val_data,
        'split_info': {
            'split_date':   split_date,
            'start_date':   start_date,
            'end_date':     end_date,
            'train_n':      movements_train_n,
            'test_n':       movements_test_n,
        },
    }

    html = _HTML_TEMPLATE.replace('__DATA_JSON__', json.dumps(payload, ensure_ascii=False))
    path.write_text(html, encoding='utf-8')
    return str(path)


# ─── Fazit Engine ────────────────────────────────────────────────────────────

_MOMENTUM_FEATS   = {'momentum_score','rsi_14','rsi_7','rsi_21','stoch_k','stoch_d','willr_14','cci_20','roc_10','roc_5','roc_20','macd_hist','kalman_vel','velocity','dist_ema_9','dist_ema_21'}
_VOLUME_FEATS     = {'cvd','cvd_slope','obv_z','obv_slope','buy_pressure','sell_pressure','vol_confirm','volume_z','volume_ratio','mfi_14','cum_pressure_slope','mfi_divergence'}
_STRUCTURE_FEATS  = {'struct_hh','struct_hl','struct_lh','struct_ll','breakout_up_10','breakout_up_20','breakout_down_10','breakout_down_20','donchian_pos','bb_position','range_position_50','at_donchian_high','at_donchian_low','fvg_bull','fvg_bear','bull_ob','bear_ob','ichi_above_cloud'}
_COMPLEXITY_FEATS = {'entropy_10','entropy_20','entropy_40','hurst_30','hurst_60','hurst_100','higuchi_fd','dfa_alpha','variance_ratio','wpi','memory_pressure','cct','lyapunov','ear_entropy','autocorr_1','realized_vol_20','atr_pct','entropy_squeeze'}

_STRATEGY_NAMES = {
    'MOMENTUM':    ('⚡ Momentum',   '#f5a623', 'Kaufe/Verkaufe wenn Momentum bereits läuft — kein antizyklischer Entry.'),
    'BREAKOUT':    ('🧱 Breakout',   '#58a6ff', 'Warte auf Ausbruch aus Konsolidierung mit Volume-Bestätigung.'),
    'MEAN_REV':    ('🔄 Mean-Reversion','#ce93d8','Fade Extrembewegungen — Preis kehrt zum Mittelwert zurück.'),
    'SQUEEZE':     ('⚡ Squeeze-Release','#26a69a','Warte auf Volatilitätskompression → Ausbruch in eine Richtung.'),
    'ORDERFLOW':   ('🏦 Order Flow', '#64b5f6', 'CVD/OBV-Divergenz als primäres Signal — institutioneller Kaufdruck.'),
    'COMPLEXITY':  ('🌀 Regime',     '#80cbc4', 'Entropy & Hurst als Filter — nur handeln wenn Regime passt.'),
    'HYBRID':      ('🔀 Hybrid',     '#aaa',    'Kombination mehrerer Ansätze — kein klares dominantes Muster.'),
}

def _generate_fazit(symbol, timeframe, move_stats, correlations, up_count, dn_count, movements, validation_results=None):
    total = sum(s['n'] for s in move_stats.values()) or 1
    coin  = symbol.split('/')[0]

    # ── 1. Bewegungstyp-Verteilung ────────────────────────────────────────────
    def _n(*keys): return sum(move_stats.get(k,{}).get('n',0) for k in keys)
    impulse_n  = _n('IMPULSE_DOWN','IMPULSE_UP')
    breakdown_n= _n('BREAKDOWN','BREAKOUT_UP')
    reversal_n = _n('REVERSAL_DOWN','REVERSAL_UP')
    squeeze_n  = _n('SQUEEZE_RELEASE_DOWN','SQUEEZE_RELEASE_UP')
    accel_n    = _n('ACCELERATION_DOWN','ACCELERATION_UP')

    # ── 2. Prädiktoren-Kategorien zählen (nur starke: |t|>=4, hit>=35) ────────
    cat_scores = {'MOMENTUM':0,'BREAKOUT':0,'MEAN_REV':0,'SQUEEZE':0,'ORDERFLOW':0,'COMPLEXITY':0}
    best_signals = []   # {feature, mtype, t, hit, direction, category}
    all_warnings = []

    for mtype, ranked_or_dict in correlations.items():
        ranked = ranked_or_dict.get('rows', ranked_or_dict) if isinstance(ranked_or_dict, dict) else ranked_or_dict
        n_events = move_stats.get(mtype, {}).get('n', 0)
        if n_events < 8:
            all_warnings.append(f"<b>{mtype}</b>: nur {n_events} Events — statistisch schwach (≥15 empfohlen)")

        for r in ranked:
            t   = r.get('t', r.get('t_statistic', 0))
            hit = r.get('hit', r.get('predictive_pct', 0))
            feat= r.get('feature','')
            if abs(t) < 4.0 or hit < 35:
                continue
            direction = 'erhöht' if t > 0 else 'erniedrigt'
            # Kategorie
            cat = None
            if feat in _MOMENTUM_FEATS:   cat = 'MOMENTUM'
            elif feat in _VOLUME_FEATS:   cat = 'ORDERFLOW'
            elif feat in _STRUCTURE_FEATS:cat = 'BREAKOUT'
            elif feat in _COMPLEXITY_FEATS:cat='COMPLEXITY'
            if cat:
                cat_scores[cat] += abs(t) * (hit/100)
            # Signal sammeln
            if abs(t) >= 5 and hit >= 45:
                best_signals.append({'feature':feat,'mtype':mtype,'t':round(t,2),'hit':round(hit,1),'direction':direction,'cat':cat or 'OTHER'})

    # Squeeze Sonderregel
    if squeeze_n / total > 0.10:
        cat_scores['SQUEEZE'] += squeeze_n * 2

    # Mean-Reversion: wenn hurst oder variance_ratio dominiert
    for mtype, ranked_or_dict in correlations.items():
        ranked = ranked_or_dict.get('rows', ranked_or_dict) if isinstance(ranked_or_dict, dict) else ranked_or_dict
        for r in ranked[:15]:
            if r.get('feature','') in ('hurst_30','hurst_60','variance_ratio','autocorr_1'):
                cat_scores['MEAN_REV'] += abs(r.get('t', r.get('t_statistic',0))) * 0.5

    # ── 3. Strategie-Typ bestimmen ────────────────────────────────────────────
    # Gewichtung: Bewegungstypen + Feature-Kategorie
    type_scores = {
        'MOMENTUM':  (impulse_n + accel_n) / total * 100 + cat_scores['MOMENTUM'],
        'BREAKOUT':  breakdown_n / total * 100 + cat_scores['BREAKOUT'],
        'MEAN_REV':  reversal_n / total * 100 + cat_scores['MEAN_REV'],
        'SQUEEZE':   squeeze_n / total * 100 + cat_scores['SQUEEZE'],
        'ORDERFLOW': cat_scores['ORDERFLOW'],
        'COMPLEXITY':cat_scores['COMPLEXITY'],
    }
    strategy = max(type_scores, key=lambda k: type_scores[k])
    # Kein klarer Gewinner → Hybrid
    top2 = sorted(type_scores.values(), reverse=True)[:2]
    if top2[0] > 0 and top2[1] / top2[0] > 0.75:
        strategy = 'HYBRID'
    confidence = min(100, int(top2[0]))

    # ── 4. Richtungs-Bias ─────────────────────────────────────────────────────
    ratio = up_count / (dn_count or 1)
    if ratio > 1.35:
        bias = 'LONG'
        bias_text = f'Long-Bias ({up_count} UP vs {dn_count} DOWN) — Markt tendierte in diesem Zeitraum aufwärts'
        bias_color= '#26a69a'
    elif ratio < 0.74:
        bias = 'SHORT'
        bias_text = f'Short-Bias ({dn_count} DOWN vs {up_count} UP) — Markt tendierte in diesem Zeitraum abwärts'
        bias_color= '#ef5350'
    else:
        bias = 'NEUTRAL'
        bias_text = f'Neutral ({up_count} UP / {dn_count} DOWN) — kein klarer Richtungs-Bias'
        bias_color= '#8b949e'

    # ── 5. Beste Signale deduplizieren & ranken ────────────────────────────────
    seen = set()
    top_signals = []
    for s in sorted(best_signals, key=lambda x: -(x['hit'] * abs(x['t']))):
        key = (s['feature'], s['mtype'])
        if key not in seen:
            seen.add(key)
            top_signals.append(s)
        if len(top_signals) >= 8:
            break

    # ── 6. Coin-Charakteristik ────────────────────────────────────────────────
    mags_all = [abs(m.magnitude_pct) for m in movements]
    avg_mag = round(sum(mags_all)/len(mags_all), 2) if mags_all else 0
    max_mag = round(max(mags_all), 2) if mags_all else 0
    volatility = 'HOCH' if avg_mag > 5 else 'MITTEL' if avg_mag > 2.5 else 'NIEDRIG'
    vol_color  = '#ef5350' if volatility=='HOCH' else '#f5a623' if volatility=='MITTEL' else '#26a69a'

    # ── 7. Empfehlungen generieren ────────────────────────────────────────────
    recs = []
    strat_name, strat_color, strat_desc = _STRATEGY_NAMES[strategy]

    if strategy == 'MOMENTUM':
        recs.append(f'Entry nur wenn Momentum bereits läuft — nicht gegen den Trend kaufen')
        if any(s['feature']=='momentum_score' for s in top_signals):
            recs.append(f'<code>momentum_score</code> ist stärkster Prädiktor — als primärer Filter nutzen')
        if any(s['feature'] in ('stoch_k','willr_14') for s in top_signals):
            recs.append(f'<code>stoch_k</code> / <code>willr_14</code> > 60 für Long, < 40 für Short als Einstiegssignal')
        recs.append(f'Trendfolge-Bot empfohlen — kein Mean-Reversion-Ansatz')

    elif strategy == 'BREAKOUT':
        recs.append(f'Warte auf Konsolidierung + Ausbruch — kein impulsiver Entry')
        if any(s['feature'] in ('bb_position','donchian_pos') for s in top_signals):
            recs.append(f'<code>bb_position</code> / <code>donchian_pos</code> > 0.8 bestätigt Ausbruch')
        recs.append(f'Volume-Bestätigung beim Ausbruch prüfen (<code>volume_z</code> > 1.0)')
        recs.append(f'SL knapp unter Ausbruchsniveau setzen')

    elif strategy == 'MEAN_REV':
        recs.append(f'Antizyklischer Entry bei Extremwerten — Geduld ist Key')
        recs.append(f'Hurst-Exponent < 0.45 als Pflicht-Filter vor jedem Entry')
        recs.append(f'RSI-Extremwerte (< 25 Long / > 75 Short) mit Volume-Bestätigung kombinieren')
        recs.append(f'Vorsicht: Mean-Reversion versagt in starken Trends — ADX < 25 als Filter')

    elif strategy == 'SQUEEZE':
        recs.append(f'Warte auf Bollinger-Band Kompression (bb_width auf Tiefstand)')
        recs.append(f'<code>entropy_squeeze</code> = 1 signalisiert aufgebaute Spannung')
        recs.append(f'Richtung beim Ausbruch bestimmt die Trade-Richtung — kein vorheriger Bias')

    elif strategy == 'ORDERFLOW':
        recs.append(f'CVD-Divergenz als primäres Entry-Signal (Preis steigt, CVD fällt = Warnung)')
        recs.append(f'<code>cum_pressure_slope</code> > 0 für Long, < 0 für Short')
        recs.append(f'Institutionelle Order Blocks (<code>bull_ob</code>/<code>bear_ob</code>) als SL-Level nutzen')

    elif strategy == 'COMPLEXITY':
        recs.append(f'Regime-Filter vor jedem Trade: nur handeln wenn Hurst > 0.5 (Trend) oder < 0.45 (Mean-Rev)')
        recs.append(f'Entropy-Anstieg = Chaos kommt — Position reduzieren')
        recs.append(f'Kombination Hurst + Entropy als Regime-Detektor für anderen Bot nutzen')

    else:  # HYBRID
        recs.append(f'Kein dominantes Muster erkennbar — Multi-Signal-Ansatz verwenden')
        recs.append(f'Mindestens 3 Signale verschiedener Kategorien für Entry kombinieren')
        recs.append(f'Backtesting mit verschiedenen Strategie-Typen empfohlen')

    # Allgemeine Empfehlungen
    if volatility == 'HOCH':
        recs.append(f'Hohe Volatilität (⌀ {avg_mag}% pro Move) — kleinere Positionsgrößen empfohlen')
    if bias != 'NEUTRAL':
        recs.append(f'{bias}-Bias beachten — Gegen-Trend Trades statistisch schwächer')

    # ── 8. Per-Move-Typ Mini-Fazit ────────────────────────────────────────────
    per_type = {}
    for mtype, ranked_or_dict in correlations.items():
        ranked = ranked_or_dict.get('rows', ranked_or_dict) if isinstance(ranked_or_dict, dict) else ranked_or_dict
        n = move_stats.get(mtype, {}).get('n', 0)
        if n < 3:
            continue
        strong = [r for r in ranked if abs(r.get('t', r.get('t_statistic',0))) >= 4 and r.get('hit', r.get('predictive_pct',0)) >= 40][:3]
        if not strong:
            continue
        signals = []
        for r in strong:
            t    = r.get('t', r.get('t_statistic',0))
            feat = r.get('feature','')
            hit  = r.get('hit', r.get('predictive_pct',0))
            before = r.get('before', r.get('mean_before',0))
            op = '>' if t > 0 else '<'
            signals.append(f'<code>{feat}</code> {op} {_fmt_val(before)} (Hit {hit:.0f}%)')

        direction = 'LONG' if 'UP' in mtype or 'BREAKOUT' in mtype else 'SHORT'
        per_type[mtype] = {
            'direction': direction,
            'n': n,
            'signals': signals,
            'avg_mag': move_stats.get(mtype,{}).get('avg_mag', 0),
        }

    # ── 9. Fließtext-Fazit ────────────────────────────────────────────────────
    fazit_text = _build_fazit_text(
        coin, timeframe, strategy, strat_name, bias, volatility,
        avg_mag, max_mag, top_signals, move_stats, total,
        impulse_n, breakdown_n, reversal_n, squeeze_n,
        validation_results=validation_results,
    )

    return {
        'strategy':     strategy,
        'strat_name':   strat_name,
        'strat_color':  strat_color,
        'strat_desc':   strat_desc,
        'confidence':   confidence,
        'bias':         bias,
        'bias_text':    bias_text,
        'bias_color':   bias_color,
        'volatility':   volatility,
        'vol_color':    vol_color,
        'avg_mag':      avg_mag,
        'max_mag':      max_mag,
        'top_signals':  top_signals,
        'recommendations': recs,
        'warnings':     all_warnings,
        'per_type':     per_type,
        'coin':         coin,
        'type_scores':  {k: round(v, 1) for k, v in type_scores.items()},
        'fazit_text':   fazit_text,
    }


def _build_fazit_text(coin, timeframe, strategy, strat_name, bias, volatility,
                      avg_mag, max_mag, top_signals, move_stats, total,
                      impulse_n, breakdown_n, reversal_n, squeeze_n,
                      validation_results=None):
    """Generiert einen zusammenhängenden Fließtext als Fazit."""

    parts = []

    # Satz 1: Grundcharakter des Coins
    dom_type = max(move_stats, key=lambda k: move_stats[k]['n']) if move_stats else '?'
    dom_n    = move_stats.get(dom_type, {}).get('n', 0)
    dom_pct  = round(dom_n / total * 100) if total else 0

    if impulse_n / total > 0.5:
        parts.append(
            f"{coin} ist ein <b>Momentum-Coin</b> — {impulse_n} von {total} Bewegungen ({int(impulse_n/total*100)}%) "
            f"waren schnelle Impulse mit durchschnittlich {avg_mag}% Magnitude (Max {max_mag}%). "
            f"Der Markt bewegt sich in starken, kurzen Schüben statt in langsamen Trends."
        )
    elif breakdown_n / total > 0.4:
        parts.append(
            f"{coin} zeigt ein <b>Breakout-Muster</b> — {breakdown_n} von {total} Bewegungen ({int(breakdown_n/total*100)}%) "
            f"entstanden durch Ausbrüche aus Konsolidierungszonen. "
            f"Der Kurs konsolidiert lange und explodiert dann mit ⌀ {avg_mag}%."
        )
    elif reversal_n / total > 0.2:
        parts.append(
            f"{coin} neigt zu <b>Mean-Reversion</b> — {reversal_n} Trendwenden in {total} analysierten Bewegungen. "
            f"Extrembewegungen werden statistisch häufiger umgekehrt als fortgesetzt."
        )
    elif squeeze_n / total > 0.1:
        parts.append(
            f"{coin} zeigt ausgeprägte <b>Squeeze-Release-Muster</b> — Volatilitätskompression "
            f"gefolgt von explosiven Ausbrüchen. Ideal für Volatilitäts-basierte Strategien."
        )
    else:
        parts.append(
            f"{coin} zeigt ein <b>gemischtes Bewegungsprofil</b> mit {total} Bewegungen über den Analysezeitraum "
            f"(⌀ {avg_mag}%, Max {max_mag}%). Kein dominanter Bewegungstyp erkennbar."
        )

    # Satz 2: Richtungs-Bias
    if bias == 'LONG':
        parts.append(
            f"Im analysierten Zeitraum gab es einen klaren <b>Long-Bias</b> — "
            f"Aufwärtsbewegungen überwiegen, was auf ein bullisches Marktumfeld hindeutet."
        )
    elif bias == 'SHORT':
        parts.append(
            f"Im analysierten Zeitraum dominierte ein <b>Short-Bias</b> — "
            f"der Markt tendierte abwärts. Short-Positionen waren statistisch häufiger profitabel."
        )
    else:
        parts.append(
            f"Die Bewegungsrichtungen sind <b>ausgewogen</b> — kein statistischer Long- oder Short-Vorteil "
            f"im Gesamtzeitraum erkennbar. Beide Richtungen sind gleich handelbar."
        )

    # Satz 3: Beste Entry-Signale
    best_by_hit = sorted(top_signals, key=lambda s: -s['hit'])[:3] if top_signals else []
    if best_by_hit:
        signal_names = ' + '.join(f"<code>{s['feature']}</code>" for s in best_by_hit)
        avg_hit = round(sum(s['hit'] for s in best_by_hit) / len(best_by_hit))
        parts.append(
            f"Die zuverlässigsten Entry-Signale sind {signal_names} "
            f"mit einer durchschnittlichen Hit-Rate von <b>{avg_hit}%</b>. "
            f"Diese Kombination war in {avg_hit}% aller analysierten Bewegungen im Vorfeld erhöht bzw. erniedrigt."
        )

    # Satz 4: Konkrete Strategie-Empfehlung
    if strategy == 'MOMENTUM':
        entry_signals = [s['feature'] for s in best_by_hit if s['cat'] == 'MOMENTUM'][:3]
        if entry_signals:
            sig_str = ' + '.join(f"<code>{f}</code>" for f in entry_signals)
            parts.append(
                f"<b>Empfehlung für einen neuen Bot auf {coin}:</b> "
                f"Momentum-Strategie — Entry wenn {sig_str} gleichzeitig in Trendrichtung zeigen. "
                f"Mean-Reversion-Ansätze sind statistisch unterlegen."
            )
        else:
            parts.append(
                f"<b>Empfehlung:</b> Momentum-Strategie — Entry nur wenn mehrere Momentum-Indikatoren "
                f"gleichzeitig in Trendrichtung zeigen. Nicht gegen den laufenden Impuls handeln."
            )
    elif strategy == 'BREAKOUT':
        parts.append(
            f"<b>Empfehlung für einen neuen Bot auf {coin}:</b> "
            f"Breakout-Strategie — Entry beim Ausbruch aus Konsolidierung mit Volume-Bestätigung. "
            f"SL knapp unter dem Ausbruchsniveau, TP basierend auf der vorherigen Range-Größe."
        )
    elif strategy == 'MEAN_REV':
        parts.append(
            f"<b>Empfehlung für einen neuen Bot auf {coin}:</b> "
            f"Mean-Reversion-Strategie — Entry bei RSI-Extremwerten (<25 Long / >75 Short) "
            f"kombiniert mit Hurst-Exponent < 0.45. ADX < 25 als Pflicht-Filter gegen starke Trends."
        )
    elif strategy == 'SQUEEZE':
        parts.append(
            f"<b>Empfehlung für einen neuen Bot auf {coin}:</b> "
            f"Squeeze-Release-Strategie — warte auf BB-Kompression (<code>bb_width</code> auf Tiefstand), "
            f"dann Entry beim ersten starken Ausbruch in die Richtung des Volumens."
        )
    elif strategy == 'ORDERFLOW':
        parts.append(
            f"<b>Empfehlung für einen neuen Bot auf {coin}:</b> "
            f"Order-Flow-Strategie — CVD-Divergenz als primäres Signal. "
            f"Wenn Preis steigt aber CVD fällt → Short-Setup. Institutionelle Order Blocks als Zonen."
        )
    elif strategy == 'COMPLEXITY':
        parts.append(
            f"<b>Empfehlung für einen neuen Bot auf {coin}:</b> "
            f"Regime-basierte Strategie — Hurst-Exponent und Entropy als Pflicht-Filter. "
            f"Nur handeln wenn das Regime klar ist (Hurst > 0.5 für Trend, < 0.45 für Mean-Rev)."
        )
    else:
        parts.append(
            f"<b>Empfehlung für einen neuen Bot auf {coin}:</b> "
            f"Kein dominantes Muster — Multi-Signal-Ansatz mit mindestens 3 unabhängigen "
            f"Bestätigungen aus verschiedenen Kategorien (Momentum, Volume, Structure) verwenden."
        )

    # Satz 5: Out-of-Sample Validierungs-Zusammenfassung
    if validation_results:
        robust   = [mt for mt, vr in validation_results.items()
                    if vr.get('reliability', {}).get('label') in ('ROBUST', 'STABIL')]
        overfitted = [mt for mt, vr in validation_results.items()
                      if vr.get('reliability', {}).get('label') == 'OVERFITTED']
        total_vr = len(validation_results)
        if total_vr > 0:
            if len(robust) >= total_vr * 0.6:
                parts.append(
                    f"<b>Out-of-Sample Validierung (30% unsichtbare Testdaten):</b> "
                    f"{len(robust)}/{total_vr} Signaltypen halten auch auf nie gesehenen Daten stand "
                    f"— die statistischen Muster sind <b style='color:#26a69a'>gut generalisiert</b>."
                )
            elif len(overfitted) > total_vr * 0.5:
                parts.append(
                    f"<b style='color:#ef5350'>Warnung — Out-of-Sample Validierung:</b> "
                    f"{len(overfitted)}/{total_vr} Signaltypen brechen auf unsichtbaren Testdaten zusammen "
                    f"— starker Overfitting-Verdacht. Signale in Echtzeit mit Vorsicht verwenden."
                )
            else:
                parts.append(
                    f"<b>Out-of-Sample Validierung:</b> Gemischtes Ergebnis — "
                    f"{len(robust)} stabile Typen, {len(overfitted)} potenzielle Overfits. "
                    f"Nur die stabilen Signale im Bot verwenden (siehe Validierungs-Tab)."
                )

    return ' '.join(parts)

def _fmt_val(v):
    try:
        f = float(v)
        return f'{f:.2f}' if abs(f) < 100 else f'{f:.0f}'
    except:
        return str(v)


# ─── Feature-Beschreibungen ───────────────────────────────────────────────────
_FEATURE_DESC = {
    'rsi_14': 'RSI (14) — Relative Strength Index, Überkauft/Überverkauft',
    'rsi_7':  'RSI (7) — Kurzfristiger RSI',
    'rsi_21': 'RSI (21) — Langfristiger RSI',
    'stoch_k': 'Stochastic %K — Momentum-Indikator 0-100',
    'stoch_d': 'Stochastic %D — Geglätteter %K',
    'willr_14': 'Williams %R — Überkauft/Überverkauft (-100 bis 0)',
    'cci_20': 'CCI (20) — Commodity Channel Index',
    'mfi_14': 'MFI (14) — Money Flow Index, Volume-gewichteter RSI',
    'adx': 'ADX — Trendstärke (>25 = starker Trend)',
    'di_plus': 'DI+ — Aufwärts-Direktionalindikator',
    'di_minus': 'DI- — Abwärts-Direktionalindikator',
    'di_delta': 'DI Delta — DI+ minus DI-',
    'macd': 'MACD — Moving Average Convergence Divergence',
    'macd_hist': 'MACD Histogramm — MACD minus Signal',
    'macd_signal': 'MACD Signal — 9-Bar EMA des MACD',
    'macd_hist_slope': 'MACD-Hist Slope — Richtungsänderung des Histogramms',
    'bb_position': 'BB Position — Preis innerhalb Bollinger Bands (0=unten, 1=oben)',
    'bb_width': 'BB Breite — Volatilitätsmaß der Bollinger Bands',
    'donchian_pos': 'Donchian Position — Preis im Donchian-Kanal',
    'donchian_width': 'Donchian Breite — Range der letzten N Bars',
    'atr_7': 'ATR (7) — Average True Range, kurzfristige Volatilität',
    'atr_14': 'ATR (14) — Average True Range, mittelfristige Volatilität',
    'atr_21': 'ATR (21) — Average True Range, langfristige Volatilität',
    'atr_pct': 'ATR % — ATR relativ zum Preis',
    'entropy_10': 'Shannon Entropy (10) — Marktunordnung kurzfristig',
    'entropy_20': 'Shannon Entropy (20) — Marktunordnung mittelfristig',
    'entropy_40': 'Shannon Entropy (40) — Marktunordnung langfristig',
    'hurst_30': 'Hurst-Exponent (30) — <0.5=Mean-Revert, >0.5=Trending',
    'hurst_60': 'Hurst-Exponent (60) — <0.5=Mean-Revert, >0.5=Trending',
    'hurst_100': 'Hurst-Exponent (100) — <0.5=Mean-Revert, >0.5=Trending',
    'higuchi_fd': 'Higuchi Fractal Dimension — Komplexität der Preisreihe',
    'dfa_alpha': 'DFA Alpha — Langzeitkorrelation (Detrended Fluctuation)',
    'kalman_vel': 'Kalman Velocity — Geglättete Geschwindigkeit',
    'variance_ratio': 'Variance Ratio — Mean-Reversion-Test (Lo & MacKinlay)',
    'wpi': 'WPI — Wick Pressure Imbalance (Kauf- vs. Verkaufsdruck)',
    'memory_pressure': 'Memory Pressure — Akkumulierter WPI (exp. Zerfall)',
    'cct': 'CCT — Candle Compression Tension (aufgestaute Energie)',
    'fft_dominant_period': 'FFT — Dominante Marktzykluslänge (Kerzen)',
    'hilbert_phase': 'Hilbert Phase — Instantane Phasenlage',
    'hilbert_phase_cos': 'Hilbert Phase cos — cos-Komponente der Phase',
    'hilbert_phase_sin': 'Hilbert Phase sin — sin-Komponente der Phase',
    'lyapunov': 'Lyapunov-Exponent — Chaosmaß des Marktes',
    'ear_entropy': 'EAR Entropy — Entropy of Absolute Returns',
    'cvd': 'CVD — Cumulative Volume Delta (Buy-Sell Volumen)',
    'cvd_slope': 'CVD Slope — Richtungsänderung des CVD',
    'obv': 'OBV — On-Balance Volume',
    'obv_z': 'OBV Z-Score — Normiertes OBV',
    'obv_slope': 'OBV Slope — Richtungsänderung des OBV',
    'volume_z': 'Volume Z-Score — Volumen relativ zum Durchschnitt',
    'volume_ratio': 'Volume Ratio — Aktuell vs. Durchschnittsvolumen',
    'buy_pressure': 'Buy Pressure — Geschätztes Kaufvolumen',
    'sell_pressure': 'Sell Pressure — Geschätztes Verkaufsvolumen',
    'vol_confirm': 'Vol Confirm — Volumen bestätigt Preisbewegung',
    'cum_pressure_slope': 'Cum Pressure Slope — Kumulierter Drucktrend',
    'momentum_score': 'Momentum Score — Kombinierter Momentum (-10/+10)',
    'trend_score': 'Trend Score — Kombinierter Trend (-10/+10)',
    'move_readiness': 'Move Readiness — Wahrscheinlichkeit einer Bewegung (0-10)',
    'struct_score': 'Structure Score — Marktstruktur-Stärke',
    'struct_hh': 'HH — Higher High (Aufwärtstrend-Indikator)',
    'struct_hl': 'HL — Higher Low (Aufwärtstrend-Indikator)',
    'struct_lh': 'LH — Lower High (Abwärtstrend-Indikator)',
    'struct_ll': 'LL — Lower Low (Abwärtstrend-Indikator)',
    'breakout_up_10': 'Breakout UP 10 — Preis über 10-Bar-High',
    'breakout_up_20': 'Breakout UP 20 — Preis über 20-Bar-High',
    'breakout_down_10': 'Breakout DOWN 10 — Preis unter 10-Bar-Low',
    'breakout_down_20': 'Breakout DOWN 20 — Preis unter 20-Bar-Low',
    'ichi_above_cloud': 'Ichimoku — Preis über der Wolke (bullish)',
    'ichi_tk_cross': 'Ichimoku TK Cross — Tenkan/Kijun Kreuzung',
    'range_position_50': 'Range Position (50) — Preis in 50-Bar-Range (0-1)',
    'range_compression_20': 'Range Compression (20) — Volatilitäts-Kompression',
    'fvg_bull': 'FVG Bullish — Fair Value Gap nach oben',
    'fvg_bear': 'FVG Bearish — Fair Value Gap nach unten',
    'bull_ob': 'Bull Order Block — Institutionelles Kaufinteresse',
    'bear_ob': 'Bear Order Block — Institutionelles Verkaufsinteresse',
    'velocity': 'Velocity — Preisgeschwindigkeit (MERS)',
    'energy': 'Energy — Kinetische Energie des Kurses',
    'price_vs_vwap': 'Preis vs VWAP — Abstand zum VWAP',
    'price_vs_poc_20': 'Preis vs POC (20) — Abstand zum Point of Control',
    'price_vs_poc_50': 'Preis vs POC (50) — Abstand zum Point of Control',
    'dist_ema_9': 'Dist EMA 9 — Abstand zur 9er EMA',
    'dist_ema_21': 'Dist EMA 21 — Abstand zur 21er EMA',
    'dist_ema_50': 'Dist EMA 50 — Abstand zur 50er EMA',
    'dist_ema_200': 'Dist EMA 200 — Abstand zur 200er EMA',
    'ema_alignment': 'EMA Alignment — EMAs in Reihenfolge (Trend)',
    'log_return_1': 'Log Return (1) — 1-Kerzen Return',
    'log_return_3': 'Log Return (3) — 3-Kerzen Return',
    'log_return_5': 'Log Return (5) — 5-Kerzen Return',
    'log_return_10': 'Log Return (10) — 10-Kerzen Return',
    'autocorr_1': 'Autocorr (lag 1) — Serielle Korrelation',
    'realized_vol_20': 'Realized Volatility (20) — Historische Volatilität',
    'consec_bull': 'Consec Bull — Aufeinanderfolgende Bullish-Kerzen',
    'consec_bear': 'Consec Bear — Aufeinanderfolgende Bearish-Kerzen',
    'candle_dir': 'Candle Dir — Kerzenrichtung (+1/-1)',
    'body': 'Body — Kerzenkörper in Punkten',
    'body_ratio': 'Body Ratio — Körper zu Gesamtrange',
    'upper_wick': 'Upper Wick — Oberer Docht',
    'lower_wick': 'Lower Wick — Unterer Docht',
    'entropy_squeeze': 'Entropy Squeeze — Entropy stark komprimiert',
    'mfi_divergence': 'MFI Divergenz — Preis vs MFI divergieren',
    'rsi_divergence': 'RSI Divergenz — Preis vs RSI divergieren',
}

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Probebot Report</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',monospace;font-size:13px;line-height:1.5}
a{color:#58a6ff;text-decoration:none}
/* Layout */
.header{background:linear-gradient(135deg,#161b22,#1a2332);border-bottom:1px solid #30363d;padding:20px 28px}
.header h1{font-size:1.4em;color:#e6edf3;font-weight:700}
.header .meta{color:#8b949e;font-size:.85em;margin-top:5px;display:flex;gap:18px;flex-wrap:wrap}
.pills{display:flex;gap:10px;padding:14px 28px;background:#161b22;border-bottom:1px solid #30363d;flex-wrap:wrap}
.pill{padding:4px 12px;border-radius:20px;font-size:.82em;font-weight:600;border:1px solid}
/* Tabs */
.tab-bar{display:flex;gap:2px;padding:0 28px;background:#0d1117;border-bottom:2px solid #21262d;overflow-x:auto;scrollbar-width:thin}
.tab{padding:10px 16px;cursor:pointer;color:#8b949e;border-bottom:2px solid transparent;margin-bottom:-2px;white-space:nowrap;font-size:.83em;font-weight:600;transition:color .15s}
.tab:hover{color:#c9d1d9}
.tab.active{color:#58a6ff;border-bottom-color:#58a6ff}
/* Panels */
.panel{display:none;padding:20px 28px}
.panel.active{display:block}
/* Controls */
.controls{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;align-items:center}
.controls input[type=text]{background:#161b22;border:1px solid #30363d;color:#c9d1d9;padding:6px 12px;border-radius:6px;font-size:.85em;width:220px;outline:none}
.controls input[type=text]:focus{border-color:#58a6ff}
.controls label{color:#8b949e;font-size:.82em;display:flex;align-items:center;gap:8px}
.controls input[type=range]{accent-color:#58a6ff;width:120px}
.btn{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:.82em;transition:background .15s}
.btn:hover{background:#30363d}
/* Stats row */
.stats-row{display:flex;gap:12px;margin-bottom:18px;flex-wrap:wrap}
.stat-box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;min-width:120px;text-align:center}
.stat-val{font-size:1.6em;font-weight:700}
.stat-lbl{font-size:.75em;color:#8b949e;text-transform:uppercase;letter-spacing:.05em;margin-top:2px}
/* Table */
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.83em}
th{padding:8px 12px;text-align:left;color:#8b949e;font-weight:600;font-size:.78em;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid #21262d;background:#161b22;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:#c9d1d9}
th .sort-icon{margin-left:4px;opacity:.5}
th.sorted .sort-icon{opacity:1}
td{padding:7px 12px;border-bottom:1px solid #1c2128;font-family:'SFMono-Regular',Consolas,monospace;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#1c2128}
.bar-cell{display:flex;align-items:center;gap:8px}
.bar{height:7px;border-radius:3px;min-width:3px;transition:width .2s}
/* Tooltip */
.feat-name{position:relative;cursor:help;border-bottom:1px dashed #444}
.feat-name:hover::after{content:attr(data-tip);position:absolute;left:0;top:100%;z-index:99;background:#1c2128;border:1px solid #30363d;color:#c9d1d9;padding:6px 10px;border-radius:6px;font-size:.8em;white-space:nowrap;max-width:340px;white-space:normal;min-width:200px;margin-top:4px;line-height:1.4;pointer-events:none}
/* Hit-rate color */
.hit-high{color:#26a69a;font-weight:700}
.hit-mid{color:#f5a623;font-weight:600}
.hit-low{color:#888}
/* Overview cards */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:24px}
.ov-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 16px;border-left:3px solid}
.ov-card .ov-type{font-size:.78em;color:#8b949e;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
.ov-card .ov-n{font-size:1.8em;font-weight:700;margin-bottom:2px}
.ov-card .ov-mag{font-size:.82em;color:#8b949e}
/* Cluster */
.cluster-card{background:#161b22;border:1px solid #30363d;border-left:3px solid #ce93d8;border-radius:8px;padding:14px 16px;margin-bottom:12px}
.cluster-card h4{color:#ce93d8;margin-bottom:8px;font-size:.9em}
.cluster-feats{list-style:none;font-size:.82em;columns:2;gap:12px}
.cluster-feats li{margin-bottom:3px}
/* Empty state */
.empty{color:#8b949e;padding:32px 0;text-align:center;font-size:.9em}
/* scrollbar */
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:#0d1117}
::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}
</style>
</head>
<body>

<div class="header">
  <h1>🔬 Probebot — Market Forensics Dashboard</h1>
  <div class="meta" id="hdr-meta"></div>
</div>
<div class="pills" id="hdr-pills"></div>
<div class="tab-bar" id="tab-bar"></div>
<div id="panels"></div>

<script>
const FEAT_DESC = __FEAT_DESC_JSON__;
const D = __DATA_JSON__;

// ── helpers ──────────────────────────────────────────────────────────────────
const $ = (sel, ctx=document) => ctx.querySelector(sel);
const $$ = (sel, ctx=document) => [...ctx.querySelectorAll(sel)];
const fmt = v => {
  const n = parseFloat(v);
  if(isNaN(n)) return v;
  if(Math.abs(n)>=10000) return n.toLocaleString('de',{maximumFractionDigits:0});
  if(Math.abs(n)>=100)   return n.toFixed(1);
  return n.toFixed(4);
};
const typeColor = t => (t.includes('UP')||t.includes('BREAKOUT')) ? '#26a69a' : '#ef5350';
const hitClass  = h => h>=60?'hit-high':h>=35?'hit-mid':'hit-low';

// ── Header ────────────────────────────────────────────────────────────────────
$('#hdr-meta').innerHTML =
  `<span>📊 ${D.symbol}</span><span>⏱ ${D.timeframe}</span>` +
  `<span>📅 ${D.period}</span><span>🕒 ${D.generated}</span>`;
$('#hdr-pills').innerHTML =
  `<span class="pill" style="color:#26a69a;border-color:#26a69a44;background:#26a69a11">▲ ${D.up_count} Aufwärts</span>` +
  `<span class="pill" style="color:#ef5350;border-color:#ef535044;background:#ef535011">▼ ${D.dn_count} Abwärts</span>` +
  `<span class="pill" style="color:#8b949e;border-color:#30363d;background:#161b22">Σ ${D.n_total} Bewegungen</span>`;

// ── Build tabs ────────────────────────────────────────────────────────────────
const tabBar  = $('#tab-bar');
const panelEl = $('#panels');

function addTab(id, label, builder) {
  const t = document.createElement('div');
  t.className = 'tab'; t.dataset.id = id; t.textContent = label;
  t.onclick = () => activateTab(id);
  tabBar.appendChild(t);

  const p = document.createElement('div');
  p.className = 'panel'; p.id = 'panel-'+id;
  panelEl.appendChild(p);
  // lazy-build content on first activation
  p._builder = builder; p._built = false;
}

function activateTab(id) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.id===id));
  $$('.panel').forEach(p => {
    const active = p.id === 'panel-'+id;
    p.classList.toggle('active', active);
    if(active && !p._built) { p._builder(p); p._built=true; }
  });
}

// ── Overview Tab ─────────────────────────────────────────────────────────────
addTab('overview','📊 Übersicht', p => {
  let cards = '';
  Object.entries(D.move_stats).sort((a,b)=>b[1].n-a[1].n).forEach(([mt,s])=>{
    const c = typeColor(mt);
    cards += `<div class="ov-card" style="border-left-color:${c}">
      <div class="ov-type">${mt.replace(/_/g,' ')}</div>
      <div class="ov-n" style="color:${c}">${s.n}</div>
      <div class="ov-mag">⌀ ${s.avg_mag}%  max ${s.max_mag}%</div>
    </div>`;
  });

  // Best predictors across all types
  let allPred = [];
  Object.entries(D.correlations).forEach(([mt, cd]) => {
    const rows = cd.rows || [];
    rows.slice(0,5).forEach(r => allPred.push({...r, mtype:mt}));
  });
  allPred.sort((a,b)=>Math.abs(b.t)-Math.abs(a.t));

  let predRows = '';
  allPred.slice(0,20).forEach(r => {
    const c = r.t>0?'#26a69a':'#ef5350';
    const ar = r.t>0?'↑':'↓';
    predRows += `<tr>
      <td><span class="feat-name" data-tip="${FEAT_DESC[r.feature]||r.feature}">${r.feature}</span></td>
      <td style="color:#8b949e;font-size:.8em">${r.mtype.replace(/_/g,' ')}</td>
      <td><div class="bar-cell"><div class="bar" style="background:${c};width:${Math.min(100,Math.abs(r.t)/15*100)}px"></div>
        <span style="color:${c};font-weight:700">${ar}${Math.abs(r.t).toFixed(2)}</span></div></td>
      <td class="${hitClass(r.hit)}">${r.hit}%</td>
    </tr>`;
  });

  p.innerHTML = `
    <div class="ov-grid">${cards}</div>
    <h3 style="color:#e6edf3;margin-bottom:12px;font-size:.95em">🏆 Stärkste Prädiktoren (alle Typen)</h3>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Feature</th><th>Bewegungstyp</th><th>t-Statistik</th><th>Hit-Rate</th></tr></thead>
      <tbody>${predRows}</tbody>
    </table></div>`;
});

// ── Helper: structured correlations ──────────────────────────────────────────
function getRows(mtype) { return ((D.correlations[mtype]||{}).rows)||[]; }
function getMeta(mtype) { return D.correlations[mtype]||{}; }

// ── Per-type Tabs ─────────────────────────────────────────────────────────────
Object.keys(D.correlations).sort().forEach(mtype => {
  const label = mtype.replace(/_/g,' ');
  const c = typeColor(mtype);
  addTab(mtype, label, p => buildTypePanel(p, mtype, c));
});

// ── Cluster Tab ───────────────────────────────────────────────────────────────
if(Object.keys(D.clusters).length) {
  addTab('clusters','🧬 Cluster', p => {
    let html = '';
    Object.entries(D.clusters).forEach(([cid, cd])=>{
      const types = Object.entries(cd.types).map(([k,v])=>`${k}: ${v}`).join(' | ');
      const feats = cd.top.map(f=>{
        const c2 = f.diff>0?'#26a69a':'#ef5350';
        return `<li style="color:${c2}">${f.diff>0?'↑':'↓'} ${f.feature} (${f.diff>0?'+':''}${(f.diff||0).toFixed(3)})</li>`;
      }).join('');
      html += `<div class="cluster-card">
        <h4>Cluster ${cid} &nbsp;<span style="color:#8b949e;font-weight:400;font-size:.85em">${cd.n} Events</span></h4>
        <div style="color:#aaa;font-size:.82em;margin-bottom:8px">${types}</div>
        <ul class="cluster-feats">${feats}</ul>
      </div>`;
    });
    p.innerHTML = html || '<div class="empty">Keine Cluster-Daten.</div>';
  });
}

// ── Type Panel Builder ────────────────────────────────────────────────────────
function buildTypePanel(p, mtype, color) {
  const rows  = getRows(mtype);
  const meta  = getMeta(mtype);
  const stats = D.move_stats[mtype] || {};
  let sortCol = 't', sortDir = -1;
  let filterText = '', filterT = 2.0;

  // ── Validation badge ──
  const vd = (D.validation||{})[mtype];
  let valBadge = '';
  if(vd) {
    const rl = vd.reliability || {};
    valBadge = `<span style="background:${rl.color||'#555'};color:#fff;border-radius:4px;padding:2px 8px;font-size:.75em;margin-left:8px">${rl.icon||''} ${rl.label||'?'} OOS ${vd.recall}%</span>`;
  }

  // ── Composite score banner ──
  const comp = meta.composite || {};
  const compHtml = comp.description
    ? `<div style="background:#1a2235;border-left:3px solid #f5a623;padding:8px 12px;margin-bottom:10px;font-size:.82em;color:#ccc;border-radius:0 4px 4px 0">
        <b style="color:#f5a623">🎯 Composite-Signal:</b> ${comp.description}
       </div>`
    : '';

  // ── Low count warning ──
  const warnHtml = meta.low_count_warning
    ? `<div style="background:#332200;border-left:3px solid #f5a623;padding:6px 12px;margin-bottom:10px;font-size:.8em;color:#f5a623;border-radius:0 4px 4px 0">
        ⚠️ Nur ${meta.n_events_train||'?'} Trainings-Events — statistische Aussagekraft eingeschränkt (Minimum: 20)
       </div>`
    : '';

  // ── Stats ──
  const statsHtml = `<div class="stats-row">
    <div class="stat-box"><div class="stat-val" style="color:${color}">${stats.n||0}</div><div class="stat-lbl">Events</div></div>
    <div class="stat-box"><div class="stat-val">${stats.avg_mag||0}%</div><div class="stat-lbl">⌀ Magnitude</div></div>
    <div class="stat-box"><div class="stat-val">${stats.max_mag||0}%</div><div class="stat-lbl">Max Move</div></div>
    <div class="stat-box"><div class="stat-val">${rows.length}</div><div class="stat-lbl">Prädiktoren ${valBadge}</div></div>
  </div>`;

  // ── Controls ──
  const ctrlId = 'ctrl-'+mtype;
  const tblId  = 'tbl-'+mtype;
  const slId   = 'sl-'+mtype;
  const slLblId= 'slLbl-'+mtype;

  p.innerHTML = statsHtml + warnHtml + compHtml + `
    <div class="controls">
      <input type="text" id="${ctrlId}" placeholder="Feature suchen…" oninput="filterTable('${mtype}')">
      <label>t-Stat ≥ <span id="${slLblId}">2.0</span>
        <input type="range" id="${slId}" min="2" max="12" step="0.5" value="2"
          oninput="document.getElementById('${slLblId}').textContent=this.value;filterTable('${mtype}')">
      </label>
      <button class="btn" onclick="exportCSV('${mtype}')">⬇ CSV</button>
    </div>
    <div class="tbl-wrap"><table id="${tblId}">
      <thead><tr>
        <th onclick="sortTable('${mtype}','feature',1)" data-col="feature">Feature <span class="sort-icon">⇅</span></th>
        <th onclick="sortTable('${mtype}','t',-1)"      data-col="t" class="sorted">t-Statistik <span class="sort-icon">↓</span></th>
        <th onclick="sortTable('${mtype}','before',1)"  data-col="before">Vor Move <span class="sort-icon">⇅</span></th>
        <th onclick="sortTable('${mtype}','baseline',1)"data-col="baseline">Gesamt ⌀ <span class="sort-icon">⇅</span></th>
        <th onclick="sortTable('${mtype}','hit',-1)"    data-col="hit">Hit-Rate <span class="sort-icon">⇅</span></th>
        <th data-col="ci">95% CI</th>
      </tr></thead>
      <tbody id="tbody-${mtype}"></tbody>
    </table></div>`;

  renderRows(mtype, rows, color, sortCol, sortDir, '', 2.0);
}

// State per type
const _state = {};
function getState(mtype) {
  if(!_state[mtype]) _state[mtype] = {sortCol:'t', sortDir:-1};
  return _state[mtype];
}

function renderRows(mtype, allRows, color, sortCol, sortDir, text, minT) {
  let rows = allRows.filter(r =>
    Math.abs(r.t) >= minT &&
    (!text || r.feature.toLowerCase().includes(text.toLowerCase()))
  );
  rows.sort((a,b) => {
    let av = a[sortCol], bv = b[sortCol];
    if(typeof av==='string') return sortDir * av.localeCompare(bv);
    return sortDir * (av - bv);
  });

  const maxT = rows.reduce((m,r)=>Math.max(m,Math.abs(r.t)),1);
  const tbody = document.getElementById('tbody-'+mtype);
  if(!tbody) return;

  if(!rows.length){tbody.innerHTML='<tr><td colspan="5" class="empty">Keine Features gefunden.</td></tr>';return;}

  tbody.innerHTML = rows.map(r=>{
    const c  = r.t>0?'#26a69a':'#ef5350';
    const ar = r.t>0?'↑':'↓';
    const bw = Math.round(Math.abs(r.t)/maxT*90);
    const tip = FEAT_DESC[r.feature] || r.feature;
    return `<tr>
      <td><span class="feat-name" data-tip="${tip}">${ar} ${r.feature}</span></td>
      <td><div class="bar-cell">
        <div class="bar" style="background:${c};width:${bw}px"></div>
        <span style="color:${c};font-weight:700">${r.t>0?'+':''}${r.t.toFixed(2)}</span>
      </div></td>
      <td style="color:#e0e0e0">${fmt(r.before)}</td>
      <td style="color:#8b949e">${fmt(r.baseline)}</td>
      <td class="${hitClass(r.hit)}">${r.hit}%</td>
      <td style="color:#8b949e;font-size:.78em">${r.ci_low&&r.ci_high ? r.ci_low+'%–'+r.ci_high+'%' : '—'}</td>
    </tr>`;
  }).join('');
}

window.filterTable = function(mtype) {
  const text = document.getElementById('ctrl-'+mtype)?.value || '';
  const minT = parseFloat(document.getElementById('sl-'+mtype)?.value || 2);
  const s = getState(mtype);
  s.text = text; s.minT = minT;
  renderRows(mtype, getRows(mtype), typeColor(mtype), s.sortCol, s.sortDir, text, minT);
};

window.sortTable = function(mtype, col, defDir) {
  const s = getState(mtype);
  if(s.sortCol === col) s.sortDir *= -1;
  else { s.sortCol = col; s.sortDir = defDir; }
  // update header icons
  const tbl = document.getElementById('tbl-'+mtype);
  if(tbl) $$('th', tbl).forEach(th=>{
    const isActive = th.dataset.col === col;
    th.classList.toggle('sorted', isActive);
    const icon = th.querySelector('.sort-icon');
    if(icon) icon.textContent = isActive ? (s.sortDir>0?'↑':'↓') : '⇅';
  });
  renderRows(mtype, getRows(mtype), typeColor(mtype),
    s.sortCol, s.sortDir, s.text||'', s.minT||2);
};

window.exportCSV = function(mtype) {
  const rows = getRows(mtype);
  const header = 'feature,t_statistic,vor_move,gesamt_avg,hit_rate_pct,ci_low,ci_high\n';
  const body = rows.map(r=>`${r.feature},${r.t},${r.before},${r.baseline},${r.hit},${r.ci_low||''},${r.ci_high||''}`).join('\n');
  const blob = new Blob([header+body], {type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `probebot_${mtype}.csv`;
  a.click();
};

// ── Validierung Tab ──────────────────────────────────────────────────────────
if(D.validation && Object.keys(D.validation).length) {
  addTab('validation','✅ Validierung', p => {
    const V = D.validation;
    const SI = D.split_info || {};

    // Timeline bar
    const trainPct = SI.train_n && SI.train_n+SI.test_n>0
      ? Math.round(SI.train_n/(SI.train_n+SI.test_n)*100) : 70;
    const testPct  = 100 - trainPct;

    let html = `
    <div style="background:#0d1117;border-radius:8px;padding:16px;margin-bottom:18px">
      <div style="font-size:.85em;color:#8b949e;margin-bottom:8px">Datentrennung — strikt temporal (Bot hat Test-Daten NIEMALS gesehen)</div>
      <div style="display:flex;border-radius:6px;overflow:hidden;height:28px;font-size:.78em;font-weight:700">
        <div style="width:${trainPct}%;background:#2d4a2d;display:flex;align-items:center;padding:0 10px;color:#7ec87e">
          70% Training ${SI.start_date||''} → ${SI.split_date||''} (${SI.train_n||'?'} Events)
        </div>
        <div style="width:${testPct}%;background:#4a2d2d;display:flex;align-items:center;padding:0 10px;color:#e08080">
          30% Test ${SI.split_date||''} → ${SI.end_date||''} (${SI.test_n||'?'} Events)
        </div>
      </div>
      <div style="font-size:.75em;color:#8b949e;margin-top:6px">
        Lernalgorithmen (Welch t-Test, Pattern Mining, Clustering) liefen ausschließlich auf dem Training-Zeitraum.
        Die Test-Daten wurden nur für die Validierung der gelernten Signale verwendet.
      </div>
    </div>`;

    // Overview cards
    const total = Object.keys(V).length;
    const robust = Object.values(V).filter(v=>(v.reliability||{}).label==='ROBUST').length;
    const stabil = Object.values(V).filter(v=>(v.reliability||{}).label==='STABIL').length;
    const schwach= Object.values(V).filter(v=>(v.reliability||{}).label==='SCHWACH').length;
    const overfit= Object.values(V).filter(v=>(v.reliability||{}).label==='OVERFITTED').length;

    html += `<div class="stats-row" style="margin-bottom:20px">
      <div class="stat-box"><div class="stat-val" style="color:#26a69a">${robust}</div><div class="stat-lbl">✅ ROBUST</div></div>
      <div class="stat-box"><div class="stat-val" style="color:#f5a623">${stabil}</div><div class="stat-lbl">🟡 STABIL</div></div>
      <div class="stat-box"><div class="stat-val" style="color:#ef8c00">${schwach}</div><div class="stat-lbl">⚠️ SCHWACH</div></div>
      <div class="stat-box"><div class="stat-val" style="color:#ef5350">${overfit}</div><div class="stat-lbl">❌ OVERFITTED</div></div>
    </div>`;

    // Per-type validation cards
    Object.entries(V).forEach(([mtype, vd]) => {
      const rl = vd.reliability || {};
      const degradation = vd.degradation || 0;
      const degColor = degradation > 25 ? '#ef5350' : degradation > 10 ? '#f5a623' : '#26a69a';

      // Per-condition table
      let condRows = '';
      (vd.cond_perf||[]).forEach(c=>{
        const delta = (c.test_hit||0) - (c.train_hit||0);
        const dc = delta < -15 ? '#ef5350' : delta < 0 ? '#f5a623' : '#26a69a';
        condRows += `<tr>
          <td style="font-size:.8em;color:#e0e0e0">${c.feature}</td>
          <td style="color:#8b949e;font-size:.8em">${c.t_statistic>0?'↑':'↓'} t=${(c.t_statistic||0).toFixed(1)}</td>
          <td class="${hitClass(c.train_hit)}">${(c.train_hit||0).toFixed(0)}%</td>
          <td class="${hitClass(c.test_hit)}" style="color:${dc}">${(c.test_hit||0).toFixed(0)}% (${delta>0?'+':''}${delta.toFixed(0)})</td>
        </tr>`;
      });
      const condTbl = condRows ? `<div class="tbl-wrap" style="margin-top:10px"><table>
        <thead><tr><th>Feature</th><th>Richtung</th><th>Train Hit-Rate</th><th>OOS Hit-Rate (Δ)</th></tr></thead>
        <tbody>${condRows}</tbody></table></div>` : '';

      html += `<div style="background:#161b22;border-radius:8px;padding:16px;margin-bottom:14px;border-left:4px solid ${rl.color||'#555'}">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <div>
            <span style="color:#e6edf3;font-weight:700">${mtype.replace(/_/g,' ')}</span>
            <span style="background:${rl.color||'#555'};color:#fff;border-radius:4px;padding:2px 8px;font-size:.75em;margin-left:8px">${rl.icon||''} ${rl.label||'?'}</span>
          </div>
          <div style="font-size:.78em;color:#8b949e">Train: ${vd.n_train||0} Events | Test: ${vd.n_test||0} Events</div>
        </div>

        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px">
          <div style="text-align:center">
            <div style="font-size:1.3em;font-weight:700;color:#8b949e">${vd.insample_hit||0}%</div>
            <div style="font-size:.75em;color:#8b949e">In-Sample Hit</div>
          </div>
          <div style="text-align:center">
            <div style="font-size:1.3em;font-weight:700;color:${rl.color||'#aaa'}">${vd.recall||0}%</div>
            <div style="font-size:.75em;color:#8b949e">OOS Recall</div>
          </div>
          <div style="text-align:center">
            <div style="font-size:1.3em;font-weight:700;color:${rl.color||'#aaa'}">${vd.precision||0}%</div>
            <div style="font-size:.75em;color:#8b949e">OOS Precision</div>
          </div>
          <div style="text-align:center">
            <div style="font-size:1.3em;font-weight:700;color:${degColor}">-${degradation||0}%</div>
            <div style="font-size:.75em;color:#8b949e">Degradation</div>
          </div>
        </div>

        <div style="background:#0d1117;border-radius:4px;padding:8px 12px;font-size:.78em;color:${rl.color||'#aaa'};margin-bottom:8px">
          ${rl.icon||''} ${rl.text||''}
          ${vd.signal_fires>0 ? ' — Signal feuerte '+vd.signal_fires+'× im Test-Zeitraum' : ''}
        </div>
        ${condTbl}
      </div>`;
    });

    p.innerHTML = html || '<div class="empty">Keine Validierungsdaten.</div>';
  });
}

// ── Fazit Tab ─────────────────────────────────────────────────────────────────
addTab('fazit','🎯 Fazit & Empfehlung', p => {
  const F = D.fazit;
  if(!F){ p.innerHTML='<div class="empty">Keine Fazit-Daten.</div>'; return; }

  // Strategy score bars
  const scoreEntries = Object.entries(F.type_scores||{}).sort((a,b)=>b[1]-a[1]);
  const maxScore = scoreEntries[0]?.[1] || 1;
  const scoreBars = scoreEntries.map(([k,v])=>{
    const w = Math.round(v/maxScore*100);
    const active = k===F.strategy;
    const c = active ? F.strat_color : '#30363d';
    return `<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <div style="width:120px;font-size:.8em;color:${active?F.strat_color:'#8b949e'};font-weight:${active?700:400}">${k}</div>
      <div style="flex:1;background:#1c2128;border-radius:4px;height:10px">
        <div style="width:${w}%;background:${c};height:10px;border-radius:4px;transition:width .3s"></div>
      </div>
      <div style="width:36px;text-align:right;font-size:.8em;color:${active?F.strat_color:'#888'}">${v}</div>
    </div>`;
  }).join('');

  // Warnings
  const warns = (F.warnings||[]).map(w=>
    `<div style="background:#f5a62311;border:1px solid #f5a62344;border-radius:6px;padding:8px 12px;margin-bottom:6px;font-size:.83em">
      ⚠️ ${w}
    </div>`).join('') || '';

  // Top signals
  const catColors = {MOMENTUM:'#f5a623',BREAKOUT:'#58a6ff',ORDERFLOW:'#64b5f6',COMPLEXITY:'#80cbc4',MEAN_REV:'#ce93d8',OTHER:'#888'};
  const sigRows = (F.top_signals||[]).map(s=>{
    const c = s.t>0?'#26a69a':'#ef5350';
    const cat_c = catColors[s.cat]||'#888';
    return `<tr>
      <td><span style="color:${cat_c};font-size:.75em;border:1px solid ${cat_c}44;padding:1px 6px;border-radius:4px">${s.cat}</span></td>
      <td><span class="feat-name" data-tip="${FEAT_DESC[s.feature]||s.feature}" style="color:#e0e0e0">${s.feature}</span></td>
      <td style="color:#8b949e;font-size:.82em">${s.mtype.replace(/_/g,' ')}</td>
      <td style="color:${c};font-weight:700">${s.t>0?'+':''}${s.t}</td>
      <td class="${s.hit>=60?'hit-high':s.hit>=40?'hit-mid':'hit-low'}">${s.hit}%</td>
      <td style="color:#aaa;font-size:.82em">${s.direction}</td>
    </tr>`;
  }).join('');

  // Recommendations
  const recItems = (F.recommendations||[]).map(r=>
    `<li style="margin-bottom:7px;color:#c9d1d9">${r}</li>`).join('');

  // Per-type mini conclusions
  const perTypeSections = Object.entries(F.per_type||{}).map(([mt,td])=>{
    const c = mt.includes('UP')||mt.includes('BREAKOUT')?'#26a69a':'#ef5350';
    const dir = td.direction==='LONG'?'▲ LONG':'▼ SHORT';
    const sigs = td.signals.map(s=>`<li style="margin-bottom:4px">${s}</li>`).join('');
    return `<div style="background:#1a1f2e;border:1px solid #30363d;border-left:3px solid ${c};border-radius:6px;padding:12px 14px;margin-bottom:8px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <span style="color:${c};font-weight:700;font-size:.88em">${mt.replace(/_/g,' ')}</span>
        <span style="color:${c};font-size:.8em">${dir}</span>
        <span style="color:#8b949e;font-size:.78em">${td.n} Events · ⌀ ${td.avg_mag}%</span>
      </div>
      <ul style="list-style:none;padding:0;font-size:.82em;color:#c9d1d9">${sigs}</ul>
    </div>`;
  }).join('');

  p.innerHTML = `
  <!-- Fließtext Fazit -->
  <div style="background:#1a2332;border:1px solid #30363d;border-left:4px solid ${F.strat_color};border-radius:10px;padding:20px 24px;margin-bottom:20px;line-height:1.8">
    <div style="color:#8b949e;font-size:.75em;text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px">🎯 Fazit</div>
    <div style="color:#e6edf3;font-size:.95em">${F.fazit_text||'Keine Analyse verfügbar.'}</div>
  </div>

  <!-- Strategy Header -->
  <div style="background:${F.strat_color}11;border:2px solid ${F.strat_color}44;border-radius:12px;padding:20px 24px;margin-bottom:20px">
    <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
      <div style="font-size:2em;font-weight:800;color:${F.strat_color}">${F.strat_name}</div>
      <div style="background:${F.strat_color}22;color:${F.strat_color};border:1px solid ${F.strat_color}55;padding:3px 10px;border-radius:20px;font-size:.8em;font-weight:600">
        Konfidenz ${F.confidence}%
      </div>
      <div style="background:${F.bias_color}22;color:${F.bias_color};border:1px solid ${F.bias_color}55;padding:3px 10px;border-radius:20px;font-size:.8em;font-weight:600">
        ${F.bias}
      </div>
      <div style="background:#ef535022;color:#ef5350;border:1px solid #ef535044;padding:3px 10px;border-radius:20px;font-size:.8em;font-weight:600" title="Durchschnittliche Magnitude pro Bewegung">
        Volatilität ${F.volatility} (⌀ ${F.avg_mag}%)
      </div>
    </div>
    <div style="color:#aaa;margin-top:10px;font-size:.88em">${F.strat_desc}</div>
    <div style="color:#8b949e;margin-top:4px;font-size:.82em">${F.bias_text}</div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px">
    <!-- Strategy Scores -->
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px">
      <div style="color:#8b949e;font-size:.78em;text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px">Strategie-Scoring</div>
      ${scoreBars}
    </div>
    <!-- Recommendations -->
    <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px">
      <div style="color:#8b949e;font-size:.78em;text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px">💡 Empfehlungen</div>
      <ul style="list-style:none;padding:0">${recItems}</ul>
    </div>
  </div>

  ${warns ? `<div style="margin-bottom:16px"><div style="color:#8b949e;font-size:.78em;text-transform:uppercase;margin-bottom:8px">⚠️ Warnungen</div>${warns}</div>` : ''}

  <!-- Top Signals -->
  <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:20px;overflow:hidden">
    <div style="padding:12px 16px;border-bottom:1px solid #21262d;color:#8b949e;font-size:.78em;text-transform:uppercase;letter-spacing:.05em">
      🏆 Zuverlässigste Entry-Signale (|t| ≥ 5, Hit ≥ 45%)
    </div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Kategorie</th><th>Feature</th><th>Bewegungstyp</th><th>t-Stat</th><th>Hit-Rate</th><th>Wert war</th></tr></thead>
      <tbody>${sigRows || '<tr><td colspan="6" class="empty">Keine starken Signale (|t|≥5, Hit≥45%).</td></tr>'}</tbody>
    </table></div>
  </div>

  <!-- Per-Type Conclusions -->
  <div style="color:#8b949e;font-size:.78em;text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px">📋 Fazit pro Bewegungstyp</div>
  ${perTypeSections || '<div class="empty">Keine ausreichenden Daten.</div>'}
  `;
});

// ── Activate first tab ────────────────────────────────────────────────────────
activateTab('fazit');
</script>
</body>
</html>"""

# Inject feature descriptions as JSON into template
import json as _json
_HTML_TEMPLATE = _HTML_TEMPLATE.replace(
    '__FEAT_DESC_JSON__',
    _json.dumps(_FEATURE_DESC, ensure_ascii=False)
)
