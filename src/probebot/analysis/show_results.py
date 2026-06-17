"""
Probebot Show-Results — Trading Bot Evaluation Modes 1-4

Mode 1: OOS-Backtest (30% Test-Periode) aller Configs → Tabelle
Mode 2: Portfolio-Simulation (mehrere Configs zusammen)
Mode 3: Auto-Portfolio-Optimizer (greedy, DD-Constraint)
Mode 4: Equity-Kurven Chart (lokal + Telegram)

STRICT 70/30: All modes only use df[split_idx:] (the 30% OOS period).
The training data df[:split_idx] is NEVER used here.
"""
import json
import sys
from pathlib import Path
from typing import List, Dict

ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(ROOT / 'src'))

G  = '\033[0;32m'
Y  = '\033[1;33m'
C  = '\033[0;36m'
R  = '\033[0;31m'
B  = '\033[0;34m'
NC = '\033[0m'


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_configs() -> List[Dict]:
    """Load all config_*.json from strategy/configs/"""
    config_dir = ROOT / 'src' / 'probebot' / 'strategy' / 'configs'
    if not config_dir.exists():
        return []
    configs = []
    for p in sorted(config_dir.glob('config_*.json')):
        try:
            with open(p, encoding='utf-8') as f:
                c = json.load(f)
            c['_config_path'] = str(p)
            c['_config_name'] = p.stem
            configs.append(c)
        except Exception as e:
            print(f"  {R}Fehler beim Laden {p.name}: {e}{NC}")
    return configs


def _load_oos_data(config: Dict):
    """
    Load the full feature df from parquet and slice to OOS period (30%).
    Returns df_oos, split_idx, or (None, None) on error.
    """
    import pandas as pd
    sym   = config['market']['symbol'].replace('/', '_').replace(':', '_')
    tf    = config['market']['timeframe']
    split_date = config.get('period', {}).get('split_date', '')

    data_path = ROOT / 'artifacts' / 'data' / f'data_{sym}_{tf}.parquet'
    if not data_path.exists():
        print(f"  {R}Daten nicht gefunden: {data_path.name}{NC}")
        print(f"  Erst run_pipeline.sh ausführen.")
        return None, None

    df = pd.read_parquet(data_path)

    # Find split index from split_date stored in config
    if not split_date:
        split_idx = int(len(df) * 0.70)
    else:
        mask = df['timestamp'].astype(str).str.startswith(split_date[:10])
        idxs = df[mask].index
        split_idx = int(idxs[0]) if len(idxs) > 0 else int(len(df) * 0.70)

    df_oos = df.iloc[split_idx:].copy().reset_index(drop=True)
    return df_oos, split_idx


def _run_oos_backtest(config: Dict) -> Dict | None:
    """Run backtest on the 30% OOS slice for one config."""
    from probebot.analysis.backtester import run_backtest

    bot_spec_path = config.get('strategy', {}).get('bot_spec_path', '')
    if not Path(bot_spec_path).exists():
        # Try relative path from ROOT
        sym = config['market']['symbol'].replace('/', '_').replace(':', '_')
        tf  = config['market']['timeframe']
        bot_spec_path = str(ROOT / 'artifacts' / 'db' / f'bot_spec_{sym}_{tf}.json')

    if not Path(bot_spec_path).exists():
        print(f"  {R}bot_spec nicht gefunden: {bot_spec_path}{NC}")
        return None

    with open(bot_spec_path, encoding='utf-8') as f:
        bot_spec = json.load(f)

    entry_conditions = bot_spec.get('entry_conditions', {})
    tradeable        = config['strategy']['tradeable_types']
    params           = {**config.get('signal', {}), **config.get('risk', {})}
    start_capital    = config.get('risk', {}).get('start_capital', 100.0)

    df_oos, split_idx = _load_oos_data(config)
    if df_oos is None:
        return None

    result = run_backtest(df_oos, entry_conditions, tradeable, params, start_capital)
    result['config'] = config
    result['split_date'] = config.get('period', {}).get('split_date', '?')
    result['oos_end']    = config.get('period', {}).get('oos_end', '?')
    return result


# ─── Mode 1: OOS-Backtest Tabelle ────────────────────────────────────────────

