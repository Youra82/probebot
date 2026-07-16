"""
gpu_backtester.py — Batched/vektorisierter Backtest-Engine (PyTorch, CPU oder CUDA).

Simuliert VIELE Parameter-Kombinationen gleichzeitig als Tensor-Operationen statt
wie backtester.py.run_backtest() eine Kombination nach der anderen sequenziell in
reinem Python. Das ist die eigentliche Quelle des Speedups fuer den Optuna-
Optimizer (siehe optimizer.py).

GEMESSENER BEFUND (lokal, RTX 3060 Ti, TRX/4h ~8400 Trainings-Kerzen, gegen
backtester.run_backtest() parity-geprueft):
  Legacy (sequenziell, 1 Trial):        1424 ms/Trial
  Batched CPU  (Batch=256/1024/4096):     19 / 5.5 / 2.4 ms/Trial
  Batched CUDA (Batch=256/1024/4096):     47 / 12  / 3.1 ms/Trial
CPU ist bei diesem Workload durchgehend schneller als CUDA (~600x schneller als
Legacy bei Batch=4096) — die Bar-fuer-Bar-Schleife bleibt sequenziell (~8000+
Python-Iterationen mit je ~15-20 kleinen Tensor-Operationen), und der CUDA-
Kernel-Launch-Overhead pro Iteration dominiert bei diesen Datengroessen ueber
die eigentliche Rechenzeit. GPU koennte bei SEHR grossen Batches (>>4096)
aufholen, aber dort steigt der Speicherbedarf schnell in den zweistelligen
GB-Bereich (siehe MAX_SIGNAL_MATRIX_BYTES unten) — deshalb bevorzugt 'auto'
aktuell CPU statt CUDA. 'cuda' bleibt als expliziter Override waehlbar.

WICHTIG — Additive-Garantie:
  backtester.py.run_backtest() bleibt die alleinige Referenz-Implementierung und
  wird NICHT veraendert. Dieses Modul wird ausschliesslich innerhalb der Optuna-
  Suche in optimizer.py verwendet (hinter --engine vectorized). Alle anderen
  Analyse-Skripte (show_results.py, monte_carlo.py, etc.) und die finale
  Best-Params-Bestaetigung in optimizer.py laufen immer ueber run_backtest().

Die Bar-fuer-Bar-Schleife bleibt sequenziell (Zustand haengt von der Vorkerze ab),
aber JEDER Schritt verarbeitet alle Trials im Batch gleichzeitig als maskierte
Tensor-Updates (torch.where statt if/elif) — siehe run_backtest_batch().

Korrektheit wird durch test_gpu_parity.py gegen backtester.run_backtest() geprueft,
nicht durch Vermutung.
"""
from typing import Dict, List

from probebot.analysis.backtester import MAINTENANCE_MARGIN_PCT, MAX_MARGIN_FRACTION

EPS = 1e-12

# compute_signal_matrix() haelt zeitweise mehrere (n_bars, n_trials, n_move_types)-
# Tensoren gleichzeitig im Speicher (score/n_total/n_met/hit_rate/masked_score/...).
# Ohne Obergrenze kann eine zu grosse Batch-Groesse den RAM sprengen und die ganze
# Maschine zum Stocken bringen (beobachtet bei ~32000 Trials auf einem 8400-Kerzen-
# Datensatz) — lieber vorher mit klarer Fehlermeldung abbrechen als das riskieren.
MAX_SIGNAL_MATRIX_BYTES = 4 * 1024 ** 3  # 4 GB — Batch=4096 (getestet, ~10s, unproblematisch)
                                          # bleibt erlaubt; Batch=16384 (beobachtet: Maschine
                                          # stockte durch RAM-Druck) bleibt blockiert.
_SIGNAL_MATRIX_TENSOR_COUNT = 6  # score/n_total/n_met/hit_rate/masked_score + Puffer


def estimate_signal_matrix_bytes(n_bars: int, n_trials: int, n_move_types: int) -> int:
    return n_bars * n_trials * n_move_types * 8 * _SIGNAL_MATRIX_TENSOR_COUNT


