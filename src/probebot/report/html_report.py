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
    for mtype, ranked in correlations.items():
        corr_data[mtype] = [
            {
                'feature':     r['feature'],
                't':           round(r['t_statistic'], 3),
                'before':      round(float(r.get('mean_before', 0)), 5),
                'baseline':    round(float(r.get('mean_all', 0)), 5),
                'hit':         round(r.get('predictive_pct', 0), 1),
                'n':           r.get('n_events', 0),
            }
            for r in ranked if abs(r.get('t_statistic', 0)) >= 2.0
        ]

    cluster_data = {}
    for cid, cdata in (clusters or {}).items():
        cluster_data[str(cid)] = {
            'n':     cdata.get('n', 0),
            'types': cdata.get('move_types', {}),
            'top':   cdata.get('top_features', [])[:10],
        }

    payload = {
        'symbol':    symbol,
        'timeframe': timeframe,
        'period':    f'{start_date} → {end_date}',
        'generated': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        'n_total':   len(movements),
        'up_count':  up_count,
        'dn_count':  dn_count,
        'move_stats': move_stats,
        'correlations': corr_data,
        'clusters':  cluster_data,
    }

    html = _HTML_TEMPLATE.replace('__DATA_JSON__', json.dumps(payload, ensure_ascii=False))
    path.write_text(html, encoding='utf-8')
    return str(path)


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
  Object.entries(D.correlations).forEach(([mt, rows]) => {
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
  const rows = D.correlations[mtype] || [];
  const stats = D.move_stats[mtype] || {};
  let sortCol = 't', sortDir = -1;
  let filterText = '', filterT = 2.0;

  // ── Stats ──
  const statsHtml = `<div class="stats-row">
    <div class="stat-box"><div class="stat-val" style="color:${color}">${stats.n||0}</div><div class="stat-lbl">Events</div></div>
    <div class="stat-box"><div class="stat-val">${stats.avg_mag||0}%</div><div class="stat-lbl">⌀ Magnitude</div></div>
    <div class="stat-box"><div class="stat-val">${stats.max_mag||0}%</div><div class="stat-lbl">Max Move</div></div>
    <div class="stat-box"><div class="stat-val">${rows.length}</div><div class="stat-lbl">Prädiktoren</div></div>
  </div>`;

  // ── Controls ──
  const ctrlId = 'ctrl-'+mtype;
  const tblId  = 'tbl-'+mtype;
  const slId   = 'sl-'+mtype;
  const slLblId= 'slLbl-'+mtype;

  p.innerHTML = statsHtml + `
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
    </tr>`;
  }).join('');
}

window.filterTable = function(mtype) {
  const text = document.getElementById('ctrl-'+mtype)?.value || '';
  const minT = parseFloat(document.getElementById('sl-'+mtype)?.value || 2);
  const s = getState(mtype);
  s.text = text; s.minT = minT;
  renderRows(mtype, D.correlations[mtype]||[], typeColor(mtype), s.sortCol, s.sortDir, text, minT);
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
  renderRows(mtype, D.correlations[mtype]||[], typeColor(mtype),
    s.sortCol, s.sortDir, s.text||'', s.minT||2);
};

window.exportCSV = function(mtype) {
  const rows = D.correlations[mtype] || [];
  const header = 'feature,t_statistic,vor_move,gesamt_avg,hit_rate_pct\n';
  const body = rows.map(r=>`${r.feature},${r.t},${r.before},${r.baseline},${r.hit}`).join('\n');
  const blob = new Blob([header+body], {type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `probebot_${mtype}.csv`;
  a.click();
};

// ── Activate first tab ────────────────────────────────────────────────────────
activateTab('overview');
</script>
</body>
</html>"""

# Inject feature descriptions as JSON into template
import json as _json
_HTML_TEMPLATE = _HTML_TEMPLATE.replace(
    '__FEAT_DESC_JSON__',
    _json.dumps(_FEATURE_DESC, ensure_ascii=False)
)
