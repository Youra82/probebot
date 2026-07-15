#!/usr/bin/env python3
"""
analysis/regime_analysis.py — Regime Performance Analysis

Win-Rate pro Marktregime (TREND / RANGE / CHAOS, aus probebots regime-
Feature) zum Entry-Zeitpunkt jedes OOS-Trades. Zeigt ob bestimmte Regime
ausgeschlossen werden sollten.
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, load_oos_data, run_oos_backtest,
    style_axes, save_send,
)

REGIME_LABELS = {1: 'TREND', 0: 'RANGE', -1: 'CHAOS'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-samples', type=int, default=10)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Regime Performance Analysis\n{'='*60}\n")

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
    caption_lines = ["probebot Regime Performance\n"]

    for i, cfg in enumerate(configs):
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or not res.get('trades'):
            print(f"  {Y}{name}: keine Trades — uebersprungen.{NC}")
            continue
        df_oos, _, _ = load_oos_data(cfg)
        if df_oos is None or 'regime' not in df_oos.columns:
            print(f"  {Y}{name}: kein regime-Feature — uebersprungen.{NC}")
            continue

        ts_to_regime = dict(zip(df_oos['timestamp'].astype(str), df_oos['regime']))

        by_regime = {}
        for t in res['trades']:
            regime = ts_to_regime.get(t['entry_ts'])
            if regime is None:
                continue
            by_regime.setdefault(regime, []).append(t)

        print(f"  {name}:")
        labels, wrs, ns = [], [], []
        for regime, trades in sorted(by_regime.items()):
            n = len(trades)
            if n < args.min_samples:
                continue
            wr = sum(1 for t in trades if t['pnl'] > 0) / n * 100
            pnl = sum(t['pnl'] for t in trades)
            label = REGIME_LABELS.get(regime, str(regime))
            col = G if wr >= 55 else Y if wr >= 45 else R
            print(f"    {label:<8} n={n:4d}  WR={col}{wr:5.1f}%{NC}  PnL={pnl:+.1f}")
            labels.append(label)
            wrs.append(wr)
            ns.append(n)

        if labels:
            caption_lines.append(f"{name}: " + ", ".join(f"{l}={w:.0f}%" for l, w in zip(labels, wrs)))
            ax = axes[0][i]
            style_axes(ax)
            hex_colors = ['#16a34a' if w >= 55 else '#f59e0b' if w >= 45 else '#ef4444' for w in wrs]
            ax.bar(labels, wrs, color=hex_colors, alpha=0.85)
            ax.axhline(50, color='white', linewidth=1, alpha=0.5, linestyle='--')
            for j, n in enumerate(ns):
                ax.text(j, wrs[j] + 1, f'n={n}', ha='center', fontsize=8, color='white')
            ax.set_title(name, fontsize=10)
            ax.set_ylabel('Win-Rate %')

    fig.suptitle('probebot Regime Performance | TREND/RANGE/CHAOS', color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'regime_analysis', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
