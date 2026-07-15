#!/usr/bin/env python3
"""
analysis/split_sensitivity.py — Split-Punkt-Robustheit

dnabot-Aequivalent (strategy_comparison.py) vergleicht woechentliche
Re-Optimierung gegen fixe Configs ueber lange Zeitraeume — das setzt eine
Historie mehrerer Config-Versionen voraus, die probebot nicht fuehrt (Configs
werden einmalig optimiert, nicht laufend neu optimiert).

Stattdessen wird hier geprueft wie empfindlich das OOS-Ergebnis vom exakten
70/30-Split-Punkt abhaengt: wenn der Split leicht verschoben wird (65/35,
75/25, 80/20 statt 70/30), bleibt das Ergebnis dann stabil? Starke
Schwankungen deuten auf Overfitting am genauen Split-Zeitpunkt hin.
"""
import argparse
import sys

import pandas as pd

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_backtest_custom,
    load_bot_spec, ROOT, style_axes, save_send,
)

SPLIT_FRACTIONS = [0.60, 0.65, 0.70, 0.75, 0.80]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Split-Punkt-Robustheit\n{'='*60}")
    print(f"  Testet 70/30 gegen alternative Split-Punkte (60/40 .. 80/20)\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    n_cfg = len(configs)
    fig, axes = plt.subplots(1, n_cfg, figsize=(4.5 * n_cfg, 5), squeeze=False)
    fig.patch.set_facecolor('#0f172a')
    caption_lines = ["probebot Split-Punkt-Robustheit\n"]

    for i, cfg in enumerate(configs):
        name = config_name(cfg)
        sym = cfg['market']['symbol'].replace('/', '_').replace(':', '_')
        tf = cfg['market']['timeframe']
        data_path = ROOT / 'artifacts' / 'data' / f'data_{sym}_{tf}.parquet'
        if not data_path.exists():
            print(f"  {Y}{name}: Daten nicht gefunden — uebersprungen.{NC}")
            continue
        df = pd.read_parquet(data_path)

        print(f"  {name}:")
        print(f"  {'Split':>8}  {'PnL%':>10}  {'MaxDD%':>8}  {'Trades':>7}")
        print(f"  {'-'*38}")

        labels, pnls = [], []
        for frac in SPLIT_FRACTIONS:
            split_idx = int(len(df) * frac)
            split_date = str(df['timestamp'].iloc[split_idx])[:10]
            res = run_backtest_custom(cfg, start_date=split_date)
            if res is None:
                continue
            label = f"{round(frac*100)}/{round((1-frac)*100)}"
            col = G if res['pnl_pct'] > 0 else R
            marker = ' <- Standard' if abs(frac - 0.70) < 1e-9 else ''
            print(f"  {label:>8}  {col}{res['pnl_pct']:>+9.1f}%{NC}  {res['max_drawdown']:>7.1f}%  "
                  f"{res['n_trades']:>7d}{marker}")
            labels.append(label)
            pnls.append(res['pnl_pct'])

        if pnls:
            spread = max(pnls) - min(pnls)
            if spread > abs(pnls[len(pnls)//2]) * 0.5:
                print(f"    {Y}Hohe Streuung ueber Split-Punkte ({spread:.0f} Punkte) — "
                      f"Ergebnis haengt stark am exakten Split ab.{NC}")
            else:
                print(f"    {G}Stabil ueber verschiedene Split-Punkte (Streuung: {spread:.0f} Punkte).{NC}")
            caption_lines.append(f"{name}: Streuung {spread:.0f}pp ueber {labels[0]}..{labels[-1]}")

            ax = axes[0][i]
            style_axes(ax)
            colors = ['#16a34a' if p > 0 else '#ef4444' for p in pnls]
            ax.bar(labels, pnls, color=colors, alpha=0.85)
            ax.axhline(0, color='white', linewidth=0.8, alpha=0.4)
            ax.set_title(name, fontsize=10)
            ax.set_xlabel('Train/OOS Split')
            ax.set_ylabel('PnL%')

    fig.suptitle('probebot Split-Punkt-Robustheit | Standard: 70/30', color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'split_sensitivity', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
