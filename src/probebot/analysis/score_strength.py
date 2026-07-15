#!/usr/bin/env python3
"""
analysis/score_strength.py — Signal-Staerke-Analyse

dnabot-Aequivalent (seq_length_analysis.py) prueft ob laengere/spezifischere
Genome-Sequenzen profitabler sind. probebot hat keine Sequenzlaengen, aber
jeder Trade traegt einen Signal-'score' (Summe der |t-Statistik| erfuellter
Bedingungen) — die direkte probebot-Entsprechung von "wie stark/eindeutig
war das Signal". Bucket-Analyse: performen hochwertige Signale besser?
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest, style_axes, save_send,
)

N_BUCKETS = 4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Signal-Staerke-Analyse\n{'='*60}")
    print(f"  Trades nach Signal-Score in {N_BUCKETS} Quartile eingeteilt.\n")

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
    caption_lines = ["probebot Signal-Staerke-Analyse\n"]

    for i, cfg in enumerate(configs):
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or len(res.get('trades', [])) < N_BUCKETS * 5:
            print(f"  {Y}{name}: zu wenig Trades — uebersprungen.{NC}")
            continue

        trades = sorted(res['trades'], key=lambda t: t['score'])
        bsize = len(trades) // N_BUCKETS
        buckets = [trades[j*bsize:(j+1)*bsize] for j in range(N_BUCKETS - 1)]
        buckets.append(trades[(N_BUCKETS-1)*bsize:])

        print(f"  {name}:")
        labels, wrs = [], []
        for j, b in enumerate(buckets):
            if not b:
                continue
            score_range = f"{b[0]['score']:.1f}-{b[-1]['score']:.1f}"
            wr = sum(1 for t in b if t['pnl'] > 0) / len(b) * 100
            col = G if wr >= 55 else Y if wr >= 45 else R
            print(f"    Q{j+1} (score {score_range}, n={len(b)}): WR={col}{wr:5.1f}%{NC}")
            labels.append(f"Q{j+1}")
            wrs.append(wr)

        if wrs:
            trend = wrs[-1] - wrs[0]
            if trend > 5:
                print(f"    {G}Hoehere Scores = bessere WR (+{trend:.1f}pp) — Score-Filter sinnvoll.{NC}")
            elif trend < -5:
                print(f"    {Y}Score korreliert NICHT positiv mit WR ({trend:+.1f}pp).{NC}")
            caption_lines.append(f"{name}: Q1={wrs[0]:.0f}% -> Q{N_BUCKETS}={wrs[-1]:.0f}%")

            ax = axes[0][i]
            style_axes(ax)
            colors = ['#16a34a' if w >= 55 else '#f59e0b' if w >= 45 else '#ef4444' for w in wrs]
            ax.bar(labels, wrs, color=colors, alpha=0.85)
            ax.axhline(50, color='white', linewidth=1, alpha=0.5, linestyle='--')
            ax.set_title(name, fontsize=10)
            ax.set_ylabel('Win-Rate %')
            ax.set_xlabel('Score-Quartil (Q1=schwach)')

    fig.suptitle('probebot Signal-Staerke vs. Win-Rate', color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'score_strength', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
