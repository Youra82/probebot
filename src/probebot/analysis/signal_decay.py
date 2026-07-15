#!/usr/bin/env python3
"""
analysis/signal_decay.py — Signal-Decay-Analyse

dnabot-Aequivalent: genome_decay.py prueft wie schnell Genome-Muster mit
zunehmendem Alter ihre Vorhersagekraft verlieren. probebot hat keine
Genome-DB, aber dieselbe Grundfrage laesst sich direkt beantworten: verliert
die (einmalig auf den Trainingsdaten kalibrierte) Entry-Logik mit
zunehmendem Abstand zum Split-Datum an Kraft? Dazu wird der OOS-Zeitraum in
chronologische Buckets geteilt und Win-Rate/PnL je Bucket verglichen.
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

    print(f"\n{'='*60}\n  probebot — Signal-Decay-Analyse\n{'='*60}")
    print(f"  OOS-Zeitraum in {N_BUCKETS} chronologische Buckets geteilt.\n")

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
    caption_lines = ["probebot Signal-Decay\n"]

    for i, cfg in enumerate(configs):
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or not res.get('trades') or len(res['trades']) < N_BUCKETS * 3:
            print(f"  {Y}{name}: zu wenig Trades fuer Bucket-Analyse — uebersprungen.{NC}")
            continue

        trades = res['trades']  # bereits chronologisch aus dem Backtester
        bucket_size = len(trades) // N_BUCKETS
        buckets = [trades[j*bucket_size:(j+1)*bucket_size] for j in range(N_BUCKETS - 1)]
        buckets.append(trades[(N_BUCKETS-1)*bucket_size:])

        print(f"  {name}:")
        wrs, pnls = [], []
        for j, b in enumerate(buckets):
            if not b:
                wrs.append(0); pnls.append(0); continue
            wr = sum(1 for t in b if t['pnl'] > 0) / len(b) * 100
            pnl = sum(t['pnl'] for t in b)
            wrs.append(wr)
            pnls.append(pnl)
            col = G if wr >= 50 else Y if wr >= 40 else R
            print(f"    Bucket {j+1}/{N_BUCKETS} ({len(b)} Trades): WR={col}{wr:.1f}%{NC}  PnL={pnl:+.1f}")

        decay = wrs[0] - wrs[-1]
        if decay > 15:
            print(f"    {R}Deutlicher Decay: WR fiel um {decay:.1f} Punkte ueber die OOS-Periode.{NC}")
        elif decay > 5:
            print(f"    {Y}Leichter Decay: {decay:.1f} Punkte.{NC}")
        else:
            print(f"    {G}Stabil ueber die OOS-Periode (Decay: {decay:+.1f} Punkte).{NC}")
        caption_lines.append(f"{name}: WR-Decay {decay:+.1f}pp (Bucket1->{N_BUCKETS})")

        ax = axes[0][i]
        style_axes(ax)
        ax2 = ax.twinx()
        ax.bar(range(N_BUCKETS), pnls, color='#2563eb', alpha=0.6, label='PnL')
        ax2.plot(range(N_BUCKETS), wrs, color='#fbbf24', marker='o', linewidth=2, label='WinRate')
        ax2.axhline(50, color='#ef4444', linestyle='--', linewidth=1, alpha=0.6)
        ax.set_title(name, fontsize=10)
        ax.set_xlabel('Bucket (chronologisch)')
        ax.set_ylabel('PnL (Summe)', color='#93c5fd')
        ax2.set_ylabel('Win-Rate %', color='#fde68a')
        ax2.tick_params(colors='#94a3b8')

    fig.suptitle('probebot Signal-Decay ueber die OOS-Periode', color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'signal_decay', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
