"""
portfolio_simulator.py — echte chronologische Multi-Symbol-Portfolio-Simulation.

Im Gegensatz zu backtester.run_backtest() (EIN Symbol, isoliertes Kapital, das
nie mit anderen Strategien konkurriert) simuliert dieses Modul mehrere Configs
GLEICHZEITIG ueber eine gemeinsame Zeitachse mit EINEM geteilten Kapitalpool —
genau wie es der echte Live-Bot tut:
  - master_runner.py begrenzt max_open_positions GEMEINSAM ueber alle aktiven
    Strategien (iteriert active_strategies in Listenreihenfolge, stoppt sobald
    das Limit erreicht ist).
  - trade_manager.py._execute_trade() sized jede Position aus dem ECHTEN,
    gemeinsamen Exchange-Kontostand (fetch_balance_usdt()) — Kapital das in
    einer offenen Position gebunden ist, steht anderen Strategien nicht mehr
    zur Verfuegung (Positionsgroesse wird bei Kapitalknappheit verkleinert,
    erst unter MIN_NOTIONAL_USDT abgelehnt).

Reiner Python-Event-Loop, KEINE Tensor-Vektorisierung — anderes Problem als
gpu_backtester.py (das fuer Optuna-Batch-Suche auf EINEM Symbol vektorisiert,
um Rechendurchsatz geht). Hier geht es um korrekte Kapital-/Slot-Konkurrenz
ueber wenige, bereits ausgewaehlte Symbole.

WICHTIG — abweichendes Kapitalmodell (bewusst, siehe Docstring unten):
backtester.run_backtest() bleibt UNVERAENDERT die Referenz fuer Modus 1/4 und
alle run_analysis.sh-Skripte (fixes Risiko vom STATISCHEN start_capital, kein
Compounding — Absicht, siehe Kommentar dort). Dieser Simulator compoundet
ECHT (Risiko % vom aktuell VERFUEGBAREN Kapital), weil das dem Live-Verhalten
entspricht. Die Kennzahlen beider Module sind daher NICHT direkt vergleichbar.
"""
from typing import Dict, List, Optional

from probebot.analysis.backtester import compute_signal_score, MAINTENANCE_MARGIN_PCT
from probebot.analysis._common import load_bot_spec, load_oos_data, load_settings
from probebot.utils.trade_manager import MIN_NOTIONAL_USDT

# trade_manager.py's echter Live-Margin-Faktor (max_by_margin = balance*leverage/price*0.99).
# NICHT backtester.py's MAX_MARGIN_FRACTION=0.9 (das ist fuer den isolierten
# Einzel-Backtest, ein anderes Modell — siehe Docstring oben).
LIVE_MARGIN_FRACTION = 0.99

# Absolute Obergrenze fuer die Positionsgroesse (wie dnabots
# run_portfolio_optimizer.py: MAX_NOTIONAL_USDT = 200_000.0). Ohne sie
# explodiert echtes Compounding (Risiko % vom aktuell verfuegbaren, nicht
# fixen Kapital) ueber viele profitable Trades hinweg exponentiell auf
# Groessenordnungen die keine reale Marktliquiditaet/kein Exchange je
# zulassen wuerde (beobachtet: +15 Mrd.% ueber 326 Trades ohne diese Grenze).
MAX_NOTIONAL_USDT = 200_000.0


class _Leg:
    """Eine Config, vorbereitet fuer die Simulation: eigene OOS-Zeilen +
    eigener Warmup-Index (native_idx, wie run_backtest()'s `i`), damit
    bars_held/max_hold_bars pro Symbol korrekt bleiben auch wenn Configs
    unterschiedliche Timeframes haben (z.B. TRX 4h neben anderen 1h)."""

    def __init__(self, config: Dict, move_conds: dict, move_dirs: dict, params: dict,
                 rows: List[dict], timestamps: list):
        sym = config['market']['symbol']
        tf  = config['market']['timeframe']
        self.key = f"{sym}_{tf}"
        self.symbol = sym
        self.coin = sym.split('/')[0]
        self.timeframe = tf
        self.config = config
        self.move_conds = move_conds
        self.move_dirs = move_dirs
        self.params = params
        self.rows = rows
        self.timestamps = timestamps
        self.native_idxs = list(range(10, 10 + len(rows)))
        self.ts_to_idx = {ts: i for i, ts in enumerate(timestamps)}