def resolve_device(preference: str = 'auto'):
    """
    Loest 'auto'/'cpu'/'cuda' zu einem echten torch.device auf. Degradiert sauber:
    kein torch installiert -> (None, Grund) fuer den Legacy-Fallback in optimizer.py;
    cuda angefordert aber nicht verfuegbar -> CPU-Fallback statt Absturz.

    'auto' waehlt CPU, nicht CUDA: gemessen (siehe Modul-Docstring) ist die
    vektorisierte CPU-Ausfuehrung bei den hier relevanten Batch-Groessen (<=4096)
    durchgehend schneller als CUDA, dessen Kernel-Launch-Overhead pro Bar-Schritt
    ueberwiegt. 'cuda' bleibt als expliziter Override waehlbar.
    """
    try:
        import torch
    except ImportError:
        return None, 'torch nicht installiert'

    if preference == 'cpu':
        return torch.device('cpu'), 'cpu (erzwungen)'
    if preference == 'cuda':
        if not torch.cuda.is_available():
            return torch.device('cpu'), 'cuda angefordert, aber nicht verfuegbar -> Fallback CPU'
        return torch.device('cuda'), f'cuda ({torch.cuda.get_device_name(0)})'
    # auto: CPU bevorzugt (siehe Docstring-Begruendung), unabhaengig von CUDA-Verfuegbarkeit
    return torch.device('cpu'), 'cpu (automatisch bevorzugt — schneller als CUDA bei diesem Workload)'


class ConditionData:
    """Pro (Symbol, Timeframe)-Datensatz einmalig vorberechnete, trial-unabhaengige
    Tensoren — wird fuer beliebig viele Trial-Batches wiederverwendet."""
    __slots__ = ('move_type_order', 'move_dirs_sign', 'close', 'high', 'low',
                 'valid_bar', 'valid_bar_cpu', 'n_bars', 'met_elig', 'elig_f', 't_abs')

    def __init__(self):
        self.move_type_order = []
        self.move_dirs_sign = None
        self.close = None
        self.high = None
        self.low = None
        self.valid_bar = None
        # Reines Python-bool-Array (kein Tensor) fuer den Bar-Gueltigkeits-Check
        # in der Schleife: `bool(cuda_tensor[i])` wuerde bei jeder Iteration eine
        # synchrone GPU->CPU-Uebertragung erzwingen (8000+ mal pro Lauf) — das
        # dominiert bei CUDA die Laufzeit komplett, obwohl valid_bar rein
        # datenabhaengig ist (nicht trial-abhaengig) und daher problemlos auf
        # der CPU bleiben kann, unabhaengig vom Rechen-Device.
        self.valid_bar_cpu = None
        self.n_bars = 0
        self.met_elig = {}   # move_type -> (n_bars, C_mt)
        self.elig_f = {}     # move_type -> (n_bars, C_mt)
        self.t_abs = {}      # move_type -> (C_mt,)


