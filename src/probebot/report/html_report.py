"""
HTML-Report Generator — Dark-Theme Dashboard mit allen Prädiktoren.
Wird als Dokument per Telegram verschickt (downloadbar).
"""
from pathlib import Path
from datetime import datetime
from typing import Optional
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
    """Erzeugt HTML-Datei und gibt den Pfad zurück."""

    from collections import Counter
    type_counts = Counter(m.move_type for m in movements)
    up_count = sum(1 for m in movements if m.direction == 'UP')
    dn_count = len(movements) - up_count

    html = _build_html(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        n_total=len(movements),
        up_count=up_count,
        dn_count=dn_count,
        type_counts=type_counts,
        correlations=correlations,
        clusters=clusters,
        generated_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
    )

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding='utf-8')
    return str(path)


def _build_html(
    symbol, timeframe, start_date, end_date,
    n_total, up_count, dn_count, type_counts,
    correlations, clusters, generated_at,
) -> str:

    # ── Summary Cards ────────────────────────────────────────────────────────
    cards_html = ''
    for mtype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        color = '#26a69a' if 'UP' in mtype or 'BREAKOUT' in mtype else '#ef5350'
        cards_html += f'''
        <div class="card">
            <div class="card-count" style="color:{color}">{cnt}</div>
            <div class="card-label">{mtype.replace("_"," ")}</div>
        </div>'''

    # ── Predictor Tables ─────────────────────────────────────────────────────
    tables_html = ''
    for mtype, ranked in sorted(correlations.items()):
        top = [r for r in ranked if abs(r.get('t_statistic', 0)) >= 2.0]
        if not top:
            continue

        n_events = top[0].get('n_events', '?') if top else '?'
        max_t = max(abs(r['t_statistic']) for r in top) if top else 1

        rows = ''
        for r in top[:40]:
            t = r['t_statistic']
            feat = r['feature']
            mean_before = r.get('mean_before', 0)
            mean_all = r.get('mean_all', 0)
            hit_pct = r.get('predictive_pct', 0)
            bar_w = min(100, int(abs(t) / max_t * 100))
            if t > 0:
                color = '#26a69a'
                arrow = '↑'
                bar_color = '#26a69a'
            else:
                color = '#ef5350'
                arrow = '↓'
                bar_color = '#ef5350'

            hit_color = '#26a69a' if hit_pct >= 60 else '#f5a623' if hit_pct >= 40 else '#888'

            rows += f'''
            <tr>
                <td style="color:{color};font-weight:600">{arrow} {feat}</td>
                <td>
                    <div style="display:flex;align-items:center;gap:8px">
                        <div style="background:{bar_color};width:{bar_w}%;height:8px;border-radius:4px;min-width:4px"></div>
                        <span style="color:{color};font-weight:700">{t:+.2f}</span>
                    </div>
                </td>
                <td style="color:#e0e0e0">{_fmt_num(mean_before)}</td>
                <td style="color:#888">{_fmt_num(mean_all)}</td>
                <td style="color:{hit_color};font-weight:600">{hit_pct:.0f}%</td>
            </tr>'''

        type_color = '#26a69a' if ('UP' in mtype or 'BREAKOUT' in mtype) else '#ef5350'
        tables_html += f'''
        <div class="section">
            <div class="section-header">
                <span class="type-badge" style="background:{type_color}22;color:{type_color};border:1px solid {type_color}44">
                    {mtype.replace("_"," ")}
                </span>
                <span class="section-meta">{n_events} Events &nbsp;|&nbsp; {symbol} &nbsp;|&nbsp; {timeframe}</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Feature</th>
                        <th>t-Statistik</th>
                        <th>Vor Move</th>
                        <th>Gesamt ⌀</th>
                        <th>Hit-Rate</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>'''

    # ── Cluster Section ───────────────────────────────────────────────────────
    clusters_html = ''
    if clusters:
        clusters_html = '<div class="section"><div class="section-header"><span class="type-badge" style="background:#7b1fa222;color:#ce93d8;border:1px solid #7b1fa244">PATTERN CLUSTER</span></div>'
        for cid, cdata in clusters.items():
            n = cdata.get('n', '?')
            types = cdata.get('move_types', {})
            top_features = cdata.get('top_features', [])
            type_str = ' | '.join(f"{k}:{v}" for k, v in list(types.items())[:4])
            feat_rows = ''
            for f in top_features[:8]:
                diff = f.get('diff', 0)
                fname = f.get('feature', '?')
                c = '#26a69a' if diff > 0 else '#ef5350'
                feat_rows += f'<li style="color:{c}">{"↑" if diff>0 else "↓"} {fname} ({diff:+.3f})</li>'
            clusters_html += f'''
            <div style="margin:16px 0;padding:16px;background:#1a1f2e;border-radius:8px;border-left:3px solid #ce93d8">
                <div style="color:#ce93d8;font-weight:700;margin-bottom:8px">Cluster {cid} &nbsp; <span style="color:#888;font-weight:400;font-size:0.85em">{n} Events</span></div>
                <div style="color:#aaa;font-size:0.85em;margin-bottom:8px">{type_str}</div>
                <ul style="list-style:none;padding:0;margin:0;font-size:0.85em">{feat_rows}</ul>
            </div>'''
        clusters_html += '</div>'

    return f'''<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Probebot — {symbol} {timeframe}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0d1117;
    color: #c9d1d9;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace;
    font-size: 14px;
    line-height: 1.5;
  }}
  .header {{
    background: linear-gradient(135deg, #161b22 0%, #1a2332 100%);
    border-bottom: 1px solid #30363d;
    padding: 24px 32px;
  }}
  .header h1 {{ font-size: 1.6em; color: #e6edf3; font-weight: 700; }}
  .header .meta {{ color: #8b949e; font-size: 0.9em; margin-top: 6px; }}
  .header .meta span {{ margin-right: 20px; }}
  .summary-bar {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    padding: 20px 32px;
    background: #161b22;
    border-bottom: 1px solid #30363d;
  }}
  .stat-pill {{
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 0.85em;
    font-weight: 600;
  }}
  .cards {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    padding: 20px 32px;
    background: #0d1117;
    border-bottom: 1px solid #21262d;
  }}
  .card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 14px 18px;
    min-width: 140px;
    text-align: center;
  }}
  .card-count {{ font-size: 2em; font-weight: 700; }}
  .card-label {{ font-size: 0.78em; color: #8b949e; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.05em; }}
  .content {{ padding: 24px 32px; max-width: 1400px; }}
  .section {{
    margin-bottom: 32px;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    overflow: hidden;
  }}
  .section-header {{
    padding: 14px 20px;
    background: #1c2128;
    border-bottom: 1px solid #30363d;
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .type-badge {{
    padding: 4px 12px;
    border-radius: 6px;
    font-weight: 700;
    font-size: 0.9em;
    letter-spacing: 0.03em;
  }}
  .section-meta {{ color: #8b949e; font-size: 0.85em; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88em;
  }}
  th {{
    padding: 10px 16px;
    text-align: left;
    color: #8b949e;
    font-weight: 600;
    font-size: 0.8em;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 1px solid #21262d;
    background: #161b22;
  }}
  td {{
    padding: 8px 16px;
    border-bottom: 1px solid #1c2128;
    font-family: 'SFMono-Regular', Consolas, monospace;
    font-size: 0.85em;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #1c2128; }}
  .footer {{
    padding: 20px 32px;
    border-top: 1px solid #21262d;
    color: #8b949e;
    font-size: 0.8em;
    text-align: center;
  }}
  .legend {{
    display: flex;
    gap: 20px;
    padding: 12px 32px;
    background: #161b22;
    border-bottom: 1px solid #21262d;
    font-size: 0.82em;
    color: #8b949e;
  }}
  .legend span {{ display: flex; align-items: center; gap: 6px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
</style>
</head>
<body>

<div class="header">
  <h1>🔬 Probebot — Market Forensics Report</h1>
  <div class="meta">
    <span>📊 {symbol}</span>
    <span>⏱ {timeframe}</span>
    <span>📅 {start_date} → {end_date}</span>
    <span>🕒 {generated_at}</span>
  </div>
</div>

<div class="summary-bar">
  <span class="stat-pill" style="background:#26a69a22;color:#26a69a;border:1px solid #26a69a44">
    ▲ {up_count} Aufwärts
  </span>
  <span class="stat-pill" style="background:#ef535022;color:#ef5350;border:1px solid #ef535044">
    ▼ {dn_count} Abwärts
  </span>
  <span class="stat-pill" style="background:#1c2128;color:#8b949e;border:1px solid #30363d">
    Σ {n_total} Bewegungen
  </span>
</div>

<div class="cards">
  {cards_html}
</div>

<div class="legend">
  <span><div class="dot" style="background:#26a69a"></div> ↑ Indikator vor Move erhöht (Long-Signal)</span>
  <span><div class="dot" style="background:#ef5350"></div> ↓ Indikator vor Move erniedrigt (Short-Signal)</span>
  <span>t-Statistik: ±2 = signifikant | ±5 = stark | ±10 = sehr stark</span>
  <span>Hit-Rate: wie oft war die Bedingung erfüllt</span>
</div>

<div class="content">
  {tables_html}
  {clusters_html}
</div>

<div class="footer">
  Probebot — Market Forensics Engine &nbsp;|&nbsp; Statistik: Welch t-Test, Cohen d, Cosine Similarity, K-Means &nbsp;|&nbsp; {generated_at}
</div>

</body>
</html>'''


def _fmt_num(val) -> str:
    try:
        v = float(val)
        if abs(v) >= 10000:
            return f'{v:,.0f}'
        elif abs(v) >= 100:
            return f'{v:.1f}'
        else:
            return f'{v:.4f}'
    except (TypeError, ValueError):
        return str(val)
