#!/usr/bin/env python3
"""
analysis/monte_carlo.py — Monte Carlo Simulation (Bootstrap)

probebots Backtester setzt eine FESTE Positionsgroesse pro Trade (% vom
Startkapital, nicht vom laufenden Kapital) — die Reihenfolge der Trades
aendert daher die Endsumme nicht, nur den Verlauf (Drawdown-Pfad). Deshalb
wird hier mit ECHTEM Bootstrap resampled (mit Zuruecklegen): jede Simulation
zieht n Trades zufaellig (auch mehrfach) aus den echten OOS-Trades — das
bildet ab wie stark das Ergebnis vom konkreten Trade-Sample abhaengt.

Beantwortet:
  - Wie breit streut das Ergebnis, wenn man "eine andere Trade-Stichprobe"
    aus derselben zugrunde liegenden Verteilung gezogen haette?
  - Ruin-Wahrscheinlichkeit (Equity < 50% Start)?
"""
import argparse
import random
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest,
    style_axes, save_send, max_drawdown_from_equity,
)


def simulate_path(trades, start_capital):
    n = len(trades)
    sample = random.choices(trades, k=n)
    equity = [start_capital]
    cap = start_capital
    for t in sample:
        cap += t['pnl']
        equity.append(cap)
    return cap, max_drawdown_from_equity(equity)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--simulations', type=int, default=10000)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Monte Carlo Simulation (Bootstrap)\n{'='*60}")
    print(f"  Simulationen: {args.simulations:,}\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    random.seed(42)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    n_cfg = len(configs)
    fig, axes = plt.subplots(n_cfg, 2, figsize=(14, 5 * n_cfg), squeeze=False)
    fig.patch.set_facecolor('#0f172a')

    caption_lines = [f"probebot Monte Carlo (Bootstrap) | {args.simulations:,} Sims\n"]

    for row, cfg in enumerate(configs):
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or not res.get('trades'):
            print(f"  {Y}{name}: keine Trades — uebersprungen.{NC}")
            continue

        trades = res['trades']
        start_capital = cfg.get('risk', {}).get('start_capital', 100.0)
        n_trades = len(trades)

        final_equities, max_dds = [], []
        for _ in range(args.simulations):
            eq, dd = simulate_path(trades, start_capital)
            final_equities.append(eq)
            max_dds.append(dd)

        pnl_pcts = sorted((e - start_capital) / start_capital * 100 for e in final_equities)
        max_dds_sorted = sorted(max_dds)
        p5  = pnl_pcts[int(0.05 * len(pnl_pcts))]
        p50 = pnl_pcts[int(0.50 * len(pnl_pcts))]
        p95 = pnl_pcts[int(0.95 * len(pnl_pcts))]
        dd95 = max_dds_sorted[int(0.95 * len(max_dds_sorted))]
        ruin = sum(1 for e in final_equities if e < start_capital * 0.5) / args.simulations * 100
        profitable = sum(1 for p in pnl_pcts if p > 0) / args.simulations * 100

        print(f"  {name} ({n_trades} Trades):")
        print(f"    5. Perzentil: {p5:+.1f}%   Median: {p50:+.1f}%   95. Perzentil: {p95:+.1f}%")
        print(f"    MaxDD 95. Perzentil: {dd95:.1f}%")
        col_ruin = R if ruin > 10 else Y if ruin > 2 else G
        print(f"    Ruin-Wahrscheinlichkeit (<50%): {col_ruin}{ruin:.1f}%{NC}   "
              f"Profitabel-Wahrscheinlichkeit: {profitable:.1f}%\n")
        caption_lines.append(f"{name}: Median {p50:+.0f}% | Ruin {ruin:.1f}%")

        ax1, ax2 = axes[row][0], axes[row][1]
        style_axes(ax1, ax2)
        ax1.hist(pnl_pcts, bins=60, color='#2563eb', alpha=0.7, edgecolor='none')
        ax1.axvline(p5, color='#ef4444', linestyle='--', linewidth=2, label=f'P5: {p5:+.0f}%')
        ax1.axvline(p50, color='#fbbf24', linewidth=2, label=f'Median: {p50:+.0f}%')
        ax1.axvline(p95, color='#16a34a', linestyle='--', linewidth=2, label=f'P95: {p95:+.0f}%')
        ax1.set_title(f'{name} — Endkapital-Verteilung', fontsize=10)
        ax1.legend(fontsize=8, facecolor='#1e293b', labelcolor='white')

        ax2.hist(max_dds, bins=50, color='#dc2626', alpha=0.7, edgecolor='none')
        ax2.axvline(dd95, color='#ef4444', linestyle='--', linewidth=2, label=f'P95: {dd95:.1f}%')
        ax2.set_title(f'{name} — Max-Drawdown-Verteilung (Ruin: {ruin:.1f}%)', fontsize=10)
        ax2.legend(fontsize=8, facecolor='#1e293b', labelcolor='white')

    fig.suptitle(f'probebot Monte Carlo | {args.simulations:,} Bootstrap-Simulationen je Config',
                 color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'monte_carlo', "\n".join(caption_lines), args.no_telegram)
    print(f"  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
