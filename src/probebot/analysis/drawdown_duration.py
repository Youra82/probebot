#!/usr/bin/env python3
"""
analysis/drawdown_duration.py — Drawdown Duration Analysis

Wie lange dauern Drawdown-Phasen (Peak -> neuer Peak) auf der echten OOS-
Equity-Kurve? Wichtiger als die reine Drawdown-Tiefe: wie lange muss man
psychologisch "aussitzen"?
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest, style_axes, save_send,
)


def drawdown_periods(trades, start_capital):
    """Liste von (start_idx, end_idx, depth_pct, n_trades_in_dd)."""
    equity = [start_capital]
    cap = start_capital
    for t in trades:
        cap += t['pnl']
        equity.append(cap)

    periods = []
    peak = equity[0]
    peak_idx = 0
    in_dd = False
    dd_start = 0
    max_depth = 0.0

    for idx, e in enumerate(equity):
        if e >= peak:
            if in_dd:
                periods.append((dd_start, idx, max_depth, idx - dd_start))
                in_dd = False
            peak = e
            peak_idx = idx
        else:
            if not in_dd:
                in_dd = True
                dd_start = peak_idx
                max_depth = 0.0
            depth = (peak - e) / peak * 100
            max_depth = max(max_depth, depth)
    if in_dd:
        periods.append((dd_start, len(equity) - 1, max_depth, len(equity) - 1 - dd_start))
    return periods


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Drawdown Duration Analysis\n{'='*60}\n")

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
    caption_lines = ["probebot Drawdown Duration\n"]

    for i, cfg in enumerate(configs):
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or len(res.get('trades', [])) < 5:
            print(f"  {Y}{name}: zu wenig Trades — uebersprungen.{NC}")
            continue

        start_capital = cfg.get('risk', {}).get('start_capital', 100.0)
        periods = drawdown_periods(res['trades'], start_capital)
        if not periods:
            print(f"  {G}{name}: keine Drawdown-Phasen (nur neue Hochs).{NC}")
            continue

        durations = [p[3] for p in periods]
        depths = [p[2] for p in periods]
        worst = max(periods, key=lambda p: p[2])

        print(f"  {name}:  {len(periods)} Drawdown-Phasen")
        print(f"    Laengste Phase: {max(durations)} Trades  |  Median: {sorted(durations)[len(durations)//2]} Trades")
        print(f"    Tiefste Phase:  {worst[2]:.1f}%  ueber {worst[3]} Trades")
        caption_lines.append(f"{name}: {len(periods)} DD-Phasen, laengste {max(durations)} Trades, "
                              f"tiefste {worst[2]:.1f}%")

        ax = axes[0][i]
        style_axes(ax)
        ax.scatter(durations, depths, s=40, alpha=0.7, color='#dc2626')
        ax.set_xlabel('Dauer (Anzahl Trades bis Recovery)')
        ax.set_ylabel('Tiefe (%)')
        ax.set_title(f'{name} — {len(periods)} DD-Phasen', fontsize=10)

    fig.suptitle('probebot Drawdown Duration Analysis', color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'drawdown_duration', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
