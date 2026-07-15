#!/usr/bin/env python3
"""
analysis/bootstrap_test.py — Bootstrap Signifikanztest

dnabot-Aequivalent testet Genome-Win-Raten gegen 50%. probebot hat keine
Genome-DB, aber die OOS-Trades sind nach Bewegungstyp (move_type) gruppiert
— genau die Einheit, die auch im bot_spec OOS-validiert wurde. Testet per
Binomial-Test ob die Win-Rate jedes Typs signifikant ueber 50% liegt.
"""
import argparse
import sys
from collections import defaultdict

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest,
    style_axes, save_send,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-samples', type=int, default=10)
    parser.add_argument('--alpha', type=float, default=0.05)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Bootstrap Signifikanztest\n{'='*60}")
    print(f"  Min. Samples: {args.min_samples} | Alpha: {args.alpha}\n")

    try:
        from scipy import stats
    except ImportError:
        print(f"  {R}scipy fehlt: pip install scipy{NC}")
        sys.exit(1)

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    results = []
    for cfg in configs:
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or not res.get('trades'):
            continue
        by_type = defaultdict(list)
        for t in res['trades']:
            by_type[t['move_type']].append(t)

        for mtype, trades in by_type.items():
            n = len(trades)
            if n < args.min_samples:
                continue
            w = sum(1 for t in trades if t['pnl'] > 0)
            wr = w / n
            p = stats.binomtest(w, n, p=0.5, alternative='greater').pvalue
            results.append({'name': name, 'move_type': mtype, 'wr': wr, 'n': n, 'p': p})

    if not results:
        print(f"  {R}Keine Kombinationen mit >= {args.min_samples} Trades.{NC}")
        sys.exit(1)

    sig = [r for r in results if r['p'] < args.alpha]
    nsig = [r for r in results if r['p'] >= args.alpha]
    pct = len(sig) / len(results) * 100

    print(f"  {len(results)} (Config, Bewegungstyp)-Kombinationen mit >= {args.min_samples} Trades\n")
    print(f"  Signifikant (p<{args.alpha}): {G}{len(sig)}{NC}/{len(results)} ({pct:.1f}%)")
    print(f"  Nicht signifikant:         {Y}{len(nsig)}{NC}/{len(results)}\n")

    top = sorted(sig, key=lambda x: x['p'])[:15]
    print(f"  Top signifikante Kombinationen:")
    for r in top:
        print(f"  p={r['p']:.4f}  WR={r['wr']:.1%}  n={r['n']:4d}  {r['name']:<14} {r['move_type']}")

    # Chart
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax1, ax2)

    wrs = [r['wr'] for r in results]
    ps = [r['p'] for r in results]
    ns = [r['n'] for r in results]
    colors_p = ['#16a34a' if p < args.alpha else '#64748b' for p in ps]

    ax1.scatter(ns, wrs, c=colors_p, alpha=0.7, s=30)
    ax1.axhline(0.5, color='#ef4444', linestyle='--', linewidth=1.5, label='Baseline 50%')
    ax1.set_xlabel('Sample-Groesse (n)')
    ax1.set_ylabel('Win-Rate')
    ax1.set_title(f'Win-Rate vs. Sample-Groesse\n{len(sig)} signifikant (gruen), {len(nsig)} nicht (grau)', fontsize=10)
    ax1.legend(facecolor='#1e293b', labelcolor='white')

    ax2.hist(ps, bins=30, color='#2563eb', alpha=0.8, edgecolor='none')
    ax2.axvline(args.alpha, color='#ef4444', linewidth=2, linestyle='--', label=f'alpha={args.alpha}')
    ax2.set_xlabel('p-Wert')
    ax2.set_ylabel('Anzahl')
    ax2.set_title('p-Wert Verteilung', fontsize=10)
    ax2.legend(facecolor='#1e293b', labelcolor='white')

    fig.suptitle(f'probebot Bootstrap Signifikanztest | {len(results)} Kombinationen | '
                 f'{pct:.1f}% signifikant (p<{args.alpha})', color='white', fontsize=11)
    plt.tight_layout()

    caption = (f"probebot Bootstrap Signifikanztest\n{len(results)} (Config,Typ)-Kombinationen\n"
               f"Signifikant (p<{args.alpha}): {len(sig)} ({pct:.1f}%)\n"
               f"Nicht signifikant: {len(nsig)} ({100-pct:.1f}%)")
    save_send(fig, 'bootstrap_test', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
