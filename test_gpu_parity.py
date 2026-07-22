"""
test_gpu_parity.py — Korrektheits-Check: gpu_backtester.run_backtest_batch() muss
fuer jede einzelne Parameter-Kombination EXAKT (innerhalb Fliesskomma-Toleranz)
dasselbe Ergebnis liefern wie backtester.run_backtest() (die Referenz).

pytest -- Teil der probebot-Test-Suite (siehe run_tests.sh).
"""
import random
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, 'src')

from probebot.analysis.backtester import run_backtest
from probebot.analysis.gpu_backtester import run_backtest_batch, resolve_device

FIELDS_TO_COMPARE = [
    'n_trades', 'win_rate', 'pnl_pct', 'max_drawdown', 'sharpe',
    'profit_factor', 'avg_win', 'avg_loss', 'end_capital', 'n_liquidations',
]
TOL = 1e-4  # relative/absolute Toleranz -- Fliesskomma-Summierungsreihenfolge
            # unterscheidet sich zwischen sequenzieller Python-Schleife und Matmul.


def build_synthetic_data(n=3000, seed=42, inject_edge_cases=True):
    """Random-Walk-OHLCV + ein paar synthetische 'Feature'-Spalten mit
    kontrollierter Verteilung, damit wir die Entry-Conditions selbst bauen
    koennen (kein Bezug zur echten Feature-Pipeline noetig)."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.standard_normal(n) * 1.5)
    close = np.abs(close) + 10
    # bewusst hohe Volatilitaet je Kerze, damit SL/TP/Liquidation im selben Bar
    # kollidieren koennen (Randfall) statt nur ueber viele Bars verteilt zu sein
    high = close * (1 + np.abs(rng.standard_normal(n)) * 0.02)
    low = close * (1 - np.abs(rng.standard_normal(n)) * 0.02)
    open_ = close + rng.standard_normal(n) * 0.5

    feat_a = rng.standard_normal(n)          # richtungsneutrales Feature
    feat_b = rng.standard_normal(n) * 2 + 1  # zweites Feature, andere Skala
    feat_c = rng.standard_normal(n)

    if inject_edge_cases:
        # NaN-Feature-Werte (muessen wie in compute_signal_score als "nicht
        # eligible" behandelt werden)
        nan_idx = rng.choice(n, size=max(5, n // 200), replace=False)
        feat_a[nan_idx] = np.nan
        # Datenluecke: close <= 0 -> muss komplett uebersprungen werden
        gap_idx = rng.choice(n, size=3, replace=False)
        close[gap_idx] = 0.0

    df = pd.DataFrame({
        'timestamp': pd.date_range('2022-01-01', periods=n, freq='1h', tz='UTC'),
        'open': open_, 'high': high, 'low': low, 'close': close,
        'volume': np.abs(rng.standard_normal(n) * 1e5 + 1e6),
        'feat_a': feat_a, 'feat_b': feat_b, 'feat_c': feat_c,
    })
    return df


def build_entry_conditions():
    """Zwei Move-Types mit ueberlappenden t-Statistik-Werten (fuer Score-
    Gleichstand-Randfaelle) und unterschiedlicher Bedingungszahl."""
    entry_conditions = {
        'IMPULSE_UP': {
            'must_have': [],
            'should_have': [
                {'feature': 'feat_a', 'direction': 'above', 'baseline_avg': 0.0, 't_statistic': 5.0},
                {'feature': 'feat_b', 'direction': 'above', 'baseline_avg': 1.0, 't_statistic': 3.2},
                {'feature': 'feat_c', 'direction': 'below', 'baseline_avg': 0.0, 't_statistic': 4.1},
            ],
        },
        'IMPULSE_DOWN': {
            'must_have': [],
            'should_have': [
                {'feature': 'feat_a', 'direction': 'below', 'baseline_avg': 0.0, 't_statistic': 5.0},
                {'feature': 'feat_c', 'direction': 'above', 'baseline_avg': 0.0, 't_statistic': 3.9},
            ],
        },
    }
    tradeable_move_types = [
        {'move_type': 'IMPULSE_UP', 'direction': 'LONG'},
        {'move_type': 'IMPULSE_DOWN', 'direction': 'SHORT'},
    ]
    return entry_conditions, tradeable_move_types


def random_params(rng, n):
    """Zieht n Parameter-Kombinationen ueber exakt denselben Suchraum wie
    optimizer.py's Optuna trial.suggest_* Aufrufe."""
    out = []
    for _ in range(n):
        out.append({
            't_threshold': rng.uniform(2.0, 8.0),
            'min_score': rng.uniform(1.0, 15.0),   # niedriger als Optuna-Range (5-100)
                                                     # damit im kleinen Synthetik-Datensatz
                                                     # ueberhaupt genug Trades entstehen
            'min_hit_rate': rng.uniform(0.2, 0.85),
            'sl_pct': rng.uniform(0.5, 5.0),
            'tp_rr': rng.uniform(1.0, 5.0),
            'leverage': rng.integers(3, 21),        # inkl. Extremfaelle fuer Liquidation
            'risk_per_trade_pct': rng.uniform(0.5, 3.0),
            'max_hold_bars': rng.integers(3, 97),
        })
    return out


