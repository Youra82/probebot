#!/usr/bin/env python3
"""
analysis/monte_carlo.py — Monte Carlo Simulation (Bootstrap)

probebots Backtester setzt eine FESTE Positionsgroesse pro Trade (% vom
Startkapital, nicht vom laufenden Kapital) — die Reihenfolge der Trades
aendert daher die Endsumme nicht, nur den Verlauf (Drawdown-Pfad). Deshalb
wird hier mit ECHTEM Bootstrap resampled (mit Zuruecklegen): jede Simulation
zieht n Trades zufaellig (auch mehrfach) aus den echten OOS-Trades — das
bildet ab wie stark das Ergebnis vom konkreten Trade-Sample abhaengt.

Beantwortet:
  - Wie breit streut das Ergebnis, wenn man "eine andere Trade-Stichprobe"
    aus derselben zugrunde liegenden Verteilung gezogen haette?
  - Ruin-Wahrscheinlichkeit (Equity < 50% Start)?
"""
import argparse
import random
import sys

from probebot.analysis._common import (
    G, Y, R, NC, load_configs, config_name, run_oos_backtest,
    style_axes, save_send, max_drawdown_from_equity, prompt_capital_override,
)


def simulate_path(trades, start_capital):
    n = len(trades)
    sample = random.choices(trades, k=n)
    equity = [start_capital]
    cap = start_capital
    for t in sample:
        cap += t['pnl']
        equity.append(cap)
    return cap, max_drawdown_from_equity(equity)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--simulations', type=int, default=10000)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    print(f"\n{'='*60}\n  probebot — Monte Carlo Simulation (Bootstrap)\n{'='*60}")
    print(f"  Simulationen: {args.simulations:,}\n")

    configs = load_configs()
    if not configs:
        print(f"  {R}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"{Y}Kapital:{NC}")
    configs = prompt_capital_override(configs)
    print()

    random.seed(42)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    # Ein eigenes Chart pro Config statt einem gemeinsamen Riesenbild: bei
    # vielen Configs (13+) skaliert ein Panel-pro-Config-Layout entweder die
    # Bildhoehe oder -breite linear mit und wird auf dem Handy unlesbar (bei
    # 13 Configs bereits 2085x9684px). Ein kompakter Boxplot-Vergleich loest
    # zwar das Groessenproblem, verliert aber das Detail pro Strategie —
    # daher hier: jede Config bekommt ihr eigenes kleines Histogramm-Paar
    # und wird sofort einzeln verschickt.
    n_sent = 0
    for cfg in configs:
        name = config_name(cfg)
        res = run_oos_backtest(cfg)
        if res is None or not res.get('trades'):
            print(f"  {Y}{name}: keine Trades — uebersprungen.{NC}")
            continue

        trades = res['trades']
        start_capital = cfg.get('risk', {}).get('start_capital', 100.0)
        risk_pct = cfg.get('risk', {}).get('risk_per_trade_pct', 0.0)
        n_trades = len(trades)

        final_equities, max_dds = [], []
        for _ in range(args.simulations):
            eq, dd = simulate_path(trades, start_capital)
            final_equities.append(eq)
            max_dds.append(dd)

        equities_sorted = sorted(final_equities)
        pnl_pcts = sorted((e - start_capital) / start_capital * 100 for e in final_equities)
        max_dds_sorted = sorted(max_dds)
        p5  = pnl_pcts[int(0.05 * len(pnl_pcts))]
        p50 = pnl_pcts[int(0.50 * len(pnl_pcts))]
        p95 = pnl_pcts[int(0.95 * len(pnl_pcts))]
        eq5  = equities_sorted[int(0.05 * len(equities_sorted))]
        eq50 = equities_sorted[int(0.50 * len(equities_sorted))]
        eq95 = equities_sorted[int(0.95 * len(equities_sorted))]
        dd95 = max_dds_sorted[int(0.95 * len(max_dds_sorted))]
        ruin = sum(1 for e in final_equities if e < start_capital * 0.5) / args.simulations * 100
        profitable = sum(1 for p in pnl_pcts if p > 0) / args.simulations * 100

        print(f"  {name} ({n_trades} Trades, Start: {start_capital:.0f} USDT):")
        print(f"    5. Perzentil: {p5:+.1f}%   Median: {p50:+.1f}%   95. Perzentil: {p95:+.1f}%")
        print(f"    Endkapital:   {eq5:.0f}    {eq50:.0f}    {eq95:.0f}  USDT")
        print(f"    MaxDD 95. Perzentil: {dd95:.1f}%")
        col_ruin = R if ruin > 10 else Y if ruin > 2 else G
        print(f"    Ruin-Wahrscheinlichkeit (<50%): {col_ruin}{ruin:.1f}%{NC}   "
              f"Profitabel-Wahrscheinlichkeit: {profitable:.1f}%\n")

        dd50 = max_dds_sorted[int(0.50 * len(max_dds_sorted))]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        fig.patch.set_facecolor('#0f172a')
        style_axes(ax1, ax2)

        ax1.hist(pnl_pcts, bins=80, color='#2563eb', alpha=0.7, edgecolor='none')
        ax1.axvline(p5, color='#ef4444', linewidth=2, linestyle='--', label=f'5. Perzentil: {p5:+.0f}%')
        ax1.axvline(p50, color='#fbbf24', linewidth=2, linestyle='-', label=f'Median: {p50:+.0f}%')
        ax1.axvline(p95, color='#16a34a', linewidth=2, linestyle='--', label=f'95. Perzentil: {p95:+.0f}%')
        ax1.axvline(0, color='white', linewidth=1, alpha=0.4)
        ax1.set_xlabel('PnL% nach allen Trades')
        ax1.set_ylabel('Häufigkeit')
        ax1.set_title('Verteilung der Endkapitale', color='white')
        ax1.legend(fontsize=9, facecolor='#1e293b', labelcolor='white', framealpha=0.5)

        ax2.hist(max_dds, bins=60, color='#dc2626', alpha=0.7, edgecolor='none')
        ax2.axvline(dd50, color='#fbbf24', linewidth=2, linestyle='-', label=f'Median MaxDD: {dd50:.1f}%')
        ax2.axvline(dd95, color='#ef4444', linewidth=2, linestyle='--', label=f'95. Perzentil MaxDD: {dd95:.1f}%')
        ax2.set_xlabel('Maximaler Drawdown (%)')
        ax2.set_ylabel('Häufigkeit')
        ax2.set_title('Verteilung der Max Drawdowns', color='white')
        ax2.legend(fontsize=9, facecolor='#1e293b', labelcolor='white', framealpha=0.5)

        fig.suptitle(
            f'{name} Monte Carlo | {args.simulations:,} Simulationen | {n_trades} Trades | '
            f'Start: {start_capital:.0f} USDT | Risk/Trade: {risk_pct}% | '
            f'Ruin-Wahrsch. (<50%): {ruin:.1f}%',
            color='white', fontsize=11)
        plt.tight_layout()

        caption = (f"{name}  ({n_trades} Trades, Start: {start_capital:.0f} USDT)\n"
                   f"Median: {p50:+.1f}%  ({eq50:.0f} USDT)  |  P5: {p5:+.1f}%  |  P95: {p95:+.1f}%\n"
                   f"MaxDD P95: {dd95:.1f}%  |  Ruin: {ruin:.1f}%  |  Profitabel: {profitable:.1f}%")
        safe = name.replace(' ', '_').replace('/', '')
        save_send(fig, f'monte_carlo_{safe}', caption, args.no_telegram)
        n_sent += 1

    if n_sent == 0:
        print(f"  {R}Keine verwertbaren Ergebnisse.{NC}")
        return
    print(f"  {G}Analyse abgeschlossen — {n_sent} Chart(s).{NC}\n")


if __name__ == '__main__':
    main()
