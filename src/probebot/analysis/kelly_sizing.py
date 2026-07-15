#!/usr/bin/env python3
"""
analysis/kelly_sizing.py — Kelly Position Sizing

Kelly% = p - (1-p)/b   (p = Win-Rate, b = R:R aus der Config)

Vergleicht die aktuell konfigurierte risk_per_trade_pct mit der Kelly-
(bzw. Half-Kelly-)empfohlenen Positionsgroesse, jeweils auf den echten
OOS-Trades nachgerechnet.
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest,
    run_backtest_custom, style_axes, save_send,
)


def kelly_fraction(win_rate: float, rr: float) -> float:
    if rr <= 0:
        return 0.0
    k = win_rate - (1 - win_rate) / rr
    return max(k * 100.0, 0.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--half-kelly', action='store_true', default=True)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Kelly Position Sizing\n{'='*60}")
    print(f"  Half-Kelly: {args.half_kelly}\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    stats = []
    print(f"  {'Config':<14}  {'WR':>6}  {'RR':>5}  {'Aktuell%':>9}  {'Kelly%':>7}  "
          f"{'PnL@Aktuell':>12}  {'PnL@Kelly':>10}")
    print(f"  {'-'*78}")

    for cfg in configs:
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or res['n_trades'] < 5:
            continue
        wr = res['win_rate'] / 100.0
        rr = cfg.get('risk', {}).get('tp_rr', 2.0)
        current_risk = cfg.get('risk', {}).get('risk_per_trade_pct', 1.0)

        k = kelly_fraction(wr, rr)
        if args.half_kelly:
            k /= 2.0
        k = min(k, 10.0)  # Sicherheitsdeckel — volles Kelly kann unrealistisch hoch sein

        res_kelly = run_backtest_custom(cfg, {'risk_per_trade_pct': k}) if k > 0 else res

        stats.append({'name': name, 'wr': wr, 'rr': rr, 'current': current_risk, 'kelly': k,
                       'pnl_current': res['pnl_pct'], 'pnl_kelly': res_kelly['pnl_pct'] if res_kelly else 0,
                       'dd_current': res['max_drawdown'], 'dd_kelly': res_kelly['max_drawdown'] if res_kelly else 0})

        print(f"  {name:<14}  {wr:>5.1%}  {rr:>5.2f}  {current_risk:>8.2f}%  {k:>6.2f}%  "
              f"{res['pnl_pct']:>+11.1f}%  {(res_kelly['pnl_pct'] if res_kelly else 0):>+9.1f}%")

    if not stats:
        print(f"\n  {R}Keine auswertbaren Configs (min. 5 Trades noetig).{NC}")
        sys.exit(1)

    # Chart
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor('#0f172a')
    style_axes(ax1, ax2)

    names = [s['name'] for s in stats]
    x = np.arange(len(names))
    width = 0.35
    ax1.bar(x - width/2, [s['current'] for s in stats], width, label='Aktuell', color='#2563eb', alpha=0.85)
    ax1.bar(x + width/2, [s['kelly'] for s in stats], width,
            label='Half-Kelly' if args.half_kelly else 'Kelly', color='#f59e0b', alpha=0.85)
    ax1.set_xticks(x)
    ax1.set_xticklabels(names, rotation=30, ha='right', fontsize=8)
    ax1.set_ylabel('Risk pro Trade %')
    ax1.set_title('Aktuelles Risk% vs. Kelly-Empfehlung', fontsize=10)
    ax1.legend(facecolor='#1e293b', labelcolor='white')

    ax2.bar(x - width/2, [s['pnl_current'] for s in stats], width, label='Aktuell', color='#2563eb', alpha=0.85)
    ax2.bar(x + width/2, [s['pnl_kelly'] for s in stats], width, label='Kelly-Risk', color='#f59e0b', alpha=0.85)
    ax2.axhline(0, color='white', linewidth=0.8, alpha=0.4)
    ax2.set_xticks(x)
    ax2.set_xticklabels(names, rotation=30, ha='right', fontsize=8)
    ax2.set_ylabel('OOS PnL%')
    ax2.set_title('PnL: Aktuelles Risk% vs. Kelly-Risk%', fontsize=10)
    ax2.legend(facecolor='#1e293b', labelcolor='white')

    fig.suptitle('probebot Kelly Position Sizing', color='white', fontsize=12)
    plt.tight_layout()

    caption = "probebot Kelly Sizing\n" + "\n".join(
        f"{s['name']}: aktuell {s['current']:.1f}% -> Kelly {s['kelly']:.1f}%" for s in stats)
    save_send(fig, 'kelly_sizing', caption, args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
