#!/usr/bin/env python3
"""
analysis/sensitivity.py — Parameter Sensitivity (Tornado-Diagramm)

Variiert jeden Signal-/Risk-Parameter der Config einzeln um +/-20% und misst
die PnL-Auswirkung. Breiter Balken = die Config haengt stark an einem exakt
getroffenen Wert = Overfitting-Risiko. Schmaler Balken = robust.
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest,
    run_backtest_custom, style_axes, save_send,
)

SWEEP_PARAMS = ['t_threshold', 'min_score', 'min_hit_rate', 'sl_pct', 'tp_rr', 'max_hold_bars']
DELTA = 0.20  # +/- 20%


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Parameter Sensitivity Analysis\n{'='*60}")
    print(f"  Variation je Parameter: +/-{DELTA*100:.0f}%\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    n_cfg = len(configs)
    fig, axes = plt.subplots(n_cfg, 1, figsize=(10, 2.5 * n_cfg), squeeze=False)
    fig.patch.set_facecolor('#0f172a')
    caption_lines = ["probebot Parameter Sensitivity (Tornado)\n"]

    for row, cfg in enumerate(configs):
        name = config_name(cfg)
        params = {**cfg.get('signal', {}), **cfg.get('risk', {})}
        base_res = run_oos_backtest(cfg)
        if base_res is None:
            print(f"  {Y}{name}: keine Daten — uebersprungen.{NC}")
            continue
        base_pnl = base_res['pnl_pct']

        print(f"\n  {name}  (Basis-PnL: {base_pnl:+.1f}%):")
        impacts = []
        for p in SWEEP_PARAMS:
            base_val = params.get(p)
            if base_val is None or base_val == 0:
                continue
            lo_val = base_val * (1 - DELTA)
            hi_val = base_val * (1 + DELTA)
            if p == 'max_hold_bars':
                lo_val, hi_val = int(lo_val), int(hi_val)

            res_lo = run_backtest_custom(cfg, {p: lo_val})
            res_hi = run_backtest_custom(cfg, {p: hi_val})
            pnl_lo = res_lo['pnl_pct'] if res_lo else base_pnl
            pnl_hi = res_hi['pnl_pct'] if res_hi else base_pnl
            spread = abs(pnl_hi - pnl_lo)
            impacts.append({'param': p, 'lo': pnl_lo, 'hi': pnl_hi, 'spread': spread})
            print(f"    {p:<16} -20%: {pnl_lo:+8.1f}%   +20%: {pnl_hi:+8.1f}%   Spread: {spread:.1f}")

        if not impacts:
            continue
        impacts.sort(key=lambda x: x['spread'])
        caption_lines.append(f"{name}: sensibelster Parameter = "
                              f"{max(impacts, key=lambda x: x['spread'])['param']}")

        ax = axes[row][0]
        style_axes(ax)
        y_pos = range(len(impacts))
        for j, imp in enumerate(impacts):
            lo, hi = sorted([imp['lo'] - base_pnl, imp['hi'] - base_pnl])
            ax.barh(j, hi - lo, left=lo, color='#f59e0b', alpha=0.8, height=0.6)
        ax.axvline(0, color='white', linewidth=1, alpha=0.6)
        ax.set_yticks(list(y_pos))
        ax.set_yticklabels([imp['param'] for imp in impacts], fontsize=8)
        ax.set_xlabel('PnL%-Abweichung von Basis')
        ax.set_title(f'{name} (Basis-PnL: {base_pnl:+.1f}%)', fontsize=10)

    fig.suptitle('probebot Parameter Sensitivity | breiter Balken = Overfitting-Risiko',
                 color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'sensitivity', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