def mode_1_oos_table():
    configs = _load_configs()
    if not configs:
        print(f"\n  {R}Keine Configs gefunden.{NC}")
        print(f"  Erst run_pipeline.sh → Optimizer ausführen.")
        return

    print(f"\n{Y}{'─'*90}{NC}")
    print(f"{Y}  OOS-BACKTEST (30% Test-Periode — nie gesehen während Optimierung){NC}")
    print(f"{Y}{'─'*90}{NC}")
    print(f"  {'Config':<28}  {'Strategie':<10}  {'Trades':>6}  {'WinRate':>7}  "
          f"{'PnL%':>7}  {'MaxDD%':>7}  {'Sharpe':>7}  {'OOS-Periode'}")
    print(f"  {'─'*28}  {'─'*10}  {'─'*6}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*20}")

    results = []
    for cfg in configs:
        sym  = cfg['market']['symbol']
        tf   = cfg['market']['timeframe']
        name = f"{sym.split('/')[0]} {tf}"
        strat = cfg.get('strategy', {}).get('type', '?')
        print(f"  Berechne {name}...", end='\r', flush=True)
        res = _run_oos_backtest(cfg)
        if res is None:
            print(f"  {R}{name:<28}  FEHLER{NC}")
            continue
        results.append((name, strat, res))

    if not results:
        print(f"\n  {R}Keine Ergebnisse.{NC}")
        return

    results.sort(key=lambda x: x[2]['pnl_pct'], reverse=True)

    print()
    for name, strat, res in results:
        pnl_c = G if res['pnl_pct'] >= 0 else R
        dd_c  = R if res['max_drawdown'] > 20 else Y if res['max_drawdown'] > 10 else G
        wr_c  = G if res['win_rate'] >= 50 else Y if res['win_rate'] >= 40 else R
        period = f"{res['split_date'][:7]} → {res['oos_end'][:7]}"
        print(f"  {name:<28}  {strat:<10}  "
              f"{res['n_trades']:>6d}  "
              f"{wr_c}{res['win_rate']:>6.1f}%{NC}  "
              f"{pnl_c}{res['pnl_pct']:>+6.1f}%{NC}  "
              f"{dd_c}{res['max_drawdown']:>6.1f}%{NC}  "
              f"{res['sharpe']:>7.3f}  "
              f"{period}")

    print(f"\n{Y}{'─'*90}{NC}")
    print(f"  Hinweis: Diese Ergebnisse basieren auf Daten die der Optimizer NIE gesehen hat.")

    # Show detailed trades if user wants
    inp = input(f"\n  Trades einer Config anzeigen? (Name oder Enter zum Überspringen): ").strip()
    if inp:
        for name, strat, res in results:
            if inp.lower() in name.lower():
                _print_trade_detail(res['config'], res.get('trades', []))
                break


def _print_trade_detail(config: Dict, trades: List[Dict]):
    print(f"\n  {C}Trades Detail:{NC}")
    print(f"  {'Datum':<12}  {'Typ':<22}  {'Dir':>5}  {'PnL':>8}  {'Grund':<8}  {'Bars':>5}")
    print(f"  {'─'*12}  {'─'*22}  {'─'*5}  {'─'*8}  {'─'*8}  {'─'*5}")
    for t in trades:
        pnl_c = G if t['pnl'] > 0 else R
        print(f"  {str(t['entry_ts'])[:10]:<12}  "
              f"{t['move_type']:<22}  "
              f"{t['direction']:>5}  "
              f"{pnl_c}{t['pnl']:>+7.2f}{NC}  "
              f"{t['close_reason']:<8}  "
              f"{t['bars_held']:>5}")


# ─── Mode 2: Portfolio-Simulation ────────────────────────────────────────────

def mode_2_portfolio():
    configs = _load_configs()
    if not configs:
        print(f"\n  {R}Keine Configs.{NC}")
        return

    print(f"\n{Y}  Verfügbare Configs:{NC}")
    for i, cfg in enumerate(configs, 1):
        sym  = cfg['market']['symbol'].split('/')[0]
        tf   = cfg['market']['timeframe']
        strat = cfg.get('strategy', {}).get('type', '?')
        pnl   = cfg.get('_meta', {}).get('insample_pnl_pct', 0)
        print(f"  {i:2d}) {sym:<6} {tf:<4}  {strat:<10}  In-Sample: {pnl:+.1f}%")

    inp = input(f"\n  Welche kombinieren? (Nummern kommagetrennt, z.B. 1,3,5): ").strip()
    if not inp:
        return

    chosen_idxs = []
    for s in inp.split(','):
        try:
            idx = int(s.strip()) - 1
            if 0 <= idx < len(configs):
                chosen_idxs.append(idx)
        except ValueError:
            pass

    if not chosen_idxs:
        print(f"  {R}Ungültige Eingabe.{NC}")
        return

    chosen = [configs[i] for i in chosen_idxs]
    print(f"\n  Berechne OOS-Backtest für {len(chosen)} Configs...")

    all_results = []
    for cfg in chosen:
        res = _run_oos_backtest(cfg)
        if res:
            all_results.append(res)

    if not all_results:
        return

    # Combine equity curves (proportional to start_capital)
    _print_portfolio_summary(all_results)