def _mismatches(a, b, name, idx):
    diffs = []
    for k in FIELDS_TO_COMPARE:
        va, vb = a.get(k, 0), b.get(k, 0)
        if isinstance(va, int) and isinstance(vb, int):
            if va != vb:
                diffs.append(f"{k}: ref={va} batch={vb}")
            continue
        if abs(va - vb) > TOL + TOL * abs(va):
            diffs.append(f"{k}: ref={va} batch={vb} (diff={abs(va-vb):.6g})")
    if diffs:
        print(f"  MISMATCH [{name} #{idx}]: " + " | ".join(diffs))
    return diffs


@pytest.fixture(scope='module')
def synthetic_setup():
    df = build_synthetic_data()
    entry_conditions, tradeable = build_entry_conditions()
    return df, entry_conditions, tradeable


def test_single_trial_cpu_parity(synthetic_setup):
    print("Test: Einzel-Trial (CPU) ...")
    df, entry_conditions, tradeable = synthetic_setup
    fixed_params = {
        't_threshold': 3.5, 'min_score': 4.0, 'min_hit_rate': 0.3,
        'sl_pct': 1.5, 'tp_rr': 2.0, 'leverage': 10, 'risk_per_trade_pct': 1.0,
        'max_hold_bars': 24,
    }
    ref = run_backtest(df, entry_conditions, tradeable, fixed_params, start_capital=100.0)
    device_cpu, _ = resolve_device('cpu')
    batch = run_backtest_batch(df, entry_conditions, tradeable, [fixed_params],
                                start_capital=100.0, device=device_cpu)[0]
    print(f"  ref n_trades={ref['n_trades']} pnl_pct={ref['pnl_pct']}  |  "
          f"batch n_trades={batch['n_trades']} pnl_pct={batch['pnl_pct']}")
    diffs = _mismatches(ref, batch, "single-cpu", 0)
    assert not diffs, f"CPU-Batch weicht vom Referenz-Backtest ab: {diffs}"


def test_batch_200_cpu_parity(synthetic_setup):
    print("\nTest: Batch von 200 Zufallsparametern (CPU) ...")
    df, entry_conditions, tradeable = synthetic_setup
    device_cpu, _ = resolve_device('cpu')
    rng = np.random.default_rng(7)
    params_list = random_params(rng, 200)

    refs = [run_backtest(df, entry_conditions, tradeable, p, start_capital=100.0) for p in params_list]
    batches = run_backtest_batch(df, entry_conditions, tradeable, params_list,
                                  start_capital=100.0, device=device_cpu)

    n_mismatch = 0
    n_with_trades = sum(1 for r in refs if r['n_trades'] > 0)
    n_liq_total = sum(r.get('n_liquidations', 0) for r in refs)
    for idx, (r, b) in enumerate(zip(refs, batches)):
        if _mismatches(r, b, "batch200-cpu", idx):
            n_mismatch += 1
    print(f"  {len(params_list)} Trials, {n_with_trades} mit Trades, {n_liq_total} Liquidationen insgesamt")
    print(f"  Mismatches: {n_mismatch}/{len(params_list)}")
    assert n_mismatch == 0


def test_batch_200_cuda_parity(synthetic_setup):
    df, entry_conditions, tradeable = synthetic_setup
    device_cuda, reason_cuda = resolve_device('cuda')
    if device_cuda.type != 'cuda':
        pytest.skip(f"CUDA nicht verfuegbar ({reason_cuda})")

    print(f"\nTest: Batch von 200 Zufallsparametern (CUDA: {reason_cuda}) ...")
    device_cpu, _ = resolve_device('cpu')
    rng = np.random.default_rng(7)
    params_list = random_params(rng, 200)
    refs = [run_backtest(df, entry_conditions, tradeable, p, start_capital=100.0) for p in params_list]
    batches_gpu = run_backtest_batch(df, entry_conditions, tradeable, params_list,
                                      start_capital=100.0, device=device_cuda)
    n_mismatch_gpu = 0
    for idx, (r, b) in enumerate(zip(refs, batches_gpu)):
        if _mismatches(r, b, "batch200-cuda", idx):
            n_mismatch_gpu += 1
    print(f"  Mismatches (CUDA vs. Referenz): {n_mismatch_gpu}/{len(params_list)}")
    assert n_mismatch_gpu == 0


