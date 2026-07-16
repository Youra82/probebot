"""
test_portfolio_simulator.py — synthetische Korrektheits-Checks fuer
portfolio_simulator.py. Kein pytest -- eigenstaendig ausfuehrbar, gleiche
Konvention wie test_gpu_parity.py/test_smoke.py.

Prueft gezielt die Verhaltensweisen die den neuen Simulator vom alten
(falschen) "isolierte Backtests summieren"-Ansatz unterscheiden:
  1. Tie-Break bei gleichzeitigem Signal + knappem Slot (config_order vs score)
  2. Kapital-Degradierung bis zur Ablehnung unter MIN_NOTIONAL_USDT
  3. Unterschiedliche Timeframes in einer Simulation (native Bar-Indizes)
  4. Ausstiegs-Logik-Abgleich gegen backtester.run_backtest() (Einzel-Leg,
     ein einziger Trade -- vor jeder Compounding-Drift identisch)

Ausfuehrung:
    python test_portfolio_simulator.py
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np


def _mk_df(n, start='2023-01-01', freq='1h', base_price=100.0, feat_a_at=None):
    """Baut einen simplen synthetischen OHLCV-DataFrame mit einer Feature-
    Spalte 'feat_a', die per Default 0 ist und an angegebenen Bar-Indizes auf
    1.0 gesetzt wird (Signal-Trigger)."""
    ts = pd.date_range(start, periods=n, freq=freq, tz='UTC')
    close = np.full(n, base_price)
    high  = close * 1.001
    low   = close * 0.999
    open_ = close.copy()
    feat_a = np.zeros(n)
    if feat_a_at:
        for i in feat_a_at:
            feat_a[i] = 1.0
    return pd.DataFrame({
        'timestamp': ts, 'open': open_, 'high': high, 'low': low,
        'close': close, 'volume': np.full(n, 1e6), 'feat_a': feat_a,
    })


def _entry_conditions(t_statistic=5.0):
    return {
        'UP': {'must_have': [], 'should_have': [
            {'feature': 'feat_a', 'direction': 'above', 'baseline_avg': 0.0, 't_statistic': t_statistic},
        ]},
    }


def _tradeable():
    return [{'move_type': 'UP', 'direction': 'LONG'}]


def _params(sl_pct=1.0, tp_rr=2.0, leverage=10.0, risk_per_trade_pct=1.0, max_hold_bars=24):
    return {
        't_threshold': 1.0, 'min_score': 1.0, 'min_hit_rate': 0.1,
        'sl_pct': sl_pct, 'tp_rr': tp_rr, 'leverage': leverage,
        'risk_per_trade_pct': risk_per_trade_pct, 'max_hold_bars': max_hold_bars,
        'fee_pct_per_side': 0.0,  # Gebuehren aus, damit die Testrechnung sauber bleibt
    }


def _config(symbol, timeframe, start_capital=100.0):
    return {
        'market': {'symbol': symbol, 'timeframe': timeframe},
        'strategy': {'type': 'TEST', 'tradeable_types': _tradeable()},
        'risk': {'start_capital': start_capital},
    }


def test_tie_break():
    print("Test 1: Tie-Break bei gleichzeitigem Signal + knappem Slot ...")
    from probebot.analysis.portfolio_simulator import _build_leg, _simulate

    n = 30
    trigger_bar = 15  # nach dem 10er-Warmup
    df = _mk_df(n, feat_a_at=[trigger_bar])

    cfg_a = _config('AAA/USDT:USDT', '1h', start_capital=50.0)
    cfg_b = _config('BBB/USDT:USDT', '1h', start_capital=50.0)
    leg_a = _build_leg(cfg_a, df, _entry_conditions(t_statistic=5.0), _tradeable(), _params())  # hoeherer Score
    leg_b = _build_leg(cfg_b, df, _entry_conditions(t_statistic=3.0), _tradeable(), _params())  # niedrigerer Score

    # configs-Reihenfolge: B zuerst gelistet, obwohl A den hoeheren Score hat
    ok = True

    sim_order = _simulate([leg_b, leg_a], max_open_positions=1, tie_break='config_order')
    filled_order = {t['symbol'] for t in sim_order['trades']}
    if filled_order != {'BBB/USDT:USDT'}:
        print(f"  FEHLER config_order: erwartet nur BBB gefuellt, bekam {filled_order}")
        ok = False
    if sim_order['n_signals_skipped_slot_limit'] != 1:
        print(f"  FEHLER config_order: n_skipped_slot={sim_order['n_signals_skipped_slot_limit']}, erwartet 1")
        ok = False

    leg_a2 = _build_leg(cfg_a, df, _entry_conditions(t_statistic=5.0), _tradeable(), _params())
    leg_b2 = _build_leg(cfg_b, df, _entry_conditions(t_statistic=3.0), _tradeable(), _params())
    sim_score = _simulate([leg_b2, leg_a2], max_open_positions=1, tie_break='score')
    filled_score = {t['symbol'] for t in sim_score['trades']}
    if filled_score != {'AAA/USDT:USDT'}:
        print(f"  FEHLER score: erwartet nur AAA (hoeherer Score) gefuellt, bekam {filled_score}")
        ok = False

    print(f"  config_order fuellt: {filled_order}  |  score fuellt: {filled_score}  ->  {'OK' if ok else 'FEHLGESCHLAGEN'}")
    return ok


def test_capital_scarcity():
    print("\nTest 2: Kapital-Degradierung bis zur Ablehnung ...")
    from probebot.analysis.portfolio_simulator import _build_leg, _simulate

    n = 40
    # Leg 1 signalisiert frueh und haelt lange (hoher max_hold_bars, weiter SL/TP
    # damit die Position waehrend Leg 2s Signal noch offen ist)
    df1 = _mk_df(n, feat_a_at=[12])
    df2 = _mk_df(n, feat_a_at=[20])  # spaeteres Signal, waehrend Leg1 noch offen ist

    cfg1 = _config('AAA/USDT:USDT', '1h', start_capital=10.0)
    cfg2 = _config('BBB/USDT:USDT', '1h', start_capital=10.0)
    # Leg1: leverage=1, risk_per_trade_pct=100 -> bindet nahezu das gesamte
    # verfuegbare Kapital als Margin (siehe LIVE_MARGIN_FRACTION-Degradierung)
    leg1 = _build_leg(cfg1, df1, _entry_conditions(), _tradeable(),
                       _params(sl_pct=1.0, leverage=1.0, risk_per_trade_pct=100.0, max_hold_bars=100))
    leg2 = _build_leg(cfg2, df2, _entry_conditions(), _tradeable(),
                       _params(sl_pct=1.0, leverage=10.0, risk_per_trade_pct=1.0, max_hold_bars=100))

    sim = _simulate([leg1, leg2], max_open_positions=2, tie_break='config_order')

    ok = True
    filled = {t['symbol'] for t in sim['trades']}
    if 'AAA/USDT:USDT' not in filled:
        print(f"  FEHLER: Leg1 (AAA) haette oeffnen muessen, trades={filled}")
        ok = False
    if 'BBB/USDT:USDT' in filled:
        print(f"  FEHLER: Leg2 (BBB) haette wegen Kapitalmangel NICHT oeffnen sollen")
        ok = False
    if sim['n_signals_skipped_capital'] < 1:
        print(f"  FEHLER: n_skipped_capital={sim['n_signals_skipped_capital']}, erwartet >=1")
        ok = False
    if sim['n_signals_skipped_slot_limit'] != 0:
        print(f"  FEHLER: n_skipped_slot={sim['n_signals_skipped_slot_limit']}, erwartet 0 (Slot war frei)")
        ok = False

    print(f"  gefuellt: {filled}  |  skipped_capital={sim['n_signals_skipped_capital']}  "
          f"skipped_slot={sim['n_signals_skipped_slot_limit']}  ->  {'OK' if ok else 'FEHLGESCHLAGEN'}")
    return ok


def test_cross_timeframe():
    print("\nTest 3: Unterschiedliche Timeframes (native Bar-Indizes) ...")
    from probebot.analysis.portfolio_simulator import _build_leg, _simulate

    # Leg A: 1h-Raster, 60 Bars. Leg B: 4h-Raster (nur jede 4. Stunde), 15 Bars,
    # ueberdeckt denselben Kalenderzeitraum.
    n_a, n_b = 60, 15
    df_a = _mk_df(n_a, freq='1h', feat_a_at=[15])
    # Index >= 10, sonst faellt das Signal in den Warmup (die ersten 10 Bars
    # werden wie in run_backtest()'s range(10, n) uebersprungen)
    df_b = _mk_df(n_b, freq='4h', feat_a_at=[11])

    cfg_a = _config('AAA/USDT:USDT', '1h', start_capital=100.0)
    cfg_b = _config('BBB/USDT:USDT', '4h', start_capital=100.0)
    # Sehr enger SL + wenige max_hold_bars, damit Leg B ueber TIMEOUT schliesst,
    # und wir bars_held direkt gegen die NATIVE (4h-)Bar-Anzahl pruefen koennen
    leg_a = _build_leg(cfg_a, df_a, _entry_conditions(), _tradeable(), _params(max_hold_bars=3))
    leg_b = _build_leg(cfg_b, df_b, _entry_conditions(), _tradeable(), _params(max_hold_bars=3))

    sim = _simulate([leg_a, leg_b], max_open_positions=2, tie_break='config_order')

    ok = True
    b_trades = [t for t in sim['trades'] if t['symbol'] == 'BBB/USDT:USDT']
    if not b_trades:
        print("  FEHLER: Leg B (4h) hat keinen Trade erzeugt")
        ok = False
    else:
        bh = b_trades[0]['bars_held']
        # TIMEOUT bei max_hold_bars=3 -> bars_held muss klein sein (native 4h-Baraenzahl),
        # NICHT die viel groessere Anzahl an 1h-Schritten die global vergangen sind
        if not (0 < bh <= 4):
            print(f"  FEHLER: Leg B bars_held={bh}, erwartet klein (native 4h-Bars, <=4)")
            ok = False
        if b_trades[0]['close_reason'] not in ('TIMEOUT', 'TP', 'SL'):
            print(f"  FEHLER: unerwarteter close_reason {b_trades[0]['close_reason']}")
            ok = False

    print(f"  Leg B Trades: {len(b_trades)}, bars_held={b_trades[0]['bars_held'] if b_trades else '-'}  "
          f"->  {'OK' if ok else 'FEHLGESCHLAGEN'}")
    return ok


def test_single_leg_parity():
    print("\nTest 4: Ausstiegs-Logik-Abgleich gegen run_backtest() (Einzel-Leg, 1 Trade) ...")
    from probebot.analysis.backtester import run_backtest
    from probebot.analysis.portfolio_simulator import _build_leg, _simulate

    n = 30
    # Bar 12: Signal. TP sehr nah (tp_rr klein + sl_pct groesser) -> schliesst
    # zuverlaessig als TP beim naechsten Bar (Preis ist konstant, high/low
    # oszilliert leicht um close, siehe _mk_df) -- fuer echten Trigger bauen
    # wir eine Kerze mit explizitem Ausschlag nach Signal-Bar.
    df = _mk_df(n, feat_a_at=[12], base_price=100.0)
    # Bar 13 (nach Entry auf Bar 12): kurzer Spike nach oben -> TP wird getroffen
    df.loc[13, 'high'] = 110.0

    cfg = _config('AAA/USDT:USDT', '1h', start_capital=100.0)
    params = _params(sl_pct=2.0, tp_rr=1.0, leverage=10.0, risk_per_trade_pct=1.0, max_hold_bars=50)
    entry_conditions = _entry_conditions()
    tradeable = _tradeable()

    ref = run_backtest(df, entry_conditions, tradeable, params, start_capital=100.0)

    leg = _build_leg(cfg, df, entry_conditions, tradeable, params)
    sim = _simulate([leg], max_open_positions=1, tie_break='config_order')

    ok = True
    if ref['n_trades'] != 1 or sim['n_trades'] != 1:
        print(f"  FEHLER: erwartet genau 1 Trade auf beiden Seiten, ref={ref['n_trades']} sim={sim['n_trades']}")
        return False

    rt = ref['trades'][0]
    st = sim['trades'][0]
    for field in ('entry_ts', 'close_ts', 'direction', 'move_type', 'close_reason'):
        if str(rt[field]) != str(st[field]):
            print(f"  MISMATCH [{field}]: ref={rt[field]}  sim={st[field]}")
            ok = False
    # Bei genau einem Trade und identischem Startkapital ist auch der PnL-Betrag
    # identisch (Compounding-Drift kann bei nur einem Trade noch nicht wirken)
    if abs(rt['pnl'] - st['pnl']) > 1e-6:
        print(f"  MISMATCH [pnl]: ref={rt['pnl']}  sim={st['pnl']}")
        ok = False

    print(f"  ref: {rt['close_reason']} @ {rt['close_ts']}, pnl={rt['pnl']}  |  "
          f"sim: {st['close_reason']} @ {st['close_ts']}, pnl={st['pnl']}  ->  {'OK' if ok else 'FEHLGESCHLAGEN'}")
    return ok


def main():
    results = [
        test_tie_break(),
        test_capital_scarcity(),
        test_cross_timeframe(),
        test_single_leg_parity(),
    ]
    print("\n" + "=" * 60)
    if all(results):
        print("ALLE TESTS BESTANDEN")
    else:
        print(f"FEHLGESCHLAGEN — {sum(1 for r in results if not r)}/{len(results)} Tests")
    print("=" * 60)
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