def precompute_condition_tensors(df, entry_conditions: Dict, tradeable_move_types: List[Dict],
                                  device, dtype=None) -> ConditionData:
    """Baut die trial-unabhaengigen Bedingungs-Tensoren einmal pro Datensatz.

    Entspricht inhaltlich compute_signal_score() in backtester.py, nur als
    (n_bars, n_conditions)-Matrix statt Bar-fuer-Bar-Python-Schleife."""
    import numpy as np
    import torch

    if dtype is None:
        dtype = torch.float64

    df_r = df.reset_index(drop=True)
    n = len(df_r)

    cd = ConditionData()
    cd.n_bars = n
    cd.close = torch.tensor(df_r['close'].astype(float).values, dtype=dtype, device=device)
    cd.high = torch.tensor(df_r.get('high', df_r['close']).astype(float).values, dtype=dtype, device=device)
    cd.low = torch.tensor(df_r.get('low', df_r['close']).astype(float).values, dtype=dtype, device=device)
    cd.valid_bar = (cd.close > 0) & ~torch.isnan(cd.close)
    cd.valid_bar_cpu = cd.valid_bar.detach().cpu().tolist()

    move_type_order = [mt['move_type'] for mt in tradeable_move_types]
    move_dirs = {mt['move_type']: mt['direction'] for mt in tradeable_move_types}
    cd.move_type_order = move_type_order
    cd.move_dirs_sign = torch.tensor(
        [1.0 if move_dirs[mt] == 'LONG' else -1.0 for mt in move_type_order],
        dtype=dtype, device=device,
    )

    for mt in move_type_order:
        conds = entry_conditions.get(mt, {})
        conditions = conds.get('must_have', []) + conds.get('should_have', [])
        c_n = len(conditions)
        if c_n == 0:
            cd.met_elig[mt] = torch.zeros((n, 0), dtype=dtype, device=device)
            cd.elig_f[mt] = torch.zeros((n, 0), dtype=dtype, device=device)
            cd.t_abs[mt] = torch.zeros((0,), dtype=dtype, device=device)
            continue

        feat_cols = []
        baseline = []
        dir_sign = []
        t_abs = []
        for cond in conditions:
            feat = cond['feature']
            vals = df_r[feat].astype(float).values if feat in df_r.columns else np.full(n, np.nan)
            feat_cols.append(vals)
            baseline.append(cond.get('baseline_avg', 0))
            dir_sign.append(1.0 if cond['direction'] == 'above' else -1.0)
            t_abs.append(abs(cond.get('t_statistic', 0)))

        feat_vals = torch.tensor(np.stack(feat_cols, axis=1), dtype=dtype, device=device)  # (n, c_n)
        baseline_t = torch.tensor(baseline, dtype=dtype, device=device)                     # (c_n,)
        dir_sign_t = torch.tensor(dir_sign, dtype=dtype, device=device)                     # (c_n,)

        eligible = ~torch.isnan(feat_vals)
        above = feat_vals > baseline_t.unsqueeze(0)
        below = feat_vals < baseline_t.unsqueeze(0)
        direction_met = torch.where(dir_sign_t.unsqueeze(0) > 0, above, below)

        cd.met_elig[mt] = (direction_met & eligible).to(dtype)
        cd.elig_f[mt] = eligible.to(dtype)
        cd.t_abs[mt] = torch.tensor(t_abs, dtype=dtype, device=device)

    return cd