def _prepare_leg(config: Dict, start_date: str = None, end_date: str = None) -> Optional[_Leg]:
    """Laedt eine Config ueber die bestehenden _common.py-Helper (keine
    Duplizierung der Lade-Logik) und bereitet sie fuer die Simulation vor."""
    bot_spec = load_bot_spec(config)
    entry_conditions = bot_spec.get('entry_conditions', {})
    tradeable = config.get('strategy', {}).get('tradeable_types', [])
    params = {**config.get('signal', {}), **config.get('risk', {})}

    df_oos, _, _ = load_oos_data(config, start_date, end_date)
    if df_oos is None or len(df_oos) < 11:
        return None

    return _build_leg(config, df_oos, entry_conditions, tradeable, params)


def _build_leg(config: Dict, df_oos, entry_conditions: dict, tradeable: list, params: dict) -> Optional[_Leg]:
    """Baut ein _Leg aus bereits geladenen Daten — von _prepare_leg() (Datei-
    Pfad) UND von Tests (synthetische Daten) gemeinsam genutzt, damit beide
    exakt denselben Vorbereitungscode durchlaufen."""
    df_r = df_oos.reset_index(drop=True)
    if len(df_r) < 11:
        return None

    move_conds = {mt['move_type']: entry_conditions.get(mt['move_type'], {}) for mt in tradeable}
    move_dirs  = {mt['move_type']: mt['direction'] for mt in tradeable}

    sub = df_r.iloc[10:].reset_index(drop=True)
    rows = sub.to_dict('records')
    timestamps = list(sub['timestamp'])

    return _Leg(config, move_conds, move_dirs, params, rows, timestamps)


def _best_signal(row: dict, leg: _Leg) -> Optional[dict]:
    """Identische Logik zu run_backtest()'s 'Check for new signal'-Block —
    wiederverwendet compute_signal_score() direkt, dupliziert nur den kleinen
    'bestes Signal ueber alle Move-Types waehlen'-Wrapper."""
    t_threshold  = float(leg.params.get('t_threshold', 4.0))
    min_score    = float(leg.params.get('min_score', 20.0))
    min_hit_rate = float(leg.params.get('min_hit_rate', 0.4))

    best_score = 0.0
    best_mtype = None
    best_hit_rate = 0.0
    n_types_signaling = 0

    for mtype, conds in leg.move_conds.items():
        if not conds:
            continue
        score, n_met, n_total = compute_signal_score(row, conds, t_threshold)
        if n_total == 0:
            continue
        hit_rate = n_met / n_total
        if score >= min_score and hit_rate >= min_hit_rate:
            n_types_signaling += 1
            if score > best_score:
                best_score = score
                best_mtype = mtype
                best_hit_rate = hit_rate

    if best_mtype is None:
        return None
    return {
        'move_type': best_mtype, 'direction': leg.move_dirs[best_mtype],
        'score': best_score, 'hit_rate': best_hit_rate,
        'n_types_signaling': n_types_signaling,
    }


