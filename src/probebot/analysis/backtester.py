"""
Backtester for probebot signal engine.

Signal logic:
  For each candle, compute a readiness score per tradeable movement type.
  Score = sum of |t_statistic| for features whose direction matches their
  global baseline comparison (above/below mean_all from correlations).
  Enter when score >= min_score AND fraction of conditions met >= min_hit_rate.

CRITICAL: The caller must pass only the allowed data slice.
          This module has no knowledge of the 70/30 split.
"""
import numpy as np
import pandas as pd
from typing import Dict, List


def compute_signal_score(row: dict, entry_conditions: dict,
                         t_threshold: float = 4.0) -> tuple:
    """
    Compute readiness score for a single movement type.

    Returns:
        score (float):    sum of |t| for met conditions
        n_met (int):      number of conditions satisfied
        n_total (int):    number of eligible conditions (|t| >= threshold)
    """
    conditions = (
        entry_conditions.get('must_have', []) +
        entry_conditions.get('should_have', [])
    )
    score   = 0.0
    n_met   = 0
    n_total = 0

    for cond in conditions:
        t = cond.get('t_statistic', 0)
        if abs(t) < t_threshold:
            continue
        feat      = cond['feature']
        direction = cond['direction']     # 'above' or 'below'
        baseline  = cond.get('baseline_avg', 0)
        val       = row.get(feat)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            continue
        n_total += 1
        met = (direction == 'above' and val > baseline) or \
              (direction == 'below' and val < baseline)
        if met:
            score += abs(t)
            n_met += 1

    return score, n_met, n_total