def _print_portfolio_summary(results: List[Dict]):
    """Print combined portfolio stats."""
    total_start = sum(r['config'].get('risk', {}).get('start_capital', 100) for r in results)
    total_end   = sum(r['end_capital'] for r in results)
    combined_pnl = (total_end - total_start) / total_start * 100

    all_trades = []
    for r in results:
        for t in r.get('trades', []):
            all_trades.append(t)
    all_trades.sort(key=lambda t: t['entry_ts'])

    total_trades = sum(r['n_trades'] for r in results)
    wins = sum(1 for r in results for t in r.get('trades', []) if t['pnl'] > 0)
    wr   = wins / total_trades * 100 if total_trades > 0 else 0

    print(f"\n{Y}{'─'*60}{NC}")
    print(f"  PORTFOLIO (OOS 30%):")
    print(f"  Strategies:     {len(results)}")
    print(f"  Total Trades:   {total_trades}")
    print(f"  Win-Rate:       {wr:.1f}%")
    print(f"  Combined PnL:   {G if combined_pnl >= 0 else R}{combined_pnl:+.2f}%{NC}")
    print(f"  Einzel-PnL:")
    for r in results:
        sym = r['config']['market']['symbol'].split('/')[0]
        tf  = r['config']['market']['timeframe']
        pnl_c = G if r['pnl_pct'] >= 0 else R
        print(f"    {sym} {tf:<4}  {pnl_c}{r['pnl_pct']:+.1f}%{NC}  "
              f"({r['n_trades']} Trades, DD:{r['max_drawdown']:.1f}%)")
    print(f"{Y}{'─'*60}{NC}")


# ─── Mode 3: Auto-Portfolio-Optimizer ────────────────────────────────────────

def mode_3_auto_portfolio():
    """Greedy portfolio selection under drawdown constraint."""
    configs = _load_configs()
    if not configs:
        print(f"\n  {R}Keine Configs.{NC}")
        return

    try:
        max_dd = float(input(f"  Max. Portfolio-Drawdown % [Standard: 20]: ").strip() or "20")
    except ValueError:
        max_dd = 20.0

    print(f"\n  Berechne OOS-Backtest für alle {len(configs)} Configs...")
    all_results = []
    for cfg in configs:
        res = _run_oos_backtest(cfg)
        if res and res['n_trades'] >= 5:
            all_results.append(res)

    if not all_results:
        print(f"  {R}Keine verwertbaren Ergebnisse.{NC}")
        return

    # Sort by PnL descending
    all_results.sort(key=lambda r: r['pnl_pct'], reverse=True)

    # Greedy: add strategies while combined DD stays under limit
    portfolio = []
    for res in all_results:
        if res['max_drawdown'] > max_dd:
            continue  # individual DD already too high
        trial = portfolio + [res]
        combined_dd = _estimate_combined_dd(trial)
        if combined_dd <= max_dd:
            portfolio.append(res)

    if not portfolio:
        print(f"\n  {R}Kein Portfolio gefunden das DD ≤ {max_dd}% einhält.{NC}")
        print(f"  Tipp: max_dd erhöhen oder mehr Configs optimieren.")
        return

    print(f"\n{G}  Portfolio gefunden: {len(portfolio)} Strategien{NC}")
    _print_portfolio_summary(portfolio)

    # Optionally write to settings.json
    inp = input(f"\n  Portfolio in settings.json speichern? (j/n): ").strip().lower()
    if inp in ('j', 'y', 'ja', 'yes'):
        _write_portfolio_to_settings(portfolio)


def _estimate_combined_dd(results: List[Dict]) -> float:
    """Simplified combined DD estimate: average of individual DDs."""
    if not results:
        return 0.0
    return sum(r['max_drawdown'] for r in results) / len(results) * 0.7


def _write_portfolio_to_settings(results: List[Dict]):
    settings_path = ROOT / 'settings.json'
    try:
        with open(settings_path, encoding='utf-8') as f:
            settings = json.load(f)
    except Exception:
        settings = {}

    active = []
    for res in results:
        cfg = res['config']
        active.append({
            'symbol':    cfg['market']['symbol'],
            'timeframe': cfg['market']['timeframe'],
            'strategy':  cfg.get('strategy', {}).get('type', 'HYBRID'),
            'active':    True,
        })

    # Write into live_trading_settings.active_strategies — that's where master_runner reads from
    if 'live_trading_settings' not in settings:
        settings['live_trading_settings'] = {}
    settings['live_trading_settings']['active_strategies'] = active
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"  {G}settings.json aktualisiert — {len(active)} aktive Strategien.{NC}")


