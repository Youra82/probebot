#!/usr/bin/env python3
"""
analysis/multi_type_confluence.py — Multi-Type Signal-Konfluenz

dnabot-Aequivalent (confluence.py): nur traden wenn mehrere Genome gleich-
zeitig signalisieren. probebot-Entsprechung: der Backtester zeichnet pro
Trade jetzt n_types_signaling auf — wie viele Bewegungstypen GLEICHZEITIG
die Entry-Bedingungen erfuellt haben (nicht nur der gewaehlte Typ mit dem
hoechsten Score). Testet ob Trades mit mehreren gleichzeitig signalisierenden
Typen eine bessere Win-Rate haben (Konfluenz-Filter sinnvoll?).
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest, style_axes, save_send,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Multi-Type Signal-Konfluenz\n{'='*60}\n")

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
    caption_lines = ["probebot Multi-Type Konfluenz\n"]

    for i, cfg in enumerate(configs):
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or not res.get('trades'):
            print(f"  {Y}{name}: keine Trades — uebersprungen.{NC}")
            continue

        trades = res['trades']
        max_n = max(t.get('n_types_signaling', 1) for t in trades)
        if max_n <= 1:
            print(f"  {Y}{name}: nie mehrere Typen gleichzeitig — kein Konfluenz-Signal moeglich.{NC}")
            continue

        print(f"  {name}:")
        labels, wrs, ns = [], [], []
        for n in range(1, max_n + 1):
            group = [t for t in trades if t.get('n_types_signaling', 1) == n]
            if len(group) < 5:
                continue
            wr = sum(1 for t in group if t['pnl'] > 0) / len(group) * 100
            col = G if wr >= 55 else Y if wr >= 45 else R
            print(f"    {n} Typ(en) gleichzeitig  n={len(group):4d}  WR={col}{wr:5.1f}%{NC}")
            labels.append(str(n))
            wrs.append(wr)
            ns.append(len(group))

        if len(wrs) >= 2:
            trend = wrs[-1] - wrs[0]
            if trend > 5:
                print(f"    {G}Konfluenz hilft: +{trend:.1f} Punkte von 1 auf {max_n} Typen.{NC}")
            caption_lines.append(f"{name}: 1-Typ={wrs[0]:.0f}% vs {max_n}-Typ={wrs[-1]:.0f}%")

            ax = axes[0][i]
            style_axes(ax)
            colors = ['#16a34a' if w >= 55 else '#f59e0b' if w >= 45 else '#ef4444' for w in wrs]
            ax.bar(labels, wrs, color=colors, alpha=0.85)
            for j, n in enumerate(ns):
                ax.text(j, wrs[j] + 1, f'n={n}', ha='center', fontsize=8, color='white')
            ax.axhline(50, color='white', linewidth=1, alpha=0.5, linestyle='--')
            ax.set_title(name, fontsize=10)
            ax.set_xlabel('Gleichzeitig signalisierende Typen')
            ax.set_ylabel('Win-Rate %')

    fig.suptitle('probebot Multi-Type Signal-Konfluenz', color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'multi_type_confluence', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
