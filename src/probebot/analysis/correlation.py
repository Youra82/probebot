#!/usr/bin/env python3
"""
analysis/correlation.py — Anti-Korrelations-Portfolio

Berechnet die Korrelation zwischen den woechentlichen OOS-Returns aller
Configs. Zeigt welche Kombinationen am wenigsten gleichzeitig verlieren
(gute Portfolio-Diversifikation).
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest, style_axes, save_send,
)


def _parse_dt(ts_str):
    try:
        dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Anti-Korrelations-Portfolio\n{'='*60}\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    all_trades_by_cfg = {}
    all_dts = []
    for cfg in configs:
        res = run_oos_backtest(cfg)
        if res is None or not res.get('trades'):
            continue
        name = config_name(cfg)
        dated = []
        for t in res['trades']:
            dt = _parse_dt(t.get('entry_ts', ''))
            if dt:
                dated.append((dt, t['pnl']))
                all_dts.append(dt)
        if dated:
            all_trades_by_cfg[name] = dated

    if len(all_trades_by_cfg) < 2:
        print(f"  {R}Mindestens 2 Configs mit Trades noetig.{NC}")
        sys.exit(1)

    min_dt, max_dt = min(all_dts), max(all_dts)
    weeks = []
    w = min_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    while w < max_dt:
        weeks.append(w)
        w += timedelta(weeks=1)

    pair_returns = {}
    for name, dated in all_trades_by_cfg.items():
        weekly = []
        for ws in weeks:
            we = ws + timedelta(weeks=1)
            weekly.append(sum(pnl for dt, pnl in dated if ws <= dt < we))
        pair_returns[name] = weekly

    import numpy as np
    labels = list(pair_returns.keys())
    matrix = np.array([pair_returns[l] for l in labels])
    corr = np.corrcoef(matrix)

    pairs_corr = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            pairs_corr.append((corr[i, j], labels[i], labels[j]))
    pairs_corr.sort(key=lambda x: x[0])

    print(f"  {len(labels)} Configs | {len(weeks)} Wochen\n")
    print(f"  Am wenigsten korrelierte Kombinationen (gut fuer Diversifikation):")
    for c, a, b in pairs_corr[:10]:
        col = G if c < 0 else Y if c < 0.3 else R
        print(f"  {col}{c:>+6.2f}{NC}  {a}  <->  {b}")
    if len(pairs_corr) > 3:
        print(f"\n  Am staerksten korrelierte (redundant, gleichzeitiges Risiko):")
        for c, a, b in pairs_corr[-5:][::-1]:
            print(f"  {R}{c:>+6.2f}{NC}  {a}  <->  {b}")

    # Chart: Heatmap
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 1.2), max(5, len(labels))))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax)
    im = ax.imshow(corr, cmap='RdYlGn_r', vmin=-1, vmax=1)
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, f'{corr[i,j]:.2f}', ha='center', va='center',
                     color='black' if abs(corr[i, j]) < 0.5 else 'white', fontsize=7)
    fig.colorbar(im, ax=ax, label='Korrelation')
    ax.set_title('probebot Config-Korrelationsmatrix (woechentliche OOS-Returns)', color='white', fontsize=11)
    plt.tight_layout()

    caption = "probebot Anti-Korrelations-Portfolio\n" + \
        "\n".join(f"{a} <-> {b}: {c:+.2f}" for c, a, b in pairs_corr[:5])
    save_send(fig, 'correlation', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
