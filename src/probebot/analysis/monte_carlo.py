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

    caption_lines = [f"probebot Monte Carlo (Bootstrap) | {args.simulations:,} Sims\n"]

    # Pro Config nur die fertigen PnL-/DD-Verteilungen sammeln — die Grafik
    # zeigt sie am Ende als kompakten Boxplot-Vergleich (ein Balken pro
    # Config statt einem eigenen Histogramm-Panel). Ein Histogramm-Panel pro
    # Config skaliert die Bildhoehe linear mit der Anzahl Configs und wird
    # ab ~10 Configs auf einem Handy-Bildschirm unlesbar (bei 13 Configs
    # bereits 2085x9684px) — Boxplots brauchen dagegen nur eine schmale Zeile
    # pro Config und bleiben bei jeder Config-Anzahl auf einen Blick lesbar.
    rows = []
    for cfg in configs:
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

        rows.append({
            'name': name, 'pnl_pcts': pnl_pcts, 'max_dds': max_dds,
            'p50': p50, 'dd95': dd95, 'ruin': ruin,
        })

    if not rows:
        print(f"  {R}Keine verwertbaren Ergebnisse.{NC}")
        return

    rows.sort(key=lambda r: r['p50'], reverse=True)
    n = len(rows)
    height = max(4.5, 0.45 * n)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, height))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax1, ax2)

    names = [r['name'] for r in rows]
    box_colors = ['#16a34a' if r['p50'] >= 0 else '#dc2626' for r in rows]

    # set_yticklabels() statt boxplot(labels=...)/(tick_labels=...): der
    # Parametername wurde zwischen Matplotlib-Versionen umbenannt (3.9: neu
    # "tick_labels", alt "labels" deprecated; manche Installationen — z.B.
    # VPS — haben "labels" bereits vollstaendig entfernt) — set_yticklabels
    # funktioniert versionsunabhaengig in jeder Matplotlib-Version.
    ytick_pos = list(range(1, n + 1))

    bp1 = ax1.boxplot([r['pnl_pcts'] for r in rows], vert=False,
                       patch_artist=True, showfliers=False, widths=0.6)
    ax1.set_yticks(ytick_pos)
    ax1.set_yticklabels(names)
    for patch, color in zip(bp1['boxes'], box_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
    ax1.axvline(0, color='white', linewidth=0.8, alpha=0.4)
    ax1.set_title(f'Endkapital-Verteilung (PnL%)\n5./50./95. Perzentil je Config', fontsize=10)
    ax1.tick_params(axis='y', labelsize=8)

    bp2 = ax2.boxplot([r['max_dds'] for r in rows], vert=False,
                       patch_artist=True, showfliers=False, widths=0.6)
    ax2.set_yticks(ytick_pos)
    ax2.set_yticklabels(names)
    for patch, r in zip(bp2['boxes'], rows):
        patch.set_facecolor('#ef4444' if r['ruin'] > 10 else '#f59e0b' if r['ruin'] > 2 else '#3b82f6')
        patch.set_alpha(0.75)
    ax2.set_title('Max-Drawdown-Verteilung\n(Farbe = Ruin-Wahrscheinlichkeit)', fontsize=10)
    ax2.tick_params(axis='y', labelsize=8)
    for i, r in enumerate(rows):
        ax2.annotate(f"Ruin {r['ruin']:.0f}%", xy=(1.0, i + 1), xycoords=('axes fraction', 'data'),
                     xytext=(4, 0), textcoords='offset points', fontsize=7,
                     color='white', va='center')

    fig.suptitle(f'probebot Monte Carlo | {args.simulations:,} Bootstrap-Simulationen je Config '
                 f'(sortiert nach Median-PnL)', color='white', fontsize=11)
    plt.tight_layout()
    save_send(fig, 'monte_carlo', "\n".join(caption_lines), args.no_telegram)
    print(f"  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
