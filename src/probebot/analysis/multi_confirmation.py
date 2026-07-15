#!/usr/bin/env python3
"""
analysis/multi_confirmation.py — Multi-Symbol Signal-Konfluenz

dnabot-Aequivalent (multitf_analysis.py) prueft ob gleichzeitige Signale auf
mehreren Pairs eine bessere Win-Rate haben. probebot hat kein Live-MTF-
Signal-Log, aber dieselbe Frage laesst sich ueber die Trade-Einstiegszeiten
mehrerer Configs beantworten: performen Trades die INNERHALB eines kurzen
Zeitfensters auf mehreren Symbolen gleichzeitig ausgeloest wurden anders als
isolierte Trades?
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

from probebot.analysis._common import G, Y, R, C, NC, load_configs, config_name, run_oos_backtest


def _parse_dt(ts_str):
    try:
        dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--window-hours', type=int, default=2)
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Multi-Symbol Signal-Konfluenz\n{'='*60}")
    print(f"  Gleichzeitigkeits-Fenster: {args.window_hours}h\n")

    configs = load_configs()
    if len(configs) < 2:
        print(f"  {R}Mindestens 2 Configs noetig fuer Konfluenz-Analyse.{NC}")
        sys.exit(1)

    all_trades = []
    for cfg in configs:
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None:
            continue
        for t in res.get('trades', []):
            dt = _parse_dt(t.get('entry_ts', ''))
            if dt:
                all_trades.append({'dt': dt, 'name': name, 'pnl': t['pnl'], 'win': t['pnl'] > 0})
    all_trades.sort(key=lambda t: t['dt'])

    if len(all_trades) < 10:
        print(f"  {R}Zu wenig Trades insgesamt.{NC}")
        sys.exit(1)

    window = timedelta(hours=args.window_hours)
    confirmed, isolated = [], []
    for i, t in enumerate(all_trades):
        others_nearby = any(
            other['name'] != t['name'] and abs((other['dt'] - t['dt']).total_seconds()) <= window.total_seconds()
            for other in all_trades if other is not t
        )
        (confirmed if others_nearby else isolated).append(t)

    def summarize(group, label):
        if not group:
            print(f"  {label}: keine Trades")
            return
        wr = sum(1 for t in group if t['win']) / len(group) * 100
        avg_pnl = sum(t['pnl'] for t in group) / len(group)
        col = G if wr >= 55 else Y if wr >= 45 else R
        print(f"  {label:<28} n={len(group):4d}  WR={col}{wr:5.1f}%{NC}  avg_pnl={avg_pnl:+.2f}")
        return wr

    print(f"  {len(all_trades)} Trades gesamt ueber {len(configs)} Configs\n")
    wr_confirmed = summarize(confirmed, f"Konfluenz (>=2 Symbole gleichzeitig)")
    wr_isolated = summarize(isolated, "Isoliert (nur 1 Symbol)")

    print()
    if wr_confirmed is not None and wr_isolated is not None:
        diff = wr_confirmed - wr_isolated
        if diff > 5:
            print(f"  {G}Konfluenz-Trades performen {diff:+.1f} Punkte besser — "
                  f"als zusaetzlicher Filter sinnvoll.{NC}")
        elif diff < -5:
            print(f"  {Y}Konfluenz-Trades performen {diff:+.1f} Punkte schlechter — "
                  f"kein Vorteil durch Gleichzeitigkeit.{NC}")
        else:
            print(f"  {C}Kein signifikanter Unterschied ({diff:+.1f} Punkte).{NC}")

    print(f"\n  {G}Analyse abgeschlossen.{NC}\n")


if __name__ == '__main__':
    main()