# ─── Mode 4: Equity Chart ────────────────────────────────────────────────────

def mode_4_charts():
    """Generate equity curve charts for OOS results."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print(f"  {R}matplotlib nicht installiert.{NC}")
        return

    configs = _load_configs()
    if not configs:
        print(f"\n  {R}Keine Configs.{NC}")
        return

    print(f"  Erstelle Equity-Charts für {len(configs)} Configs...")
    charts_dir = ROOT / 'artifacts' / 'charts'
    charts_dir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(len(configs), 1,
                             figsize=(14, 4 * max(len(configs), 1)),
                             facecolor='#0d1117')

    if len(configs) == 1:
        axes = [axes]

    saved = []
    for ax, cfg in zip(axes, configs):
        res = _run_oos_backtest(cfg)
        sym = cfg['market']['symbol'].split('/')[0]
        tf  = cfg['market']['timeframe']
        ax.set_facecolor('#0d1117')
        ax.tick_params(colors='#8b949e')
        for spine in ax.spines.values():
            spine.set_color('#30363d')

        if res and res['trades']:
            capitals = [cfg.get('risk', {}).get('start_capital', 100)]
            dates    = [res['trades'][0]['entry_ts'][:10]]
            for t in res['trades']:
                capitals.append(t['capital_after'])
                dates.append(t['close_ts'][:10])

            color = '#26a69a' if capitals[-1] >= capitals[0] else '#ef5350'
            ax.plot(range(len(capitals)), capitals, color=color, linewidth=1.5)
            ax.fill_between(range(len(capitals)), capitals,
                            capitals[0], alpha=0.15, color=color)
            pnl_pct = res['pnl_pct']
            ax.set_title(
                f"{sym} {tf}  OOS PnL: {pnl_pct:+.1f}%  "
                f"({res['n_trades']} Trades, WR:{res['win_rate']:.0f}%, DD:{res['max_drawdown']:.1f}%)",
                color='#e6edf3', fontsize=10,
            )
        else:
            ax.set_title(f"{sym} {tf}  — Keine OOS-Trades", color='#8b949e', fontsize=10)
        ax.grid(True, color='#21262d', linewidth=0.5)

    plt.tight_layout(pad=2.0)
    out = charts_dir / 'oos_equity_curves.png'
    plt.savefig(str(out), dpi=150, bbox_inches='tight',
                facecolor='#0d1117', edgecolor='none')
    plt.close()
    print(f"  {G}Chart gespeichert: {out}{NC}")
    saved.append(str(out))

    # Telegram
    try:
        from probebot.utils.telegram import load_telegram_config, send_photo
        secret_path = ROOT.parent / 'secret.json'
        if not secret_path.exists():
            secret_path = ROOT / 'secret.json'
        tg = load_telegram_config(str(secret_path))
        if tg.get('bot_token'):
            inp = input("  Per Telegram senden? (j/n): ").strip().lower()
            if inp in ('j', 'y', 'ja'):
                send_photo(tg['bot_token'], tg['chat_id'], saved[0],
                           f"📊 Probebot OOS Equity Curves\n{len(configs)} Strategien")
                print(f"  {G}Telegram gesendet.{NC}")
    except Exception:
        pass


# ─── Main entry point ────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=int, default=None)
    args = parser.parse_args()

    if args.mode is None:
        print(f"\n{B}{'='*60}{NC}")
        print(f"       probebot — Trading Bot Evaluation")
        print(f"{B}{'='*60}{NC}")
        print(f"\n{Y}Modus wählen:{NC}")
        print(f"  {G}1{NC}) OOS-Backtest (30% Test-Periode) aller Configs")
        print(f"  {G}2{NC}) Portfolio-Simulation (mehrere Configs kombinieren)")
        print(f"  {G}3{NC}) Auto-Portfolio-Optimizer (greedy, DD-Constraint)")
        print(f"  {G}4{NC}) Equity-Chart erstellen + Telegram")
        mode = input(f"\nAuswahl (1-4) [Standard: 1]: ").strip() or "1"
    else:
        mode = str(args.mode)

    mode = mode.strip()
    if mode == '1':
        mode_1_oos_table()
    elif mode == '2':
        mode_2_portfolio()
    elif mode == '3':
        mode_3_auto_portfolio()
    elif mode == '4':
        mode_4_charts()
    else:
        print(f"  {R}Ungültiger Modus: {mode}{NC}")


if __name__ == '__main__':
    main()
