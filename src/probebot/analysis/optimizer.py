"""
Probebot Optimizer — Optuna parameter optimization.

STRICT 70/30 RULE:
  This module ONLY ever sees df[:split_idx] (70% training data).
  The OOS period df[split_idx:] is never passed here.
  The caller in run.py enforces this by slicing before calling.

Usage (CLI):
    python -m probebot.analysis.optimizer \
        --symbol BTC/USDT:USDT --timeframe 1h \
        --bot_spec artifacts/db/bot_spec_BTC_USDT_USDT_1h.json \
        --data     artifacts/data/data_BTC_USDT_USDT_1h.parquet \
        --split_idx 24545 --trials 100
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(ROOT / 'src'))


def run_optimizer(
    symbol: str,
    timeframe: str,
    bot_spec_path: str,
    df_train: pd.DataFrame,       # ONLY the 70% training slice — enforced by caller
    n_trials: int = 100,
    start_capital: float = 100.0,
    max_drawdown: float = 30.0,
    min_trades: int = 20,
    min_win_rate: float = 35.0,
    mode: str = 'best_profit',    # 'strict' | 'best_profit'
    output_dir: str = None,
    force: bool = False,          # True = bereits vorhandene Config überschreiben
) -> str | None:
    """
    Optimize signal + risk parameters on the training slice.
    Writes config_SYMBOL_TF.json to output_dir.
    Returns path to written config, or None on failure.

    Overfitting-Schutz: Wenn Config bereits existiert und force=False,
    wird der Optimizer übersprungen (verhindert Trial-Akkumulation).
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        print("  [optimizer] ERROR: optuna nicht installiert. Bitte: pip install optuna")
        return None

    # ── Overfitting-Sperre: skip wenn Config schon existiert ──────────────────
    sym_safe    = symbol.replace('/', '_').replace(':', '_')
    _out_dir    = Path(output_dir) if output_dir else ROOT / 'src' / 'probebot' / 'strategy' / 'configs'
    config_path = _out_dir / f'config_{sym_safe}_{timeframe}.json'

    if config_path.exists() and not force:
        try:
            existing  = json.loads(config_path.read_text(encoding='utf-8'))
            old_meta  = existing.get('_meta', {})
            old_pnl   = old_meta.get('insample_pnl_pct', 0)
            old_trials= old_meta.get('n_trials_total', 0)
            old_date  = str(old_meta.get('optimized_at', '?'))[:10]
        except Exception:
            old_pnl, old_trials, old_date = 0, 0, '?'
        print(f"  [optimizer] ⚠️  Config existiert bereits!")
        print(f"  [optimizer]    Optimiert: {old_date}  |  Trials: {old_trials}  |  PnL: {old_pnl:+.1f}%")
        print(f"  [optimizer]    Überspringe — verhindert Overfitting durch Trial-Akkumulation.")
        print(f"  [optimizer]    Für Neuoptimierung: run_pipeline.sh → 'DB loeschen = j'")
        return str(config_path)

    from probebot.analysis.backtester import run_backtest

    # ── Load bot spec ──────────────────────────────────────────────────────────
    with open(bot_spec_path, encoding='utf-8') as f:
        bot_spec = json.load(f)

    entry_conditions  = bot_spec.get('entry_conditions', {})
    oos_validation    = bot_spec.get('oos_validation', {})
    selected_strategy = bot_spec.get('selected_strategy', {})
    strategy_name     = selected_strategy.get('strategy', 'HYBRID')

    # Tradeable move types — only ROBUST/STABIL from OOS validation
    tradeable = []
    for mtype, vr in oos_validation.items():
        use_in_bot = vr.get('use_in_bot', False)
        if use_in_bot:
            direction = 'LONG' if 'UP' in mtype else 'SHORT'
            tradeable.append({'move_type': mtype, 'direction': direction})

    if not tradeable:
        # Kein Fallback auf ungeprüfte Event-Counts — wenn nichts OOS-validiert
        # ist, gibt es (aktuell) keinen belastbaren Edge für dieses Symbol/TF.
        print("  [optimizer] Keine ROBUST/STABIL Typen mit n_train>=20 — kein Edge gefunden. Abbruch.")
        return None

    print(f"  [optimizer] Strategie: {strategy_name}  |  "
          f"Typen: {[t['move_type'] for t in tradeable]}")
    print(f"  [optimizer] Training-Kerzen: {len(df_train)}  |  "
          f"Trials: {n_trials}  |  Modus: {mode}")

    # ── Optuna study ──────────────────────────────────────────────────────────
    study_name = f"probebot_{sym_safe}_{timeframe}_{mode}"
    db_path    = ROOT / 'artifacts' / 'db' / 'optuna_probebot.db'
    db_path.parent.mkdir(parents=True, exist_ok=True)

    storage = optuna.storages.RDBStorage(
        url=f"sqlite:///{db_path}",
        engine_kwargs={'connect_args': {'timeout': 30}},
    )

    # Bei force=True: alte Study löschen damit Trial-Zähler bei 0 startet
    if force:
        try:
            optuna.delete_study(study_name=study_name, storage=storage)
            print(f"  [optimizer] Alte Optuna-Study gelöscht ({study_name})")
        except Exception:
            pass  # Study existierte nicht — kein Problem

    study = optuna.create_study(
        direction='maximize',
        study_name=study_name,
        storage=storage,
        load_if_exists=True,
    )

    completed_before = len([t for t in study.trials if t.state.name == 'COMPLETE'])
    counter = [0]

    def objective(trial):
        params = {
            't_threshold':        trial.suggest_float('t_threshold', 2.0, 8.0),
            'min_score':          trial.suggest_float('min_score', 5.0, 100.0),
            'min_hit_rate':       trial.suggest_float('min_hit_rate', 0.2, 0.85),
            'sl_pct':             trial.suggest_float('sl_pct', 0.5, 5.0),
            'tp_rr':              trial.suggest_float('tp_rr', 1.0, 5.0),
            'leverage':           trial.suggest_int('leverage', 3, 20),
            'risk_per_trade_pct': trial.suggest_float('risk_per_trade_pct', 0.5, 3.0),
            'max_hold_bars':      trial.suggest_int('max_hold_bars', 3, 96),
        }

        result = run_backtest(df_train, entry_conditions, tradeable, params, start_capital)

        if result['n_trades'] < min_trades:
            raise optuna.exceptions.TrialPruned()
        if result['max_drawdown'] > max_drawdown:
            raise optuna.exceptions.TrialPruned()
        if mode == 'strict' and result['win_rate'] < min_win_rate:
            raise optuna.exceptions.TrialPruned()

        counter[0] += 1
        done   = len([t for t in study.trials if t.state.name == 'COMPLETE'])
        pruned = len([t for t in study.trials if t.state.name == 'PRUNED'])
        try:
            best   = study.best_value
            best_s = f"{best:+.2f}%"
        except ValueError:
            best_s = "      —"
        pct    = counter[0] / n_trials * 100
        filled = int(40 * counter[0] / n_trials)
        bar    = '█' * filled + '░' * (40 - filled)
        print(f"\r  [{bar}] {pct:5.1f}%  {counter[0]:4d}/{n_trials}"
              f"  Best:{best_s}  ✓{done}  ✗{pruned}   ",
              end='', flush=True)

        return result['pnl_pct']

    print(f"  [{' ' * 40}]   0.0%     0/{n_trials}  Best:      —  ✓0  ✗0   ",
          end='', flush=True)
    interrupted = False
    try:
        study.optimize(objective, n_trials=n_trials)
    except KeyboardInterrupt:
        interrupted = True
    print()  # Zeilenumbruch nach fertigem Balken
    if interrupted:
        print(f"  Ctrl+C — verwende bestes bisheriges Ergebnis ({counter[0]} Trials)")

    completed = [t for t in study.trials if t.state.name == 'COMPLETE']
    if not completed:
        print("  [optimizer] Keine erfolgreichen Trials. Parameter zu streng?")
        print(f"  Tipp: --max_dd erhöhen oder --min_trades senken")
        return None

    best_params = study.best_trial.params
    best_result = run_backtest(df_train, entry_conditions, tradeable,
                               best_params, start_capital)

    print(f"\n  {'─'*50}")
    print(f"  Bestes Ergebnis (In-Sample, 70% Training-Daten):")
    print(f"  PnL:          {best_result['pnl_pct']:>+8.2f}%")
    print(f"  Trades:       {best_result['n_trades']:>8d}")
    print(f"  Win-Rate:     {best_result['win_rate']:>8.1f}%")
    print(f"  Max DD:       {best_result['max_drawdown']:>8.2f}%")
    print(f"  Sharpe:       {best_result['sharpe']:>8.3f}")
    print(f"  Profit-Fakt.: {best_result['profit_factor']:>8.2f}")
    print(f"  {'─'*50}")
    print(f"  Optimierte Parameter:")
    for k, v in best_params.items():
        print(f"    {k:<24} {v}")

    # ── Write config JSON ──────────────────────────────────────────────────────
    if output_dir is None:
        output_dir = str(ROOT / 'src' / 'probebot' / 'strategy' / 'configs')
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    meta   = bot_spec.get('meta', {})
    period = meta.get('period', {})

    config = {
        'market': {
            'symbol':    symbol,
            'timeframe': timeframe,
        },
        'strategy': {
            'type':            strategy_name,
            'tradeable_types': tradeable,
            'bot_spec_path':   str(bot_spec_path),
        },
        'signal': {
            't_threshold':   round(best_params['t_threshold'], 3),
            'min_score':     round(best_params['min_score'], 2),
            'min_hit_rate':  round(best_params['min_hit_rate'], 3),
            'max_hold_bars': int(best_params['max_hold_bars']),
        },
        'risk': {
            'sl_pct':             round(best_params['sl_pct'], 3),
            'tp_rr':              round(best_params['tp_rr'], 3),
            'leverage':           int(best_params['leverage']),
            'risk_per_trade_pct': round(best_params['risk_per_trade_pct'], 3),
            'start_capital':      start_capital,
        },
        'period': {
            'train_start': period.get('start', ''),
            'split_date':  meta.get('split_date', ''),
            'oos_end':     period.get('end', ''),
        },
        '_meta': {
            'insample_pnl_pct':   best_result['pnl_pct'],
            'insample_trades':    best_result['n_trades'],
            'insample_win_rate':  best_result['win_rate'],
            'insample_max_dd':    best_result['max_drawdown'],
            'insample_sharpe':    best_result['sharpe'],
            'optimized_at':       pd.Timestamp.now().isoformat(),
            'n_trials_total':     len(study.trials),
            'n_trials_completed': len(completed),
            'strategy_scores':    selected_strategy.get('type_scores', {}),
            'oos_period':         f"{meta.get('split_date', '?')} → {period.get('end', '?')}",
            'note':               'OOS (30%) never used during optimization',
        },
    }

    config_path = Path(output_dir) / f'config_{sym_safe}_{timeframe}.json'

    # Only overwrite if this run is strictly better
    if config_path.exists():
        try:
            old_pnl = json.loads(config_path.read_text()).get('_meta', {}).get('insample_pnl_pct', float('-inf'))
            if best_result['pnl_pct'] <= old_pnl:
                print(f"\n  Ergebnis ({best_result['pnl_pct']:.1f}%) nicht besser als "
                      f"bestehendes ({old_pnl:.1f}%) — Config NICHT überschrieben")
                return str(config_path)
        except Exception:
            pass

    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\n  Config gespeichert: {config_path.name}")
    print(f"  OOS-Test mit: bash show_results.sh → Mode 1")
    return str(config_path)