def _try_open_position(leg: _Leg, row: dict, signal: dict, available_equity: float,
                        native_idx: int) -> Optional[dict]:
    """Positionsgroesse/Margin-Cap/Notional-Grenze repliziert trade_manager.py's
    ECHTE Live-Regeln (Risiko % vom aktuell verfuegbaren Kapital, Position wird
    bei Kapitalknappheit verkleinert statt abgelehnt, Ablehnung erst unter
    MIN_NOTIONAL_USDT). Liquidations-/SL/TP-Preisformeln sind unveraendert aus
    run_backtest() uebernommen (echtes Boersenverhalten, unabhaengig vom
    Sizing-Modell)."""
    p = leg.params
    sl_pct   = float(p.get('sl_pct', 1.5))
    tp_rr    = float(p.get('tp_rr', 2.0))
    leverage = max(float(p.get('leverage', 10)), 1.0)
    risk_per_trade_pct = float(p.get('risk_per_trade_pct', 1.0))
    fee_pct_per_side   = float(p.get('fee_pct_per_side', 0.06))

    close = float(row.get('close', 0) or 0)
    if close <= 0 or sl_pct <= 0 or available_equity <= 0:
        return None

    risk_amt = available_equity * risk_per_trade_pct / 100
    sl_dist  = close * sl_pct / 100
    notional = risk_amt / (sl_pct / 100)

    # Zwei unabhaengige Obergrenzen, die kleinere gewinnt: (1) Margin-Cap aus
    # verfuegbarem Kapital*Hebel (trade_manager.py's echte Live-Regel), (2)
    # absolute MAX_NOTIONAL_USDT-Grenze (siehe Konstante oben — verhindert
    # exponentielle Explosion durch echtes Compounding).
    max_by_margin_notional = available_equity * leverage * LIVE_MARGIN_FRACTION
    notional = min(notional, max_by_margin_notional, MAX_NOTIONAL_USDT)
    margin_required = notional / leverage
    risk_amt = notional * sl_pct / 100

    if notional < MIN_NOTIONAL_USDT:
        return None

    liq_dist_pct = max(100.0 / leverage - MAINTENANCE_MARGIN_PCT, 0.05)
    liq_dist = close * liq_dist_pct / 100

    direction = signal['direction']
    if direction == 'LONG':
        sl = close - sl_dist
        tp = close + sl_dist * tp_rr
        liq_price = close - liq_dist
        effective_stop = max(sl, liq_price)
        is_liquidation = liq_price > sl
    else:
        sl = close + sl_dist
        tp = close - sl_dist * tp_rr
        liq_price = close + liq_dist
        effective_stop = min(sl, liq_price)
        is_liquidation = liq_price < sl

    fee_cost = notional * (fee_pct_per_side / 100) * 2

    return {
        'entry_native_idx': native_idx,
        'entry_ts': str(row['timestamp']),
        'entry_price': close, 'sl': sl, 'tp': tp, 'liq_price': liq_price,
        'leverage': leverage, 'effective_stop': effective_stop,
        'is_liquidation': is_liquidation, 'margin_required': margin_required,
        'direction': direction, 'risk_amount': risk_amt,
        'move_type': signal['move_type'], 'score': signal['score'],
        'hit_rate': signal['hit_rate'], 'n_types_signaling': signal['n_types_signaling'],
        'fee_cost': fee_cost, 'max_hold_bars': int(p.get('max_hold_bars', 24)),
        'sl_pct': sl_pct, 'tp_rr': tp_rr,
    }


def _close_position(leg: _Leg, pos: dict, close_ts: str, close_price: float,
                     pnl: float, close_reason: str, equity_after: float,
                     bars_held: int) -> dict:
    return {
        'entry_ts': pos['entry_ts'], 'close_ts': close_ts,
        'coin': leg.coin, 'symbol': leg.symbol, 'timeframe': leg.timeframe,
        'move_type': pos['move_type'], 'direction': pos['direction'],
        'entry_price': round(pos['entry_price'], 4), 'close_price': round(close_price, 4),
        'sl': round(pos['sl'], 4), 'tp': round(pos['tp'], 4),
        'liq_price': round(pos['liq_price'], 4), 'leverage': pos['leverage'],
        'score': round(pos['score'], 2), 'hit_rate': round(pos['hit_rate'], 3),
        'n_types_signaling': pos['n_types_signaling'],
        'pnl': round(pnl, 4), 'close_reason': close_reason,
        'capital_after': round(equity_after, 4),
        'portfolio_equity_after': round(equity_after, 4),
        'bars_held': bars_held, 'margin_required': round(pos['margin_required'], 4),
    }