def compute_signal_matrix(cd: ConditionData, t_threshold, min_score, min_hit_rate):
    """Vektorisierte Signal-Erkennung fuer einen Batch von Trials, ueber ALLE Bars
    und ALLE Move-Types gleichzeitig (keine Python-Schleife ueber Bars).

    t_threshold/min_score/min_hit_rate: (n_trials,) Tensoren, gleiches device/dtype
    wie cd. Gibt (best_score, best_mtype_idx, best_hit_rate, has_signal,
    n_types_signaling) zurueck, jeweils (n_bars, n_trials).
    """
    import torch

    n_trials = t_threshold.shape[0]
    n_bars = cd.n_bars
    device = t_threshold.device
    dtype = t_threshold.dtype

    score_list, n_total_list, n_met_list = [], [], []
    for mt in cd.move_type_order:
        t_abs = cd.t_abs[mt]           # (c_n,)
        elig_f = cd.elig_f[mt]         # (n_bars, c_n)
        met_elig = cd.met_elig[mt]     # (n_bars, c_n)

        if t_abs.shape[0] == 0:
            zeros = torch.zeros((n_bars, n_trials), dtype=dtype, device=device)
            score_list.append(zeros)
            n_total_list.append(zeros)
            n_met_list.append(zeros)
            continue

        gate = (t_abs.unsqueeze(0) >= t_threshold.unsqueeze(1)).to(dtype)  # (n_trials, c_n)
        weighted_gate = gate * t_abs.unsqueeze(0)                          # (n_trials, c_n)

        n_total_mt = elig_f @ gate.T        # (n_bars, n_trials)
        n_met_mt = met_elig @ gate.T        # (n_bars, n_trials)
        score_mt = met_elig @ weighted_gate.T  # (n_bars, n_trials)

        score_list.append(score_mt)
        n_total_list.append(n_total_mt)
        n_met_list.append(n_met_mt)

    score_all = torch.stack(score_list, dim=2)      # (n_bars, n_trials, n_mtypes)
    n_total_all = torch.stack(n_total_list, dim=2)
    n_met_all = torch.stack(n_met_list, dim=2)

    hit_rate_all = n_met_all / n_total_all.clamp_min(EPS)
    signal_ok = (
        (n_total_all > 0)
        & (score_all >= min_score.view(1, -1, 1))
        & (hit_rate_all >= min_hit_rate.view(1, -1, 1))
    )

    NEG = torch.finfo(dtype).min / 2
    masked_score = torch.where(signal_ok, score_all, torch.full_like(score_all, NEG))
    best_score, best_mtype_idx = masked_score.max(dim=2)  # (n_bars, n_trials) each
    has_signal = best_score > NEG / 2

    n_types_signaling = signal_ok.sum(dim=2)

    safe_idx = best_mtype_idx.clamp_min(0)
    best_hit_rate = torch.gather(hit_rate_all, 2, safe_idx.unsqueeze(2)).squeeze(2)
    best_hit_rate = torch.where(has_signal, best_hit_rate, torch.zeros_like(best_hit_rate))
    best_score = torch.where(has_signal, best_score, torch.zeros_like(best_score))

    return best_score, best_mtype_idx, best_hit_rate, has_signal, n_types_signaling


def _empty_result(start_capital: float) -> Dict:
    return {
        'n_trades': 0, 'win_rate': 0.0, 'pnl_pct': 0.0,
        'max_drawdown': 0.0, 'sharpe': 0.0, 'profit_factor': 0.0,
        'avg_win': 0.0, 'avg_loss': 0.0, 'end_capital': round(start_capital, 4),
        'n_liquidations': 0, 'trades': [],
    }