def run_backtest(
    df: pd.DataFrame,
    entry_conditions: Dict,
    tradeable_move_types: List[Dict],
    params: Dict,
    start_capital: float = 100.0,
) -> Dict:
    """
    Simulate trades using forensics-derived entry conditions.

    params:
        t_threshold (float):        min |t| for feature to count      [default 4.0]
        min_score (float):          minimum signal score to enter      [default 20.0]
        min_hit_rate (float):       minimum fraction of conds met      [default 0.4]
        sl_pct (float):             stop loss %                        [default 1.5]
        tp_rr (float):              take profit R:R ratio              [default 2.0]
        leverage (int):             futures leverage                   [default 10]
        risk_per_trade_pct (float): % of start capital at risk (fixed, no compounding within the backtest) [default 1.0]
        max_hold_bars (int):        max bars before forced close       [default 24]
        fee_pct_per_side (float):   taker fee % per fill (Bitget ~0.06) [default 0.06]

    Returns dict with: n_trades, win_rate, pnl_pct, max_drawdown,
                       sharpe, profit_factor, end_capital, trades
    """
    t_threshold        = float(params.get('t_threshold', 4.0))
    min_score          = float(params.get('min_score', 20.0))
    min_hit_rate       = float(params.get('min_hit_rate', 0.4))
    sl_pct             = float(params.get('sl_pct', 1.5))
    tp_rr              = float(params.get('tp_rr', 2.0))
    risk_per_trade_pct = float(params.get('risk_per_trade_pct', 1.0))
    max_hold_bars      = int(params.get('max_hold_bars', 24))
    fee_pct_per_side   = float(params.get('fee_pct_per_side', 0.06))

    # Pre-index entry conditions and directions
    move_conds = {
        mt['move_type']: entry_conditions.get(mt['move_type'], {})
        for mt in tradeable_move_types
    }
    move_dirs = {mt['move_type']: mt['direction'] for mt in tradeable_move_types}

    df_r = df.reset_index(drop=True)
    n    = len(df_r)

    capital     = start_capital
    peak        = start_capital
    max_dd      = 0.0
    trades      = []
    open_pos    = None  # dict when a position is open

    for i in range(10, n):
        row   = df_r.iloc[i].to_dict()
        close = float(row.get('close', 0) or 0)
        high  = float(row.get('high',  close) or close)
        low   = float(row.get('low',   close) or close)
        if close <= 0:
            continue

        # ── Manage open position ──────────────────────────────────────────────
        if open_pos is not None:
            direction   = open_pos['direction']
            entry_price = open_pos['entry_price']
            sl          = open_pos['sl']
            tp          = open_pos['tp']
            risk_amt    = open_pos['risk_amount']
            bars_held   = i - open_pos['entry_idx']

            closed       = False
            close_reason = ''
            pnl          = 0.0

            if direction == 'LONG':
                if low <= sl:
                    pnl = -risk_amt
                    closed, close_reason = True, 'SL'
                elif high >= tp:
                    pnl = risk_amt * tp_rr
                    closed, close_reason = True, 'TP'
            else:
                if high >= sl:
                    pnl = -risk_amt
                    closed, close_reason = True, 'SL'
                elif low <= tp:
                    pnl = risk_amt * tp_rr
                    closed, close_reason = True, 'TP'

            if not closed and bars_held >= max_hold_bars:
                sl_dist = entry_price * sl_pct / 100
                if direction == 'LONG':
                    pnl = (close - entry_price) / sl_dist * risk_amt
                else:
                    pnl = (entry_price - close) / sl_dist * risk_amt
                closed, close_reason = True, 'TIMEOUT'

            if closed:
                pnl     -= open_pos['fee_cost']
                capital += pnl
                peak     = max(peak, capital)
                dd       = (peak - capital) / peak * 100
                max_dd   = max(max_dd, dd)
                trades.append({
                    'entry_ts':     str(df_r.iloc[open_pos['entry_idx']].get('timestamp', '')),
                    'close_ts':     str(row.get('timestamp', '')),
                    'move_type':    open_pos['move_type'],
                    'direction':    direction,
                    'entry_price':  round(entry_price, 4),
                    'close_price':  round(close, 4),
                    'sl':           round(open_pos['sl'], 4),
                    'tp':           round(open_pos['tp'], 4),
                    'score':        round(open_pos['score'], 2),
                    'hit_rate':     round(open_pos.get('hit_rate', 0.0), 3),
                    'n_types_signaling': open_pos.get('n_types_signaling', 1),
                    'pnl':          round(pnl, 4),
                    'pnl_pct':      round(pnl / start_capital * 100, 3),
                    'close_reason': close_reason,
                    'capital_after': round(capital, 4),
                    'bars_held':    bars_held,
                })
                open_pos = None

        # ── Check for new signal ──────────────────────────────────────────────
        if open_pos is None:
            best_score = 0.0
            best_mtype = None
            best_hit_rate = 0.0
            n_types_signaling = 0

            for mtype, conds in move_conds.items():
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

            if best_mtype is not None:
                direction = move_dirs[best_mtype]
                # Fixe Positionsgröße (% vom Start-Kapital, nicht vom laufenden
                # Kapital) — verhindert exponentielles Compounding im Backtest/
                # Optimizer bei vielen Trades. Der Live-Bot compoundet echt
                # (balance-basiert); das ist hier bewusst nur für die Metrik
                # anders, damit pnl_pct interpretierbar bleibt und der Optimizer
                # nicht blind auf die schnellste Explosionsrate optimiert.
                risk_amt  = start_capital * risk_per_trade_pct / 100
                sl_dist   = close * sl_pct / 100

                if direction == 'LONG':
                    sl = close - sl_dist
                    tp = close + sl_dist * tp_rr
                else:
                    sl = close + sl_dist
                    tp = close - sl_dist * tp_rr

                # Notional aus Risiko + SL-Abstand ableiten (kein explizites
                # Contracts-Tracking in diesem R-Multiple-Backtest), Fees
                # (Entry + Exit) daraus vorab bestimmen.
                notional = risk_amt / (sl_pct / 100) if sl_pct > 0 else 0.0
                fee_cost = notional * (fee_pct_per_side / 100) * 2

                open_pos = {
                    'entry_idx':   i,
                    'entry_price': close,
                    'sl':          sl,
                    'tp':          tp,
                    'direction':   direction,
                    'risk_amount': risk_amt,
                    'move_type':   best_mtype,
                    'score':       best_score,
                    'hit_rate':    best_hit_rate,
                    'n_types_signaling': n_types_signaling,
                    'fee_cost':    fee_cost,
                }

    # Force-close any remaining position at last price
    if open_pos is not None and n > 0:
        last = df_r.iloc[-1].to_dict()
        last_price = float(last.get('close', open_pos['entry_price']) or open_pos['entry_price'])
        direction  = open_pos['direction']
        sl_dist    = open_pos['entry_price'] * sl_pct / 100
        if sl_dist == 0:
            pnl = 0.0
        elif direction == 'LONG':
            pnl = (last_price - open_pos['entry_price']) / sl_dist * open_pos['risk_amount']
        else:
            pnl = (open_pos['entry_price'] - last_price) / sl_dist * open_pos['risk_amount']
        pnl     -= open_pos['fee_cost']
        capital += pnl
        peak     = max(peak, capital)
        max_dd   = max(max_dd, (peak - capital) / peak * 100)
        trades.append({
            'entry_ts':     str(df_r.iloc[open_pos['entry_idx']].get('timestamp', '')),
            'close_ts':     str(last.get('timestamp', '')),
            'move_type':    open_pos['move_type'],
            'direction':    direction,
            'entry_price':  round(open_pos['entry_price'], 4),
            'close_price':  round(last_price, 4),
            'sl':           round(open_pos['sl'], 4),
            'tp':           round(open_pos['tp'], 4),
            'score':        round(open_pos['score'], 2),
            'hit_rate':     round(open_pos.get('hit_rate', 0.0), 3),
            'n_types_signaling': open_pos.get('n_types_signaling', 1),
            'pnl':          round(pnl, 4),
            'pnl_pct':      round(pnl / start_capital * 100, 3),
            'close_reason': 'END',
            'capital_after': round(capital, 4),
            'bars_held':    n - 1 - open_pos['entry_idx'],
        })

    # ── Summary ───────────────────────────────────────────────────────────────
    n_trades = len(trades)
    if n_trades == 0:
        return {
            'n_trades': 0, 'win_rate': 0.0, 'pnl_pct': 0.0,
            'max_drawdown': 0.0, 'sharpe': 0.0, 'profit_factor': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0, 'end_capital': round(capital, 4),
            'trades': [],
        }

    wins    = [t for t in trades if t['pnl'] > 0]
    losses  = [t for t in trades if t['pnl'] <= 0]
    win_rate = len(wins) / n_trades * 100
    pnl_pct  = (capital - start_capital) / start_capital * 100

    pnls     = [t['pnl'] for t in trades]
    mean_pnl = float(np.mean(pnls))
    std_pnl  = float(np.std(pnls)) if len(pnls) > 1 else 1e-9
    sharpe   = mean_pnl / std_pnl * (n_trades ** 0.5) if std_pnl > 0 else 0.0

    total_win  = sum(t['pnl'] for t in wins)
    total_loss = abs(sum(t['pnl'] for t in losses))
    pf         = total_win / total_loss if total_loss > 0 else 999.0

    return {
        'n_trades':      n_trades,
        'win_rate':      round(win_rate, 1),
        'pnl_pct':       round(pnl_pct, 2),
        'max_drawdown':  round(max_dd, 2),
        'sharpe':        round(sharpe, 3),
        'profit_factor': round(pf, 2),
        'avg_win':       round(float(np.mean([t['pnl'] for t in wins]))  if wins   else 0, 4),
        'avg_loss':      round(float(np.mean([t['pnl'] for t in losses])) if losses else 0, 4),
        'end_capital':   round(capital, 4),
        'trades':        trades,
    }