def run_portfolio_simulation(configs: List[Dict], max_open_positions: int = None,
                              tie_break: str = 'config_order',
                              start_date: str = None, end_date: str = None) -> Optional[Dict]:
    """
    Echte chronologische Simulation ueber mehrere Configs mit EINEM geteilten
    Kapitalpool. configs-Reihenfolge = Prioritaet bei Slot-/Kapitalknappheit
    (tie_break='config_order', Standard) — entspricht exakt master_runner.py's
    Live-Verhalten (active_strategies-Listenreihenfolge). tie_break='score'
    sortiert gleichzeitige Signale stattdessen nach Signal-Staerke — eine
    bewusste, klar gekennzeichnete Abweichung vom Live-Verhalten.

    max_open_positions: Standard aus settings.json -> live_trading_settings
    .max_open_positions (wie master_runner.py). Kapital = Summe der
    risk.start_capital jeder Config (honoriert bereits vorhandene Kapital-
    Override-Prompts in show_results.py).
    """
    legs = [leg for cfg in configs if (leg := _prepare_leg(cfg, start_date, end_date)) is not None]
    if not legs:
        return None

    return _simulate(legs, max_open_positions, tie_break)


def _simulate(legs: List[_Leg], max_open_positions: int = None,
              tie_break: str = 'config_order') -> Dict:
    if max_open_positions is None:
        settings = load_settings()
        max_open_positions = int(settings.get('live_trading_settings', {}).get('max_open_positions', 5))

    start_capital = sum(l.config.get('risk', {}).get('start_capital', 100.0) for l in legs)
    legs_by_key = {l.key: l for l in legs}

    all_ts = sorted(set().union(*(set(l.timestamps) for l in legs)))

    equity = start_capital
    open_positions: Dict[str, dict] = {}
    trades: List[dict] = []
    equity_curve: List[dict] = []
    peak_realized = equity
    peak_mtm = equity
    max_dd_realized = 0.0
    max_dd_mtm = 0.0
    n_skipped_slot = 0
    n_skipped_capital = 0

    for ts in all_ts:
        # ── A) offene Positionen verwalten ───────────────────────────────────
        for key in list(open_positions.keys()):
            leg = legs_by_key[key]
            idx = leg.ts_to_idx.get(ts)
            if idx is None:
                continue
            row = leg.rows[idx]
            native_idx = leg.native_idxs[idx]
            pos = open_positions[key]

            close = float(row.get('close', 0) or 0)
            high  = float(row.get('high', close) or close)
            low   = float(row.get('low', close) or close)
            if close <= 0:
                continue

            direction = pos['direction']
            bars_held = native_idx - pos['entry_native_idx']
            closed = False
            close_reason = ''
            pnl = 0.0

            if direction == 'LONG':
                if low <= pos['effective_stop']:
                    if pos['is_liquidation']:
                        pnl = -pos['margin_required']; closed, close_reason = True, 'LIQ'
                    else:
                        pnl = -pos['risk_amount']; closed, close_reason = True, 'SL'
                elif high >= pos['tp']:
                    pnl = pos['risk_amount'] * pos['tp_rr']; closed, close_reason = True, 'TP'
            else:
                if high >= pos['effective_stop']:
                    if pos['is_liquidation']:
                        pnl = -pos['margin_required']; closed, close_reason = True, 'LIQ'
                    else:
                        pnl = -pos['risk_amount']; closed, close_reason = True, 'SL'
                elif low <= pos['tp']:
                    pnl = pos['risk_amount'] * pos['tp_rr']; closed, close_reason = True, 'TP'

            if not closed and bars_held >= pos['max_hold_bars']:
                sl_dist = pos['entry_price'] * pos['sl_pct'] / 100
                if sl_dist == 0:
                    pnl = 0.0
                elif direction == 'LONG':
                    pnl = (close - pos['entry_price']) / sl_dist * pos['risk_amount']
                else:
                    pnl = (pos['entry_price'] - close) / sl_dist * pos['risk_amount']
                closed, close_reason = True, 'TIMEOUT'

            pos['last_price'] = close

            if closed:
                pnl -= pos['fee_cost']
                equity += pnl
                trades.append(_close_position(leg, pos, str(row['timestamp']), close,
                                               pnl, close_reason, equity, bars_held))
                del open_positions[key]

        # ── B) Mark-to-Market + Drawdown-Tracking ────────────────────────────
        unrealized = 0.0
        for key, pos in open_positions.items():
            last_price = pos.get('last_price', pos['entry_price'])
            mult = 1 if pos['direction'] == 'LONG' else -1
            sl_dist = pos['entry_price'] * pos['sl_pct'] / 100
            if sl_dist > 0:
                unrealized += (last_price - pos['entry_price']) * mult / sl_dist * pos['risk_amount']

        peak_realized = max(peak_realized, equity)
        if peak_realized > 0:
            max_dd_realized = max(max_dd_realized, (peak_realized - equity) / peak_realized * 100)

        mtm_equity = equity + unrealized
        peak_mtm = max(peak_mtm, mtm_equity)
        if peak_mtm > 0:
            max_dd_mtm = max(max_dd_mtm, (peak_mtm - mtm_equity) / peak_mtm * 100)

        equity_curve.append({'timestamp': str(ts), 'equity_realized': round(equity, 4),
                              'equity_mtm': round(mtm_equity, 4), 'n_open': len(open_positions)})

        # ── C) neue Positionen eroeffnen ──────────────────────────────────────
        if len(open_positions) < max_open_positions:
            candidates = []
            for leg in legs:
                if leg.key in open_positions:
                    continue
                idx = leg.ts_to_idx.get(ts)
                if idx is None:
                    continue
                sig = _best_signal(leg.rows[idx], leg)
                if sig:
                    candidates.append((leg, idx, sig))

            if tie_break == 'score':
                candidates.sort(key=lambda c: c[2]['score'], reverse=True)
            # sonst ('config_order'): Reihenfolge der `legs`-Liste bleibt erhalten

            for leg, idx, sig in candidates:
                if len(open_positions) >= max_open_positions:
                    n_skipped_slot += 1
                    continue
                locked_margin = sum(p['margin_required'] for p in open_positions.values())
                available_equity = equity - locked_margin
                if available_equity < MIN_NOTIONAL_USDT:
                    n_skipped_capital += 1
                    continue
                pos = _try_open_position(leg, leg.rows[idx], sig, available_equity, leg.native_idxs[idx])
                if pos is None:
                    n_skipped_capital += 1
                    continue
                open_positions[leg.key] = pos

    # ── Verbleibende offene Positionen am jeweils letzten Bar der Leg zwangsschliessen ──
    for key, pos in list(open_positions.items()):
        leg = legs_by_key[key]
        last_row = leg.rows[-1]
        last_price = float(last_row.get('close', pos['entry_price']) or pos['entry_price'])
        direction = pos['direction']
        sl_dist = pos['entry_price'] * pos['sl_pct'] / 100
        if sl_dist == 0:
            pnl = 0.0
        elif direction == 'LONG':
            pnl = (last_price - pos['entry_price']) / sl_dist * pos['risk_amount']
        else:
            pnl = (pos['entry_price'] - last_price) / sl_dist * pos['risk_amount']
        pnl -= pos['fee_cost']
        equity += pnl
        bars_held = leg.native_idxs[-1] - pos['entry_native_idx']
        trades.append(_close_position(leg, pos, str(last_row['timestamp']), last_price,
                                       pnl, 'END', equity, bars_held))

    trades.sort(key=lambda t: t['entry_ts'])

    n_trades = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    win_rate = len(wins) / n_trades * 100 if n_trades else 0.0
    pnl_pct = (equity - start_capital) / start_capital * 100 if start_capital else 0.0

    return {
        'configs':          [l.config for l in legs],
        'start_capital':    round(start_capital, 4),
        'end_capital':      round(equity, 4),
        'pnl_pct':          round(pnl_pct, 2),
        'n_trades':         n_trades,
        'win_rate':         round(win_rate, 1),
        'max_drawdown':               round(max_dd_mtm, 2),
        'max_drawdown_realized_only': round(max_dd_realized, 2),
        'n_signals_skipped_slot_limit': n_skipped_slot,
        'n_signals_skipped_capital':    n_skipped_capital,
        'max_open_positions': max_open_positions,
        'tie_break':          tie_break,
        'trades':             trades,
        'equity_curve':        equity_curve,
    }