def run_backtest_batch(df, entry_conditions: Dict, tradeable_move_types: List[Dict],
                        params_list: List[Dict], start_capital: float = 100.0,
                        device=None, dtype=None) -> List[Dict]:
    """Wie backtester.run_backtest(), aber fuer einen ganzen Batch von Parameter-
    Kombinationen gleichzeitig. Gibt eine Liste von Result-Dicts in derselben
    Reihenfolge wie params_list zurueck (dieselben Felder wie run_backtest(),
    ausser 'trades' — die Batch-Engine fuehrt kein Einzeltrade-Log, das der
    Optimizer waehrend der Suche nicht braucht)."""
    import torch

    if device is None:
        device = torch.device('cpu')
    if dtype is None:
        dtype = torch.float64

    n_trials = len(params_list)
    if n_trials == 0:
        return []

    cd = precompute_condition_tensors(df, entry_conditions, tradeable_move_types, device, dtype)
    n_bars = cd.n_bars

    n_mtypes = max(len(cd.move_type_order), 1)
    est_bytes = estimate_signal_matrix_bytes(n_bars, n_trials, n_mtypes)
    if est_bytes > MAX_SIGNAL_MATRIX_BYTES:
        max_safe_trials = max(1, MAX_SIGNAL_MATRIX_BYTES // (n_bars * n_mtypes * 8 * _SIGNAL_MATRIX_TENSOR_COUNT))
        raise ValueError(
            f"run_backtest_batch: geschaetzter Speicherbedarf {est_bytes / 1024**3:.1f} GB "
            f"fuer {n_trials} Trials x {n_bars} Kerzen ueberschreitet das Limit "
            f"({MAX_SIGNAL_MATRIX_BYTES / 1024**3:.0f} GB). Batch-Groesse verkleinern "
            f"(sicher waeren hier ca. <= {max_safe_trials} Trials pro Batch)."
        )

    def _p(key, default):
        return torch.tensor([float(p.get(key, default)) for p in params_list], dtype=dtype, device=device)

    t_threshold = _p('t_threshold', 4.0)
    min_score = _p('min_score', 20.0)
    min_hit_rate = _p('min_hit_rate', 0.4)
    sl_pct = _p('sl_pct', 1.5)
    tp_rr = _p('tp_rr', 2.0)
    leverage = _p('leverage', 10.0).clamp_min(1.0)
    risk_per_trade_pct = _p('risk_per_trade_pct', 1.0)
    max_hold_bars = _p('max_hold_bars', 24.0)
    fee_pct_per_side = _p('fee_pct_per_side', 0.06)

    best_score, best_mtype_idx, best_hit_rate, has_signal, n_types_signaling = \
        compute_signal_matrix(cd, t_threshold, min_score, min_hit_rate)

    zeros = torch.zeros(n_trials, dtype=dtype, device=device)
    zeros_bool = torch.zeros(n_trials, dtype=torch.bool, device=device)
    zeros_long = torch.zeros(n_trials, dtype=torch.long, device=device)

    pos_open = zeros_bool.clone()
    direction = zeros.clone()
    entry_price = zeros.clone()
    sl = zeros.clone()
    tp = zeros.clone()
    liq_price = zeros.clone()
    effective_stop = zeros.clone()
    is_liquidation = zeros_bool.clone()
    margin_required = zeros.clone()
    risk_amount = zeros.clone()
    fee_cost = zeros.clone()
    entry_idx = zeros_long.clone()

    capital = torch.full((n_trials,), float(start_capital), dtype=dtype, device=device)
    peak = capital.clone()
    max_dd = zeros.clone()

    n_trades = zeros_long.clone()
    sum_pnl = zeros.clone()
    sum_pnl_sq = zeros.clone()
    n_wins = zeros_long.clone()
    sum_win_pnl = zeros.clone()
    sum_loss_abs = zeros.clone()
    n_liq = zeros_long.clone()

    max_margin = float(start_capital) * MAX_MARGIN_FRACTION

    for i in range(10, n_bars):
        if not cd.valid_bar_cpu[i]:
            continue

        close_i = cd.close[i]
        high_i = cd.high[i]
        low_i = cd.low[i]

        # ── Manage open position ──────────────────────────────────────────
        is_long = direction > 0
        bars_held = (i - entry_idx).to(dtype)

        stop_trig = pos_open & torch.where(is_long, low_i <= effective_stop, high_i >= effective_stop)
        tp_trig = pos_open & ~stop_trig & torch.where(is_long, high_i >= tp, low_i <= tp)
        timeout_trig = pos_open & ~stop_trig & ~tp_trig & (bars_held >= max_hold_bars)
        closed_mask = stop_trig | tp_trig | timeout_trig

        sl_dist = entry_price * sl_pct / 100
        pnl_stop = torch.where(is_liquidation, -margin_required, -risk_amount)
        pnl_tp = risk_amount * tp_rr
        pnl_timeout = torch.where(
            is_long,
            (close_i - entry_price) / sl_dist.clamp_min(EPS) * risk_amount,
            (entry_price - close_i) / sl_dist.clamp_min(EPS) * risk_amount,
        )
        pnl = torch.where(stop_trig, pnl_stop, torch.where(tp_trig, pnl_tp,
              torch.where(timeout_trig, pnl_timeout, zeros)))
        pnl = pnl - fee_cost

        capital = capital + pnl * closed_mask.to(dtype)
        peak = torch.maximum(peak, capital)
        dd = (peak - capital) / peak.clamp_min(EPS) * 100
        max_dd = torch.maximum(max_dd, dd)

        # Referenz speichert 'pnl' im Trade-Dict GERUNDET (round(pnl, 4)) und
        # berechnet sharpe/profit_factor/avg_win/avg_loss/Win-Klassifikation
        # aus genau diesem gerundeten Wert (siehe backtester.py Zeilen 338-361)
        # — Kapital/PnL%/MaxDD nutzen dagegen den ungerundeten Wert. Fuer exakte
        # Parity muessen die Statistik-Akkumulatoren denselben gerundeten Wert
        # verwenden wie die Referenz, obwohl Kapital weiterhin ungerundet laeuft.
        pnl_r = torch.round(pnl * 10000) / 10000
        is_win = (pnl_r > 0) & closed_mask
        is_loss = closed_mask & ~is_win
        n_trades = n_trades + closed_mask.long()
        sum_pnl = sum_pnl + pnl_r * closed_mask.to(dtype)
        sum_pnl_sq = sum_pnl_sq + (pnl_r * pnl_r) * closed_mask.to(dtype)
        n_wins = n_wins + is_win.long()
        sum_win_pnl = sum_win_pnl + torch.where(is_win, pnl_r, zeros)
        sum_loss_abs = sum_loss_abs + torch.where(is_loss, -pnl_r, zeros)
        n_liq = n_liq + (stop_trig & is_liquidation).long()

        pos_open = pos_open & ~closed_mask

        # ── Open new position ───────────────────────────────────────────────
        can_open = ~pos_open
        sig_i = has_signal[i]
        open_now = can_open & sig_i

        idx_i = best_mtype_idx[i].clamp_min(0)
        direction_new = cd.move_dirs_sign[idx_i]

        risk_amt_new = float(start_capital) * risk_per_trade_pct / 100
        sl_dist_new = close_i * sl_pct / 100
        notional_new = risk_amt_new / (sl_pct / 100).clamp_min(EPS)
        margin_required_new = notional_new / leverage

        over_cap = margin_required_new > max_margin
        scale = torch.where(over_cap, max_margin / margin_required_new.clamp_min(EPS),
                             torch.ones_like(margin_required_new))
        risk_amt_new = risk_amt_new * scale
        notional_new = notional_new * scale
        margin_required_new = torch.minimum(margin_required_new, torch.full_like(margin_required_new, max_margin))

        liq_dist_pct = (100.0 / leverage - MAINTENANCE_MARGIN_PCT).clamp_min(0.05)
        liq_dist_new = close_i * liq_dist_pct / 100

        is_long_new = direction_new > 0
        sl_new = torch.where(is_long_new, close_i - sl_dist_new, close_i + sl_dist_new)
        tp_new = torch.where(is_long_new, close_i + sl_dist_new * tp_rr, close_i - sl_dist_new * tp_rr)
        liq_price_new = torch.where(is_long_new, close_i - liq_dist_new, close_i + liq_dist_new)
        effective_stop_new = torch.where(is_long_new, torch.maximum(sl_new, liq_price_new),
                                          torch.minimum(sl_new, liq_price_new))
        is_liquidation_new = torch.where(is_long_new, liq_price_new > sl_new, liq_price_new < sl_new)

        fee_cost_new = notional_new * (fee_pct_per_side / 100) * 2

        pos_open = pos_open | open_now
        direction = torch.where(open_now, direction_new, direction)
        entry_price = torch.where(open_now, close_i, entry_price)
        sl = torch.where(open_now, sl_new, sl)
        tp = torch.where(open_now, tp_new, tp)
        liq_price = torch.where(open_now, liq_price_new, liq_price)
        effective_stop = torch.where(open_now, effective_stop_new, effective_stop)
        is_liquidation = torch.where(open_now, is_liquidation_new, is_liquidation)
        margin_required = torch.where(open_now, margin_required_new, margin_required)
        risk_amount = torch.where(open_now, risk_amt_new, risk_amount)
        fee_cost = torch.where(open_now, fee_cost_new, fee_cost)
        entry_idx = torch.where(open_now, torch.full_like(entry_idx, i), entry_idx)

    # ── Force-close any remaining open position at the last bar's close ──────
    if n_bars > 0:
        last_close = cd.close[-1]
        is_long = direction > 0
        sl_dist_final = entry_price * sl_pct / 100
        pnl_final = torch.where(
            is_long,
            (last_close - entry_price) / sl_dist_final.clamp_min(EPS) * risk_amount,
            (entry_price - last_close) / sl_dist_final.clamp_min(EPS) * risk_amount,
        )
        pnl_final = torch.where(sl_dist_final == 0, zeros, pnl_final)
        pnl_final = pnl_final - fee_cost

        m = pos_open
        capital = capital + pnl_final * m.to(dtype)
        peak = torch.maximum(peak, capital)
        max_dd = torch.maximum(max_dd, (peak - capital) / peak.clamp_min(EPS) * 100)

        pnl_final_r = torch.round(pnl_final * 10000) / 10000
        is_win = (pnl_final_r > 0) & m
        is_loss = m & ~is_win
        n_trades = n_trades + m.long()
        sum_pnl = sum_pnl + pnl_final_r * m.to(dtype)
        sum_pnl_sq = sum_pnl_sq + (pnl_final_r * pnl_final_r) * m.to(dtype)
        n_wins = n_wins + is_win.long()
        sum_win_pnl = sum_win_pnl + torch.where(is_win, pnl_final_r, zeros)
        sum_loss_abs = sum_loss_abs + torch.where(is_loss, -pnl_final_r, zeros)

    # ── Summary per trial ──────────────────────────────────────────────────
    n_trades_f = n_trades.to(dtype)
    has_trades = n_trades > 0

    win_rate = torch.where(has_trades, n_wins.to(dtype) / n_trades_f.clamp_min(1) * 100, zeros)
    pnl_pct = (capital - float(start_capital)) / float(start_capital) * 100

    mean_pnl = sum_pnl / n_trades_f.clamp_min(1)
    var_pnl = (sum_pnl_sq / n_trades_f.clamp_min(1) - mean_pnl * mean_pnl).clamp_min(0)
    std_pnl = torch.where(n_trades > 1, torch.sqrt(var_pnl), torch.full_like(zeros, 1e-9))
    sharpe = torch.where(std_pnl > 0, mean_pnl / std_pnl * torch.sqrt(n_trades_f), zeros)

    n_losses = n_trades - n_wins
    avg_win = torch.where(n_wins > 0, sum_win_pnl / n_wins.clamp_min(1).to(dtype), zeros)
    avg_loss = torch.where(n_losses > 0, -sum_loss_abs / n_losses.clamp_min(1).to(dtype), zeros)
    profit_factor = torch.where(
        ~has_trades, zeros,
        torch.where(sum_loss_abs > 0, sum_win_pnl / sum_loss_abs.clamp_min(EPS), torch.full_like(zeros, 999.0)),
    )

    # CPU-Transfer einmal am Ende (nicht pro Bar) — wichtig fuer GPU-Performance.
    n_trades_l = n_trades.tolist()
    win_rate_l = win_rate.tolist()
    pnl_pct_l = pnl_pct.tolist()
    max_dd_l = max_dd.tolist()
    sharpe_l = sharpe.tolist()
    pf_l = profit_factor.tolist()
    avg_win_l = avg_win.tolist()
    avg_loss_l = avg_loss.tolist()
    end_capital_l = capital.tolist()
    n_liq_l = n_liq.tolist()

    results = []
    for j in range(n_trials):
        if n_trades_l[j] == 0:
            results.append(_empty_result(start_capital))
            continue
        results.append({
            'n_trades': int(n_trades_l[j]),
            'win_rate': round(win_rate_l[j], 1),
            'pnl_pct': round(pnl_pct_l[j], 2),
            'max_drawdown': round(max_dd_l[j], 2),
            'sharpe': round(sharpe_l[j], 3),
            'profit_factor': round(pf_l[j], 2),
            'avg_win': round(avg_win_l[j], 4),
            'avg_loss': round(avg_loss_l[j], 4),
            'end_capital': round(end_capital_l[j], 4),
            'n_liquidations': int(n_liq_l[j]),
            'trades': [],
        })
    return results
