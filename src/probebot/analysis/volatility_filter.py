#!/usr/bin/env python3
"""
analysis/volatility_filter.py — Volatilitaets-Filter Optimierung

Testet ob das Ausschliessen von Trades bei extremer Volatilitaet (ATR-Z-Score
am Entry) die Performance verbessert. Post-hoc-Filter auf den echten OOS-
Trades (kein Re-Backtest noetig, da nur eine Teilmenge der Trades behalten wird).
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, load_oos_data, run_oos_backtest,
    style_axes, save_send, equity_curve, max_drawdown_from_equity,
)

ATR_Z_THRESHOLDS = [None, 1.0, 1.5, 2.0, 2.5, 3.0]  # None = kein Filter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Volatilitaets-Filter Optimierung\n{'='*60}")
    print(f"  Schliesst Trades aus bei denen |ATR-Z| > Schwelle am Entry.\n")

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
    caption_lines = ["probebot Volatilitaets-Filter\n"]

    for i, cfg in enumerate(configs):
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or not res.get('trades'):
            print(f"  {Y}{name}: keine Trades — uebersprungen.{NC}")
            continue
        df_oos, _, _ = load_oos_data(cfg)
        if df_oos is None or 'atr_z' not in df_oos.columns:
            print(f"  {Y}{name}: kein atr_z-Feature — uebersprungen.{NC}")
            continue

        start_capital = cfg.get('risk', {}).get('start_capital', 100.0)
        ts_to_atrz = dict(zip(df_oos['timestamp'].astype(str), df_oos['atr_z']))

        print(f"  {name}:")
        print(f"  {'|ATR-Z| max':>12}  {'Trades':>7}  {'PnL%':>10}  {'MaxDD%':>8}  {'WinRate':>8}")
        print(f"  {'-'*52}")

        labels, pnls = [], []
        for thresh in ATR_Z_THRESHOLDS:
            if thresh is None:
                kept = res['trades']
            else:
                kept = [t for t in res['trades']
                        if abs(ts_to_atrz.get(t['entry_ts'], 0)) <= thresh]
            if not kept:
                continue
            eq = equity_curve(kept, start_capital)
            pnl_pct = (eq[-1] - start_capital) / start_capital * 100
            dd = max_drawdown_from_equity(eq)
            wr = sum(1 for t in kept if t['pnl'] > 0) / len(kept) * 100
            label = 'kein Filter' if thresh is None else f"<={thresh}"
            col = G if pnl_pct > 0 else R
            print(f"  {label:>12}  {len(kept):>7d}  {col}{pnl_pct:>+9.1f}%{NC}  {dd:>7.1f}%  {wr:>7.1f}%")
            labels.append(label)
            pnls.append(pnl_pct)

        if pnls:
            best_idx = pnls.index(max(pnls))
            caption_lines.append(f"{name}: bestes PnL bei Filter={labels[best_idx]} ({pnls[best_idx]:+.0f}%)")
            ax = axes[0][i]
            style_axes(ax)
            colors = ['#16a34a' if p > 0 else '#ef4444' for p in pnls]
            ax.bar(labels, pnls, color=colors, alpha=0.85)
            ax.axhline(0, color='white', linewidth=0.8, alpha=0.4)
            ax.set_title(name, fontsize=10)
            ax.set_xlabel('ATR-Z Filter')
            ax.set_ylabel('PnL%')
            plt.setp(ax.get_xticklabels(), rotation=30, ha='right', fontsize=8)

    fig.suptitle('probebot Volatilitaets-Filter Sweep', color='white', fontsize=12)
    plt.tight_layout()
    save_send(fig, 'volatility_filter', "\n".join(caption_lines), args.no_telegram)
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