def test_forced_liquidation_parity(synthetic_setup):
    print("\nTest: Erzwungene Liquidation vs. sichere Konfiguration (CPU) ...")
    df, entry_conditions, tradeable = synthetic_setup
    device_cpu, _ = resolve_device('cpu')
    liq_params = []
    for lev in (30, 40, 50, 60):
        for slp in (3.0, 4.0, 5.0):
            liq_params.append({
                't_threshold': 3.0, 'min_score': 4.0, 'min_hit_rate': 0.3,
                'sl_pct': slp, 'tp_rr': 2.0, 'leverage': lev,
                'risk_per_trade_pct': 1.0, 'max_hold_bars': 30,
            })
    refs4 = [run_backtest(df, entry_conditions, tradeable, p, start_capital=100.0) for p in liq_params]
    batches4 = run_backtest_batch(df, entry_conditions, tradeable, liq_params,
                                   start_capital=100.0, device=device_cpu)
    n_liq4 = sum(r.get('n_liquidations', 0) for r in refs4)
    n_mismatch4 = 0
    for idx, (r, b) in enumerate(zip(refs4, batches4)):
        if _mismatches(r, b, "liq-stress-cpu", idx):
            n_mismatch4 += 1
    print(f"  {len(liq_params)} Trials (hoher Hebel), {n_liq4} Liquidationen insgesamt")
    print(f"  Mismatches: {n_mismatch4}/{len(liq_params)}")
    assert n_mismatch4 == 0
    assert n_liq4 > 0, "kein einziger Liquidations-Trade ausgeloest — Testdaten pruefen."


def test_single_trade_sharpe_not_absurd():
    """
    Regressionstest fuer einen echten Bug (gefunden ueber show_results.sh im
    Live-Betrieb): mit genau 1 Trade wurde std_pnl frueher auf 1e-9 statt 0.0
    gesetzt, wodurch mean_pnl/std_pnl auf zweistellige Millionenwerte
    explodierte statt korrekt auf Sharpe=0.0 zurueckzufallen (Varianz ist mit
    nur 1 Beobachtung nicht schaetzbar). 200 zufaellige Trials in den anderen
    Tests trafen nie zufaellig genau n_trades==1 -- deshalb hier gezielt
    konstruiert, fuer CPU (run_backtest) UND run_backtest_batch (Parity).
    """
    print("\nTest: Sharpe bei genau 1 Trade darf nicht explodieren ...")
    n = 30
    ts = pd.date_range('2023-01-01', periods=n, freq='1h', tz='UTC')
    close = np.full(n, 100.0)
    high = close * 1.001
    low = close * 0.999
    open_ = close.copy()
    feat_a = np.zeros(n)
    feat_a[12] = 1.0
    df = pd.DataFrame({
        'timestamp': ts, 'open': open_, 'high': high, 'low': low, 'close': close,
        'volume': np.full(n, 1e6), 'feat_a': feat_a,
    })
    df.loc[13, 'high'] = 110.0  # erzwingt TP-Treffer auf dem einzigen Trade

    entry_conditions = {'UP': {'must_have': [], 'should_have': [
        {'feature': 'feat_a', 'direction': 'above', 'baseline_avg': 0.0, 't_statistic': 5.0}]}}
    tradeable = [{'move_type': 'UP', 'direction': 'LONG'}]
    params = {
        't_threshold': 1.0, 'min_score': 1.0, 'min_hit_rate': 0.1,
        'sl_pct': 1.0, 'tp_rr': 2.0, 'leverage': 10, 'risk_per_trade_pct': 1.0,
        'max_hold_bars': 24, 'fee_pct_per_side': 0.06,
    }

    ref = run_backtest(df, entry_conditions, tradeable, params, start_capital=15.0)
    device_cpu, _ = resolve_device('cpu')
    batch = run_backtest_batch(df, entry_conditions, tradeable, [params],
                                start_capital=15.0, device=device_cpu)[0]

    print(f"  n_trades={ref['n_trades']}  ref_sharpe={ref['sharpe']}  batch_sharpe={batch['sharpe']}")
    assert ref['n_trades'] == 1, "Testaufbau soll genau 1 Trade erzeugen"
    assert ref['sharpe'] == 0.0, f"CPU: erwartet Sharpe=0.0 bei 1 Trade, bekam {ref['sharpe']}"
    assert batch['sharpe'] == 0.0, f"Batch: erwartet Sharpe=0.0 bei 1 Trade, bekam {batch['sharpe']}"
