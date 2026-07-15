#!/usr/bin/env python3
"""
analysis/walk_forward.py — Walk-Forward Stabilitaets-Test

dnabot-Aequivalent: dort wird der optimale Lookback fuer die woechentliche
Pair-Neuauswahl gesucht. probebot hat keine woechentliche Neu-Optimierung
(Configs bleiben nach dem Optuna-Lauf fix) — die relevante Frage hier ist
stattdessen: haelt die Performance ueber die gesamte OOS-Periode stabil,
oder kommt der Grossteil des PnL aus wenigen Wochen (Overfitting-Signal)?

Methode: OOS-Zeitraum in rollierende Fenster (Standard 4 Wochen) teilen,
pro Fenster den Backtest neu laufen lassen, PnL/WinRate je Fenster zeigen.
"""
import argparse
import sys
from datetime import timedelta

from probebot.analysis._common import (
    G, Y, R, C, NC, load_configs, config_name, load_oos_data,
    run_backtest_custom, style_axes, save_send,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--window-weeks', type=int, default=4)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Walk-Forward Stabilitaets-Test\n{'='*60}")
    print(f"  Fenstergroesse: {args.window_weeks} Wochen\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    import pandas as pd
    all_results = {}

    for cfg in configs:
        name = config_name(cfg)
        df_oos, split_idx, _ = load_oos_data(cfg)
        if df_oos is None or len(df_oos) == 0:
            continue
        start = df_oos['timestamp'].iloc[0]
        end = df_oos['timestamp'].iloc[-1]
        window = timedelta(weeks=args.window_weeks)

        windows = []
        cur = start
        while cur < end:
            w_end = min(cur + window, end)
            windows.append((cur, w_end))
            cur = w_end

        pnls = []
        for w_start, w_end in windows:
            res = run_backtest_custom(cfg, start_date=str(w_start.date()), end_date=str(w_end.date()))
            pnls.append(res['pnl_pct'] if res else 0.0)

        if any(p != 0 for p in pnls):
            all_results[name] = {'windows': windows, 'pnls': pnls}
            pos = sum(1 for p in pnls if p > 0)
            print(f"  {name:<20} {len(windows)} Fenster, {pos}/{len(windows)} positiv, "
                  f"PnL-Range: {min(pnls):+.1f}% .. {max(pnls):+.1f}%")

    if not all_results:
        print(f"\n  {R}Keine auswertbaren Ergebnisse.{NC}")
        sys.exit(1)

    # Konsistenz-Score: Anteil positiver Fenster
    print(f"\n{Y}{'─'*60}{NC}")
    print(f"  Konsistenz (Anteil positiver {args.window_weeks}-Wochen-Fenster):")
    for name, r in sorted(all_results.items(), key=lambda x: -sum(1 for p in x[1]['pnls'] if p > 0) / len(x[1]['pnls'])):
        pos_frac = sum(1 for p in r['pnls'] if p > 0) / len(r['pnls']) * 100
        col = G if pos_frac >= 60 else Y if pos_frac >= 40 else R
        print(f"  {name:<20} {col}{pos_frac:>5.1f}%{NC} positiv  ({len(r['pnls'])} Fenster)")

    # Chart
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    n = len(all_results)
    fig, axes = plt.subplots(n, 1, figsize=(12, max(3, 2.2 * n)), squeeze=False)
    fig.patch.set_facecolor('#0f172a')
    from probebot.analysis._common import COLORS

    for i, (name, r) in enumerate(all_results.items()):
        ax = axes[i][0]
        style_axes(ax)
        colors = ['#16a34a' if p > 0 else '#ef4444' for p in r['pnls']]
        ax.bar(range(len(r['pnls'])), r['pnls'], color=colors, alpha=0.85)
        ax.axhline(0, color='white', linewidth=0.8, alpha=0.4)
        ax.set_title(f'{name} — PnL% je {args.window_weeks}-Wochen-Fenster', fontsize=10)
        ax.set_ylabel('PnL%')

    fig.suptitle(f'probebot Walk-Forward Stabilitaet | {n} Configs | Fenster: {args.window_weeks}W',
                 color='white', fontsize=12)
    plt.tight_layout()

    caption = f"probebot Walk-Forward Stabilitaet\n{n} Configs, {args.window_weeks}-Wochen-Fenster\n\n" + \
        "\n".join(f"{name}: {sum(1 for p in r['pnls'] if p>0)}/{len(r['pnls'])} Fenster positiv"
                   for name, r in all_results.items())
    save_send(fig, 'walk_forward', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
