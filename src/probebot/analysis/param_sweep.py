#!/usr/bin/env python3
"""
analysis/param_sweep.py — Parameter-Sweeps (RR-Ratio / Score-Threshold / SL%)

dnabot-Aequivalent: param_optimizer.py mit --param rr/score/callback.
probebot hat kein Trailing-Callback (nutzt fixes SL/TP), daher wird die
dritte Sweep-Dimension durch sl_pct ersetzt.

Alle Sweeps laufen auf den ECHTEN OOS-Daten (kein Blick auf Trainingsdaten)
— das ist bewusst kein Re-Optimieren, sondern zeigt wie empfindlich die
bereits optimierte Config auf Verschiebungen dieses einen Parameters ist.
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_backtest_custom,
    style_axes, save_send,
)

RR_LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
REL_LEVELS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]  # Multiplikator auf den Config-Wert

PARAM_INFO = {
    'rr':    {'key': 'tp_rr',    'label': 'TP:SL Ratio',      'mode': 'absolute', 'levels': RR_LEVELS},
    'score': {'key': 'min_score','label': 'Min-Score (rel.)', 'mode': 'relative', 'levels': REL_LEVELS},
    'sl':    {'key': 'sl_pct',   'label': 'SL% (rel.)',       'mode': 'relative', 'levels': REL_LEVELS},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--param', choices=['rr', 'score', 'sl'], required=True)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    info = PARAM_INFO[args.param]
    key, label, mode, levels = info['key'], info['label'], info['mode'], info['levels']

    print(f"\n{'='*60}\n  probebot — Parameter-Sweep: {label}\n{'='*60}\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    n_cfg = len(configs)
    fig, axes = plt.subplots(1, n_cfg, figsize=(5 * n_cfg, 5), squeeze=False)
    fig.patch.set_facecolor('#0f172a')

    caption_lines = [f"probebot Parameter-Sweep: {label}\n"]

    for i, cfg in enumerate(configs):
        name = config_name(cfg)
        base_val = {**cfg.get('signal', {}), **cfg.get('risk', {})}.get(key)
        if base_val is None:
            print(f"  {Y}{name}: Parameter '{key}' nicht in Config — uebersprungen.{NC}")
            continue

        print(f"\n  {name}  (aktueller Wert: {base_val}):")
        print(f"  {'Wert':>10}  {'PnL%':>10}  {'MaxDD%':>8}  {'WinRate':>8}  {'Trades':>7}")
        print(f"  {'-'*48}")

        x_vals, pnl_vals = [], []
        for level in levels:
            test_val = level if mode == 'absolute' else round(base_val * level, 4)
            res = run_backtest_custom(cfg, {key: test_val})
            if res is None:
                continue
            marker = ' <- aktuell' if mode == 'relative' and abs(level - 1.0) < 1e-9 else ''
            col = G if res['pnl_pct'] > 0 else R
            print(f"  {test_val:>10.3f}  {col}{res['pnl_pct']:>+9.1f}%{NC}  "
                  f"{res['max_drawdown']:>7.1f}%  {res['win_rate']:>7.1f}%  {res['n_trades']:>7d}{marker}")
            x_vals.append(test_val)
            pnl_vals.append(res['pnl_pct'])

        if not x_vals:
            continue

        best_idx = pnl_vals.index(max(pnl_vals))
        print(f"  {Y}Bestes PnL bei {label}={x_vals[best_idx]}: {pnl_vals[best_idx]:+.1f}%{NC}")
        caption_lines.append(f"{name}: bestes PnL bei {x_vals[best_idx]} ({pnl_vals[best_idx]:+.0f}%)")

        ax = axes[0][i]
        style_axes(ax)
        colors = ['#16a34a' if p > 0 else '#ef4444' for p in pnl_vals]
        ax.bar([f"{v:.2f}" for v in x_vals], pnl_vals, color=colors, alpha=0.85)
        ax.axhline(0, color='white', linewidth=0.8, alpha=0.4)
        if mode == 'relative':
            base_idx = min(range(len(levels)), key=lambda k: abs(levels[k] - 1.0))
            if base_idx < len(x_vals):
                ax.get_xticklabels()[base_idx].set_color('#fbbf24')
        ax.set_title(name, fontsize=10)
        ax.set_xlabel(label)
        ax.set_ylabel('PnL%')

    fig.suptitle(f'probebot Parameter-Sweep: {label}', color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, f'param_sweep_{args.param}', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
