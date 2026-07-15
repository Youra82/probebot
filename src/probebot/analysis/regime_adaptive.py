#!/usr/bin/env python3
"""
analysis/regime_adaptive.py — Regime-adaptive Parameter

Prueft ob ein je Regime unterschiedliches TP:SL-Verhaeltnis besser waere als
ein fixes. Fuer jedes Regime (TREND/RANGE/CHAOS) wird per RR-Sweep das beste
RR getrennt ermittelt — zeigt ob eine regime-abhaengige Parametrisierung
ueberhaupt Potenzial haette.
"""
import argparse
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, load_oos_data,
    run_backtest_custom, style_axes, save_send, equity_curve,
)

REGIME_LABELS = {1: 'TREND', 0: 'RANGE', -1: 'CHAOS'}
RR_LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Regime-adaptive Parameter\n{'='*60}\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    for cfg in configs:
        name = config_name(cfg)
        df_oos, _, _ = load_oos_data(cfg)
        if df_oos is None or 'regime' not in df_oos.columns:
            print(f"  {Y}{name}: kein regime-Feature — uebersprungen.{NC}")
            continue
        ts_to_regime = dict(zip(df_oos['timestamp'].astype(str), df_oos['regime']))
        start_capital = cfg.get('risk', {}).get('start_capital', 100.0)

        print(f"  {name}:")
        for regime, label in REGIME_LABELS.items():
            best_rr, best_pnl = None, -1e18
            for rr in RR_LEVELS:
                res = run_backtest_custom(cfg, {'tp_rr': rr})
                if res is None or not res.get('trades'):
                    continue
                kept = [t for t in res['trades'] if ts_to_regime.get(t['entry_ts']) == regime]
                if len(kept) < 5:
                    continue
                eq = equity_curve(kept, start_capital)
                pnl_pct = (eq[-1] - start_capital) / start_capital * 100
                if pnl_pct > best_pnl:
                    best_pnl, best_rr = pnl_pct, rr
            if best_rr is not None:
                print(f"    {label:<8} bestes RR: {best_rr:.1f}  (PnL bei diesem RR: {best_pnl:+.1f}%)")
            else:
                print(f"    {label:<8} zu wenig Trades in diesem Regime.")

    print(f"\n  {Y}Hinweis: unterschiedliche optimale RR je Regime deutet auf Potenzial fuer{NC}")
    print(f"  {Y}regime-adaptive Parametrisierung hin (aktuell nutzt probebot ein fixes RR).{NC}")
    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