def main():
    parser = argparse.ArgumentParser(description='Probebot Optimizer (70% Training-Daten)')
    parser.add_argument('--symbol',     required=True)
    parser.add_argument('--timeframe',  required=True)
    parser.add_argument('--bot_spec',   required=True, help='Pfad zur bot_spec_*.json')
    parser.add_argument('--data',       required=True, help='Pfad zur data_*.parquet (alle Daten)')
    parser.add_argument('--split_idx',  type=int, required=True, help='Index des 70/30-Splits')
    parser.add_argument('--trials',     type=int, default=100)
    parser.add_argument('--capital',    type=float, default=100.0)
    parser.add_argument('--max_dd',     type=float, default=30.0)
    parser.add_argument('--min_trades', type=int,   default=20)
    parser.add_argument('--min_wr',     type=float, default=35.0)
    parser.add_argument('--mode',       default='best_profit', choices=['strict', 'best_profit'])
    parser.add_argument('--output',     default=None)
    parser.add_argument('--force',      action='store_true',
                        help='Bestehende Config + Optuna-Study löschen und neu optimieren')
    args = parser.parse_args()

    print(f"\nProbebot Optimizer")
    print(f"  Symbol:    {args.symbol} | TF: {args.timeframe}")
    print(f"  Modus:     {args.mode}")

    df = pd.read_parquet(args.data)
    # CRITICAL: slice to training period only
    df_train = df.iloc[:args.split_idx].copy()
    print(f"  Training:  {len(df_train)} Kerzen  "
          f"({str(df_train['timestamp'].iloc[0])[:10]} → "
          f"{str(df_train['timestamp'].iloc[-1])[:10]})")
    print(f"  OOS:       {len(df) - args.split_idx} Kerzen  "
          f"({str(df.iloc[args.split_idx]['timestamp'])[:10]} → "
          f"{str(df.iloc[-1]['timestamp'])[:10]})  ← NIE gesehen")

    run_optimizer(
        symbol=args.symbol,
        timeframe=args.timeframe,
        bot_spec_path=args.bot_spec,
        df_train=df_train,
        n_trials=args.trials,
        start_capital=args.capital,
        max_drawdown=args.max_dd,
        min_trades=args.min_trades,
        min_win_rate=args.min_wr,
        mode=args.mode,
        output_dir=args.output,
        force=args.force,
    )


if __name__ == '__main__':
    main()
