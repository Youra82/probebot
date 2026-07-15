#!/usr/bin/env python3
"""
analysis/time_analysis.py — Tageszeit-Analyse

Performen Trades zu bestimmten Uhrzeiten (Asien/Europa/US-Session) besser?
"""
import argparse
import sys
from datetime import datetime

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest, style_axes, save_send,
)

SESSIONS = [
    ('Asien',  0, 8),
    ('Europa', 8, 16),
    ('US',     16, 24),
]


def _hour(ts_str):
    try:
        return datetime.fromisoformat(str(ts_str).replace('Z', '+00:00')).hour
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Tageszeit-Analyse\n{'='*60}\n")

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
    caption_lines = ["probebot Tageszeit-Analyse (UTC)\n"]

    for i, cfg in enumerate(configs):
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or not res.get('trades'):
            print(f"  {Y}{name}: keine Trades — uebersprungen.{NC}")
            continue

        print(f"  {name}:")
        labels, wrs = [], []
        for label, lo, hi in SESSIONS:
            group = [t for t in res['trades'] if (h := _hour(t['entry_ts'])) is not None and lo <= h < hi]
            if len(group) < 5:
                continue
            wr = sum(1 for t in group if t['pnl'] > 0) / len(group) * 100
            col = G if wr >= 55 else Y if wr >= 45 else R
            print(f"    {label:<8} ({lo:02d}-{hi:02d} UTC)  n={len(group):4d}  WR={col}{wr:5.1f}%{NC}")
            labels.append(label)
            wrs.append(wr)

        if wrs:
            caption_lines.append(f"{name}: " + ", ".join(f"{l}={w:.0f}%" for l, w in zip(labels, wrs)))
            ax = axes[0][i]
            style_axes(ax)
            colors = ['#16a34a' if w >= 55 else '#f59e0b' if w >= 45 else '#ef4444' for w in wrs]
            ax.bar(labels, wrs, color=colors, alpha=0.85)
            ax.axhline(50, color='white', linewidth=1, alpha=0.5, linestyle='--')
            ax.set_title(name, fontsize=10)
            ax.set_ylabel('Win-Rate %')

    fig.suptitle('probebot Tageszeit-Analyse (Handelssession)', color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'time_analysis', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
