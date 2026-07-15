#!/usr/bin/env python3
"""
analysis/fee_impact.py — Slippage & Fee Impact Analyse

Zeigt wie verschiedene Gebuehren-Niveaus die OOS-Performance beeinflussen
und ermittelt den Break-Even-Punkt. Nutzt den echten Backtester direkt
(fee_pct_per_side ist ein regulaerer Parameter von run_backtest).
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_backtest_custom,
    style_axes, save_send,
)

FEE_LEVELS = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Slippage & Fee Impact Analyse\n{'='*60}")
    print(f"  Bitget Taker-Gebuehr: 0.06%/Seite (Round-Trip: 0.12%)\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    for cfg in configs:
        name = config_name(cfg)
        print(f"\n  {name}:")
        print(f"  {'Gebuehr/Seite':>13}  {'PnL%':>10}  {'MaxDD%':>8}  {'Trades':>7}")
        print(f"  {'-'*44}")

        results_fee = []
        for fee in FEE_LEVELS:
            res = run_backtest_custom(cfg, {'fee_pct_per_side': fee})
            if res is None:
                continue
            results_fee.append({**res, 'fee': fee})
            col = G if res['pnl_pct'] > 0 else R
            marker = ' <- Bitget' if abs(fee - 0.06) < 0.001 else ''
            print(f"  {fee:>11.2f}%  {col}{res['pnl_pct']:>+9.1f}%{NC}  "
                  f"{res['max_drawdown']:>7.1f}%  {res['n_trades']:>7d}{marker}")

        if not results_fee:
            print(f"  {R}Keine Daten.{NC}")
            continue

        break_even = None
        for i in range(len(results_fee) - 1):
            if results_fee[i]['pnl_pct'] > 0 and results_fee[i+1]['pnl_pct'] <= 0:
                break_even = (results_fee[i]['fee'] + results_fee[i+1]['fee']) / 2
                break
        if break_even:
            print(f"  {Y}Break-Even Gebuehr: ~{break_even:.2f}%/Seite ({break_even*2:.2f}% Round-Trip){NC}")
        elif results_fee[0]['pnl_pct'] > 0:
            print(f"  {G}Profitabel bei allen getesteten Gebuehren.{NC}")
        else:
            print(f"  {R}Nicht profitabel — auch ohne Gebuehren.{NC}")

    # Chart fuer die erste (oder beste) Config als Beispiel-Visualisierung
    print()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(configs[:4]), figsize=(6 * min(len(configs), 4), 5), squeeze=False)
    fig.patch.set_facecolor('#0f172a')
    for i, cfg in enumerate(configs[:4]):
        ax = axes[0][i]
        style_axes(ax)
        name = config_name(cfg)
        fee_vals, pnl_vals = [], []
        for fee in FEE_LEVELS:
            res = run_backtest_custom(cfg, {'fee_pct_per_side': fee})
            if res:
                fee_vals.append(fee)
                pnl_vals.append(res['pnl_pct'])
        colors = ['#16a34a' if p > 0 else '#ef4444' for p in pnl_vals]
        ax.bar([f"{f:.2f}" for f in fee_vals], pnl_vals, color=colors, alpha=0.85)
        ax.axhline(0, color='#ef4444', linewidth=1, linestyle='--')
        ax.set_title(name, fontsize=10)
        ax.set_xlabel('Gebuehr/Seite %')
        ax.set_ylabel('PnL%')

    fig.suptitle('probebot Fee Impact | Bitget Taker = 0.06%/Seite', color='white', fontsize=12)
    plt.tight_layout()
    caption = "probebot Fee Impact Analyse\nBreak-Even-Gebuehr pro Config siehe Terminal-Ausgabe."
    save_send(fig, 'fee_impact', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
