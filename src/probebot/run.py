"""
Probebot — Market Forensics Engine
Usage:
    python -m probebot.run --symbol "BTC/USDT:USDT" --timeframe 1d
                           --start_date 2022-01-01 --end_date 2025-01-01
                           [--mode scan|investigate|full|live]
                           [--investigate_date 2024-03-14]
                           [--min_move_pct 2.5] [--top_n 5]
                           [--drill_down] [--no_drill_down]
                           [--no_telegram] [--clear]
                           [--movement_types BREAKDOWN,IMPULSE_DOWN]
                           [--scan_candles 5]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / 'src'))

# secret.json is one level above the probebot directory (shared with all bots)
SECRET_PATH = ROOT.parent / 'secret.json'
if not SECRET_PATH.exists():
    SECRET_PATH = ROOT / 'secret.json'


def main():
    parser = argparse.ArgumentParser(description='Probebot Market Forensics')
    parser.add_argument('--symbol', default=None)
    parser.add_argument('--timeframe', default=None)
    parser.add_argument('--start_date', default=None)
    parser.add_argument('--end_date', default=None)
    parser.add_argument('--mode', default='full', choices=['scan', 'investigate', 'full', 'live'])
    parser.add_argument('--investigate_date', default=None)
    parser.add_argument('--min_move_pct', type=float, default=None)
    parser.add_argument('--top_n', type=int, default=None)
    parser.add_argument('--drill_down', dest='drill_down', action='store_true', default=None)
    parser.add_argument('--no_drill_down', dest='drill_down', action='store_false')
    parser.add_argument('--no_telegram', action='store_true', default=False)
    parser.add_argument('--clear', action='store_true', default=False)
    parser.add_argument('--movement_types', default=None)
    parser.add_argument('--scan_candles', type=int, default=None,
                        help='Live-Modus: Anzahl der letzten Kerzen die auf Bewegungen geprüft werden')
    args = parser.parse_args()

    # ─── Load settings ──────────────────────────────────────────────────────
    settings_path = ROOT / 'settings.json'
    settings = {}
    if settings_path.exists():
        with open(settings_path) as f:
            settings = json.load(f)

    symbol = args.symbol or settings.get('symbol', 'BTC/USDT:USDT')
    timeframe = args.timeframe or settings.get('primary_timeframe', '1d')
    start_date = args.start_date or settings.get('start_date', '2022-01-01')
    end_date = args.end_date or settings.get('end_date', '2025-01-01')
    min_move_pct = args.min_move_pct or settings.get('min_move_pct', 2.5)
    top_n = args.top_n or settings.get('report_top_n', 5)
    drill_down = args.drill_down if args.drill_down is not None else settings.get('drill_down', True)
    exchange = settings.get('exchange', 'bitget')
    drill_tfs = settings.get('drill_down_timeframes', ['4h', '1h', '15m', '5m', '1m'])
    use_telegram = not args.no_telegram
    movement_types = (
        args.movement_types.split(',') if args.movement_types
        else settings.get('movement_types', None)
    )

    # ─── Telegram setup ─────────────────────────────────────────────────────
    from probebot.utils.telegram import load_telegram_config, send_message, send_photo, send_document
    tg = load_telegram_config(str(SECRET_PATH)) if use_telegram else {}
    tg_token = tg.get('bot_token', '')
    tg_chat = tg.get('chat_id', '')

    def tg_msg(text: str):
        if use_telegram and tg_token:
            send_message(tg_token, tg_chat, text)

    def tg_photo(path: str, caption: str = ''):
        if use_telegram and tg_token and path:
            send_photo(tg_token, tg_chat, path, caption)

    def tg_doc(path: str, caption: str = ''):
        if use_telegram and tg_token and path:
            send_document(tg_token, tg_chat, path, caption)

    print(f"\nProbebot starting...")
    print(f"  Symbol: {symbol} | TF: {timeframe} | Mode: {args.mode}")
    print(f"  Telegram: {'enabled' if (use_telegram and tg_token) else 'disabled'}")

    # ─── LIVE MODE ──────────────────────────────────────────────────────────
    if args.mode == 'live':
        _run_live(
            args=args,
            settings=settings,
            symbol=symbol,
            timeframe=timeframe,
            min_move_pct=min_move_pct,
            exchange=exchange,
            drill_tfs=drill_tfs,
            use_telegram=use_telegram,
            tg_token=tg_token,
            tg_chat=tg_chat,
            tg_msg=tg_msg,
            tg_photo=tg_photo,
        )
        return

    # Telegram: nur am Ende 2 Dateien senden — keine Zwischen-Nachrichten

    # ─── Lazy imports ────────────────────────────────────────────────────────
    from probebot.data.loader import DataLoader
    from probebot.features.engine import compute_all_features
    from probebot.detection.detector import MovementDetector
    from probebot.forensics.database import ForensicsDB
    from probebot.forensics.miner import PatternMiner
    from probebot.forensics.correlator import Correlator
    from probebot.forensics.drill_down import DrillDownEngine
    from probebot.report import generator as rpt
    from probebot.report.charts import (
        save_chart, overview_chart, correlation_chart,
        cluster_chart, drill_down_chart, fingerprint_chart,
    )
    from probebot.report.html_report import generate_html_report
    from probebot.report.bot_spec import generate_bot_spec

    loader = DataLoader(exchange_id=exchange, secret_path=str(SECRET_PATH))
    db = ForensicsDB()
    miner = PatternMiner(db, lookback=settings.get('lookback_candles', 10))
    correlator = Correlator(db, lookback=5)
    drill_engine = DrillDownEngine(loader, timeframe_chain=['1d'] + drill_tfs)

    # ─── [1] Load data ───────────────────────────────────────────────────────
    print(f"\n[1/6] Loading {timeframe} data for {symbol}...")
    df_raw = loader.fetch(symbol, timeframe, start_date, end_date)
    print(f"  Loaded {len(df_raw)} candles")

    if df_raw.empty:
        listed = symbol in (getattr(loader.exchange, 'markets', None) or {})
        if not listed:
            msg = f"⏭️  {symbol} ist auf {exchange} nicht gelistet — übersprungen."
        else:
            msg = f"⏭️  Keine Daten für {symbol} {timeframe} im Zeitraum {start_date} → {end_date} — übersprungen."
        print(msg)
        tg_msg(msg)
        db.close()
        sys.exit(2)  # 2 = kein Fehler, nur nichts zu tun — Aufrufer soll weitermachen

    # ─── Datenqualitäts-Check: Lücken erkennen ───────────────────────────────
    # Bitget liefert für 1h/4h historische Daten mit bis zu 56-Tage-Lücken.
    # Solche Lücken erzeugen "Phantom-Moves" (z.B. +43% auf einer "1h"-Kerze).
    _tf_min_map = {
        '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
        '1h': 60, '2h': 120, '4h': 240, '6h': 360, '12h': 720,
        '1d': 1440, '3d': 4320, '1w': 10080,
    }
    _expected_min = _tf_min_map.get(timeframe, 60)
    _gaps_min = df_raw['timestamp'].diff().dropna().dt.total_seconds() / 60
    _median_gap = _gaps_min.median()
    _max_gap = _gaps_min.max()
    _gap_ratio = _max_gap / _expected_min
    _max_gap_days = _max_gap / 1440

    print(f"  Lücken-Check: median={_median_gap:.0f}min, max={_max_gap:.0f}min "
          f"({_max_gap_days:.1f} Tage, {_gap_ratio:.0f}× erwartet={_expected_min}min)")

    if _gap_ratio > 10:
        print(f"  ⚠️  Datenlücken erkannt: max. Lücke {_max_gap_days:.1f} Tage bei {timeframe} — Ergebnisse können Artefakte enthalten")
    elif _gap_ratio > 2:
        print(f"  ⚠️  Daten-Warnung: Max-Lücke {_gap_ratio:.0f}× größer als erwartet")

    # ─── [2] Compute features ────────────────────────────────────────────────
    print(f"\n[2/6] Computing features ({len(df_raw)} candles)...")
    df = compute_all_features(df_raw)
    print(f"  {len(df.columns)} feature columns computed")

    # Save processed df to parquet so optimizer/show_results can load without re-fetching
    _data_dir = ROOT / 'artifacts' / 'data'
    _data_dir.mkdir(parents=True, exist_ok=True)
    _sym_safe = symbol.replace('/', '_').replace(':', '_')
    _data_path = _data_dir / f'data_{_sym_safe}_{timeframe}.parquet'
    df.to_parquet(str(_data_path), index=False)
    print(f"  Data cached: {_data_path.name}")

    # ─── [3] Detect movements ────────────────────────────────────────────────
    print(f"\n[3/6] Detecting movements...")

    # Berechne Ziel-Events aus tatsächlicher Datenzeitspanne
    # (Bitget limitiert 1h auf ~5200 Kerzen — CLI-Args können weiter zurückreichen)
    from datetime import datetime as _dt
    try:
        _actual_ts0 = df['timestamp'].iloc[0]
        _actual_ts1 = df['timestamp'].iloc[-1]
        _years_pre = max((_actual_ts1 - _actual_ts0).total_seconds() / (365.25 * 86400), 0.1)
    except Exception:
        _years_pre = 1.0
    _target_epy = _target_events_per_year(timeframe)
    _min_events_needed = int(_target_epy * _years_pre)
    print(f"  Daten: {str(df['timestamp'].iloc[0])[:10]} → {str(df['timestamp'].iloc[-1])[:10]} "
          f"({len(df)} Kerzen, {_years_pre:.2f}J) → Ziel ≥{_min_events_needed} Events")

    # Timeframe-adaptive Detector-Parameter
    # Je kürzer der TF, desto strenger die Strukturkriterien (sonst Rauschen)
    _tf_params = {
        '1w':  dict(breakout_bars=10, reversal_min_run=3),
        '3d':  dict(breakout_bars=12, reversal_min_run=3),
        '1d':  dict(breakout_bars=20, reversal_min_run=5),
        '12h': dict(breakout_bars=24, reversal_min_run=6),
        '6h':  dict(breakout_bars=28, reversal_min_run=7),
        '4h':  dict(breakout_bars=30, reversal_min_run=8),
        '2h':  dict(breakout_bars=36, reversal_min_run=9),
        '1h':  dict(breakout_bars=48, reversal_min_run=12),
        '30m': dict(breakout_bars=60, reversal_min_run=15),
        '15m': dict(breakout_bars=80, reversal_min_run=20),
        '5m':  dict(breakout_bars=96, reversal_min_run=24),
        '1m':  dict(breakout_bars=120, reversal_min_run=30),
    }
    _tf_p = _tf_params.get(timeframe, dict(breakout_bars=20, reversal_min_run=5))

    # Adaptiver atr_impulse: senkt Sensitivität wenn Detector zu wenige Events findet
    _atr_steps = [settings.get('atr_multiplier', 1.5), 1.2, 1.0, 0.75, 0.5]
    _used_atr = _atr_steps[0]
    all_movements = []
    for _atr_try in _atr_steps:
        _det = MovementDetector(
            atr_impulse=_atr_try,
            breakout_bars=_tf_p['breakout_bars'],
            reversal_min_run=_tf_p['reversal_min_run'],
        )
        _mvs = _det.detect(df)
        if movement_types:
            _mvs = [m for m in _mvs if m.move_type in movement_types]
        all_movements = _mvs
        _used_atr = _atr_try
        if len(all_movements) >= _min_events_needed:
            break
        if _atr_try != _atr_steps[-1]:
            print(f"  atr_impulse={_atr_try}: {len(all_movements)} Events < Ziel {_min_events_needed} → senke auf {_atr_steps[_atr_steps.index(_atr_try)+1]}")

    print(f"  Detector: atr_impulse={_used_atr}, breakout={_tf_p['breakout_bars']}b, "
          f"reversal={_tf_p['reversal_min_run']}c → {len(all_movements)} Events")

    # Auto-Kalibrierung: optimalen min_move_pct für diesen Coin + Zeitraum finden
    # Nur expliziter CLI-Wert zählt als "manuell" — settings.json-Wert wird überschrieben
    _user_set_threshold = args.min_move_pct is not None
    if not _user_set_threshold:
        # Tatsächliche Daten-Zeitspanne (nicht CLI-Args) — Bitget limitiert 1h auf ~5200 Kerzen
        _actual_start = str(df['timestamp'].iloc[0])[:10]
        _actual_end   = str(df['timestamp'].iloc[-1])[:10]
        min_move_pct, _median_atr, _min_total, _years, _calib = _auto_calibrate_min_move(
            df, all_movements, start_date=_actual_start, end_date=_actual_end,
            events_per_year=_target_epy,
        )
        _chosen_n = next((n for t, n in _calib if t == min_move_pct), 0)
        _label = _best_calib_label(_chosen_n, _min_total)
        print(f"  Auto-Kalibrierung (Median ATR={_median_atr:.2f}%, "
              f"Zeitraum={_years:.1f}J, Ziel ≥{_min_total} Events):")
        for _t, _n in _calib:
            _marker = '→' if _t == min_move_pct else ' '
            _flag = ' ✓' if _n >= _min_total else ''
            print(f"  {_marker} {_t:.2f}%: {_n:4d} Events{_flag}")
        print(f"  Gewählt: min_move_pct = {min_move_pct}%  [{_label}]")
    else:
        print(f"  min_move_pct = {min_move_pct}% (manuell)")

    movements = [m for m in all_movements if abs(m.magnitude_pct) >= min_move_pct]
    print(f"  Detected {len(movements)} movements (≥{min_move_pct}%)")

    rpt.print_header(symbol, timeframe, start_date, end_date, len(movements))
    rpt.print_movement_summary(movements)

    if not movements:
        msg = f"⚠️ Keine signifikanten Bewegungen ≥{min_move_pct}% gefunden.\nTimeframe: {timeframe} | Symbol: {symbol}"
        print(msg)
        tg_msg(msg)
        db.close()
        return

    # ─── STRICT 70/30 TRAIN/TEST SPLIT ──────────────────────────────────────
    # KRITISCH: Die 30% Test-Daten sind für den Lernalgorithmus vollständig unsichtbar.
    # Nur die 70% Training-Daten fließen in Mining, Korrelation und Clustering.
    split_idx  = int(len(df) * 0.70)
    split_date = str(df.iloc[split_idx].get('timestamp', df.index[split_idx]))[:10]

    df_train        = df.iloc[:split_idx].copy()   # Lernen NUR auf diesen Daten
    # df_test       = df.iloc[split_idx:].copy()   # Für Validierung — bleibt unberührt

    movements_train = [m for m in movements if m.idx < split_idx]
    movements_test  = [m for m in movements if m.idx >= split_idx]

    print(f"\n  Datentrennung (70% / 30%): Split bei {split_date}")
    print(f"  TRAINING  [{start_date} → {split_date}]: {len(movements_train)} Bewegungen")
    print(f"  TEST      [{split_date} → {end_date}]:   {len(movements_test)} Bewegungen  ← UNSICHTBAR für Lernen")

    if len(movements_train) < 5:
        msg = f"⚠️ Zu wenige Trainings-Bewegungen ({len(movements_train)}). Bitte längeren Zeitraum oder kleineren min_move_pct wählen."
        print(msg)
        tg_msg(msg)
        db.close()
        return

    # ─── [4] Mine patterns + correlations (NUR Training-Daten!) ──────────────
    print(f"\n[4/6] Mining patterns & correlations (NUR 70% Training: {len(movements_train)} Events)...")
    miner.mine_movements(df_train, movements_train, symbol, timeframe, clear_existing=args.clear)
    all_move_types = list({m.move_type for m in movements_train})
    correlations, correlations_meta = correlator.analyze(
        df_train, movements_train, symbol, timeframe, move_types=all_move_types
    )
    rpt.print_correlations(correlations, top_n=top_n)

    clusters = {}
    if len(movements_train) >= 4:
        clusters = correlator.cluster_movements(df_train, movements_train, n_clusters=min(4, len(movements_train) // 2))
        rpt.print_clusters(clusters)

    # ─── [4b] Out-of-Sample Validierung (Test-Daten — Bot hat diese NIE gesehen) ──
    from probebot.forensics.validator import OutOfSampleValidator
    validation_results = {}
    if movements_test and correlations:
        print(f"\n[4b] Out-of-Sample Validierung ({len(movements_test)} Test-Events ab {split_date})...")
        validator = OutOfSampleValidator(lookback=3, signal_window=2)
        validation_results = validator.validate(df, split_idx, movements_test, correlations)
        # Trainings-Anzahl nachträglich eintragen
        for mtype, vr in validation_results.items():
            vr['n_train'] = sum(1 for m in movements_train if m.move_type == mtype)
        # Zusammenfassung ausgeben
        robust_cnt  = sum(1 for vr in validation_results.values()
                          if vr['reliability']['label'] in ('ROBUST', 'STABIL'))
        bot_cnt     = sum(1 for vr in validation_results.values()
                          if vr['reliability']['label'] in ('ROBUST', 'STABIL')
                          and vr.get('precision_pct', 0) >= 10
                          and vr.get('n_train', 0) >= 20)
        print(f"  OOS Validierung: {robust_cnt}/{len(validation_results)} ROBUST/STABIL  →  {bot_cnt} für Bot verwendbar")
        for mtype, vr in sorted(validation_results.items()):
            n_tr   = vr.get('n_train', 0)
            prec   = vr.get('precision_pct', 0)
            label  = vr['reliability']['label']
            in_bot = label in ('ROBUST', 'STABIL') and prec >= 10 and n_tr >= 20
            icon   = vr['reliability']['icon']
            if label in ('ROBUST', 'STABIL') and not in_bot:
                excl = 'n<20' if n_tr < 20 else 'prec<10%'
                suffix = f"  ⛔ excl ({excl})"
            else:
                suffix = ""
            print(f"    {icon} {mtype:<28} n={n_tr:3d}  In-Sample: {vr['insample_hit']:3d}%  "
                  f"OOS-Recall: {vr['recall_pct']:3d}%  OOS-Precision: {prec:3d}%  [{label}]{suffix}")
    else:
        print(f"\n[4b] OOS-Validierung übersprungen (keine Test-Events)")

    # ─── Strategy selection ──────────────────────────────────────────────────
    from probebot.analysis.strategy_selector import select_strategy as _sel_strategy
    _move_stats_sel = {}
    for _m in movements:
        _move_stats_sel.setdefault(_m.move_type, {'n': 0})
        _move_stats_sel[_m.move_type]['n'] += 1
    _selected_strat, _type_scores, _tradeable = _sel_strategy(
        _move_stats_sel, correlations, movements, validation_results or {}
    )
    _selected_strategy_info = {
        'strategy':    _selected_strat,
        'type_scores': {k: round(v, 2) for k, v in _type_scores.items()},
        'tradeable':   _tradeable,
    }
    print(f"\n  Strategie-Auswahl: {_selected_strat}  "
          f"(Score: {_type_scores.get(_selected_strat, 0):.1f})")

    # ─── [5] Drill-Down ──────────────────────────────────────────────────────
    drill_down_results = {}
    if drill_down and movements:
        print(f"\n[5/6] MTF Drill-Down (top {top_n} events)...")

        if args.investigate_date:
            import pandas as pd
            target_ts = pd.Timestamp(args.investigate_date)
            focus = sorted(movements,
                           key=lambda m: abs((pd.Timestamp(m.timestamp) - target_ts).total_seconds()))[:top_n]
        else:
            focus = sorted(movements, key=lambda m: abs(m.magnitude_pct), reverse=True)[:top_n]

        for i, m in enumerate(focus):
            print(f"\n  Event {i+1}/{len(focus)}: {m.move_type} @ {str(m.timestamp)[:16]}")
            dd = drill_engine.drill(symbol, m, m.direction, start_tf=timeframe)
            drill_down_results[str(m.timestamp)] = dd

            similar = miner.find_similar(df, m.idx, symbol, timeframe,
                                         move_type=m.move_type, top_n=3)
            all_db_rows = db.get_movements(symbol, timeframe)
            movement_db_id = next(
                (r['id'] for r in all_db_rows if str(m.timestamp) in r.get('timestamp', '')),
                -1
            )
            if movement_db_id > 0:
                db.update_drill_down(movement_db_id, dd)

            rpt.print_movement_detail(m, dd, similar)

    else:
        print(f"\n[5/6] Drill-down skipped")

    # ─── [6] Charts lokal + 2 Dateien per Telegram ──────────────────────────
    print(f"\n[6/6] Generating charts + reports...")

    chart_dir = ROOT / 'artifacts' / 'charts'
    chart_dir.mkdir(parents=True, exist_ok=True)
    sym_safe = symbol.replace('/', '_').replace(':', '_')

    # Charts lokal speichern (kein Telegram)
    print("  Generating charts (lokal)...")
    save_chart(overview_chart, df=df, movements=movements,
               symbol=symbol, timeframe=timeframe,
               prefix=f'overview_{sym_safe}_{timeframe}')
    save_chart(correlation_chart, correlations=correlations,
               symbol=symbol, timeframe=timeframe,
               prefix=f'correlation_{sym_safe}_{timeframe}', top_n=12)
    save_chart(fingerprint_chart, correlations=correlations,
               symbol=symbol, timeframe=timeframe,
               prefix=f'fingerprint_{sym_safe}_{timeframe}')
    if clusters:
        save_chart(cluster_chart, clusters=clusters,
                   symbol=symbol, timeframe=timeframe,
                   prefix=f'cluster_{sym_safe}_{timeframe}')
    if drill_down_results:
        for m in sorted(movements, key=lambda x: abs(x.magnitude_pct), reverse=True)[:3]:
            dd = drill_down_results.get(str(m.timestamp))
            if dd:
                save_chart(drill_down_chart, movement=m, drill_down=dd,
                           symbol=symbol,
                           prefix=f'drilldown_{sym_safe}_{str(m.timestamp)[:10]}')

    # ── Datei 1: HTML-Report ─────────────────────────────────────────────────
    print("  Generating HTML report...")
    html_path = str(ROOT / 'artifacts' / 'db' / f'report_{sym_safe}_{timeframe}.html')
    # focus movements = die top-N die auch für Drill-Down genutzt wurden
    focus_movements = sorted(movements, key=lambda x: abs(x.magnitude_pct), reverse=True)[:top_n]
    generate_html_report(
        symbol=symbol, timeframe=timeframe,
        start_date=start_date, end_date=end_date,
        movements=movements, correlations=correlations,
        clusters=clusters, output_path=html_path,
        validation_results=validation_results,
        correlations_meta=correlations_meta,
        split_date=split_date,
        movements_train_n=len(movements_train),
        movements_test_n=len(movements_test),
        drill_down_results=drill_down_results,
        focus_movements=focus_movements,
    )
    print(f"  HTML saved: {html_path}")

    # ── Datei 2: Bot-Spec JSON ───────────────────────────────────────────────
    print("  Generating bot spec...")
    spec_path = str(ROOT / 'artifacts' / 'db' / f'bot_spec_{sym_safe}_{timeframe}.json')
    generate_bot_spec(
        symbol=symbol, timeframe=timeframe,
        start_date=start_date, end_date=end_date,
        movements=movements, correlations=correlations,
        clusters=clusters, drill_down_results=drill_down_results,
        output_path=spec_path,
        validation_results=validation_results,
        correlations_meta=correlations_meta,
        selected_strategy=_selected_strategy_info,
        split_date=split_date,
        split_idx=split_idx,
    )
    print(f"  Bot-Spec saved: {spec_path}")

    # ── Telegram: nur diese 2 Dateien ────────────────────────────────────────
    tg_doc(html_path,
           f"📊 <b>Probebot Report</b> — {symbol} {timeframe}\n"
           f"{start_date} → {end_date} | {len(movements)} Bewegungen\n"
           f"Im Browser öffnen für Dark-Theme Dashboard")
    tg_doc(spec_path,
           f"🤖 <b>Bot-Spec</b> — {symbol} {timeframe}\n"
           f"Entry-Bedingungen, Schwellenwerte, Signal-Templates\n"
           f"Direkt verwendbar für neuen Bot")

    db.log_scan(symbol, timeframe, start_date, end_date, len(movements))
    db.close()
    print(f"\nProbebot finished.")
    print(f"  HTML:     {html_path}")
    print(f"  Bot-Spec: {spec_path}")


# ─── Telegram helper functions ────────────────────────────────────────────────

def _send_movement_telegram(tg_msg, movement, drill_down, similar, symbol):
    direction_sym = '▼' if movement.direction == 'DOWN' else '▲'
    mag_sign = '🔴' if movement.direction == 'DOWN' else '🟢'
    lines = [
        f"{mag_sign} <b>{movement.move_type}</b>  {direction_sym} {movement.magnitude_pct:+.1f}%",
        f"Zeitpunkt: <code>{str(movement.timestamp)[:16]}</code>  |  {movement.atr_multiple:.1f}× ATR",
    ]

    ctx = movement.context
    if ctx:
        regime = ctx.get('regime', 'N/A')
        rsi = ctx.get('rsi_14')
        adx = ctx.get('adx')
        ent = ctx.get('entropy_20')
        hurst = ctx.get('hurst_60')
        lines.append(
            f"Regime: {regime}  RSI: {_fmt(rsi)}  ADX: {_fmt(adx)}  "
            f"Entropy: {_fmt(ent)}  Hurst: {_fmt(hurst)}"
        )

    if drill_down:
        best_tf = None
        best_conf = 0
        best_entry = None
        for tf, level in drill_down.items():
            if isinstance(level, dict) and 'error' not in level:
                c = level.get('entry_confidence', 0)
                if c > best_conf:
                    best_conf = c
                    best_tf = tf
                    best_entry = level.get('entry_ts')
        if best_tf:
            lines.append(f"\n⏱ Bestes Entry-Signal: <code>{best_tf}</code>  "
                         f"Confidence: {best_conf}/10")
            if best_entry:
                lines.append(f"Entry-Zeitpunkt: <code>{str(best_entry)[:16]}</code>")

        # Top precursors
        all_precursors = []
        for tf, level in drill_down.items():
            if isinstance(level, dict):
                all_precursors.extend(level.get('precursors', []))
        if all_precursors:
            lines.append("\n🔍 <b>Vorbedingungen:</b>")
            for p in list(dict.fromkeys(all_precursors))[:4]:
                lines.append(f"  • {p}")

    if similar:
        lines.append(f"\n🔗 <b>Ähnliche Ereignisse ({len(similar)}):</b>")
        for s in similar[:2]:
            sim = s.get('similarity_score', 0)
            lines.append(
                f"  {str(s.get('timestamp', ''))[:10]}  "
                f"{s.get('move_type')}  {s.get('magnitude_pct', 0):+.1f}%  "
                f"Ähnlichkeit: {sim:.0%}"
            )

    tg_msg('\n'.join(lines))


def _send_final_summary(tg_msg, symbol, timeframe, movements, correlations, clusters):
    from collections import Counter
    type_counts = Counter(m.move_type for m in movements)
    top_type = type_counts.most_common(1)[0] if type_counts else ('N/A', 0)

    # Best predictors across all move types
    all_predictors = []
    for mtype, ranked in correlations.items():
        for r in ranked[:3]:
            if abs(r['t_statistic']) >= 2.0:
                all_predictors.append((abs(r['t_statistic']), r['feature'], mtype, r['t_statistic']))
    all_predictors.sort(reverse=True)

    lines = [
        f"✅ <b>PROBEBOT abgeschlossen</b>",
        f"Symbol: <code>{symbol}</code>  TF: <code>{timeframe}</code>",
        f"Total Movements: <b>{len(movements)}</b>  |  Häufigster Typ: {top_type[0]} ({top_type[1]}×)",
    ]

    if clusters:
        lines.append(f"Pattern-Cluster: <b>{len(clusters)}</b> Gruppen identifiziert")

    if all_predictors:
        lines.append(f"\n📊 <b>Stärkste Prädiktoren:</b>")
        for t_abs, feat, mtype, t_stat in all_predictors[:5]:
            direction = '↑ erhöht' if t_stat > 0 else '↓ erniedrigt'
            lines.append(f"  • <code>{feat}</code> {direction} vor {mtype}  (t={t_stat:+.2f})")

    tg_msg('\n'.join(lines))


def _run_live(
    args, settings, symbol, timeframe, min_move_pct, exchange,
    drill_tfs, use_telegram, tg_token, tg_chat, tg_msg, tg_photo,
):
    """Live-Modus: scannt aktuelle Daten, erklärt die aktuelle Marktbewegung."""
    from probebot.data.loader import DataLoader
    from probebot.forensics.database import ForensicsDB
    from probebot.forensics.drill_down import DrillDownEngine
    from probebot.live.scanner import LiveScanner
    from probebot.live.alerter import send_live_alert, send_no_alert

    scan_candles = args.scan_candles or settings.get('scan_candles', 5)
    lookback = settings.get('lookback_candles', 300)

    print(f"\n[LIVE] Starte Live-Scan...")
    print(f"  Symbol: {symbol}  Timeframe: {timeframe}")
    print(f"  Letzte Kerzen: {scan_candles}  Min-Move: {min_move_pct}%")

    loader = DataLoader(exchange_id=exchange, secret_path=str(SECRET_PATH))
    db = ForensicsDB()

    tf_chain = [timeframe] + [t for t in drill_tfs if t != timeframe]
    drill_engine = DrillDownEngine(loader, timeframe_chain=tf_chain)

    scanner = LiveScanner(
        loader=loader,
        db=db,
        drill_engine=drill_engine,
        min_move_pct=min_move_pct,
        lookback_candles=lookback,
        recent_candles=scan_candles,
    )

    alerts = scanner.scan(symbol, timeframe, drill_down_tfs=drill_tfs)

    if not alerts:
        print(f"  [LIVE] Keine signifikante Bewegung erkannt (≥{min_move_pct}%).")
        if use_telegram and tg_token:
            send_no_alert(tg_token, tg_chat, symbol, timeframe, min_move_pct)
        db.close()
        return

    print(f"\n  [LIVE] {len(alerts)} Alert(s) erkannt — sende Telegram...")
    for alert in alerts:
        m = alert['movement']
        print(f"    → {m.move_type} {m.direction} {m.magnitude_pct:+.2f}%  "
              f"@ {str(m.timestamp)[:16]}")

        if use_telegram and tg_token:
            # Pass df and movements for overview chart if available
            df_for_chart = alert.get('df')
            all_mvts_for_chart = alert.get('all_movements')
            send_live_alert(
                alert=alert,
                bot_token=tg_token,
                chat_id=tg_chat,
                df=df_for_chart,
                all_movements=all_mvts_for_chart,
            )
        else:
            # Terminal output only
            print(f"\n    Ursachen ({len(alert['cause'])}):")
            for c in alert['cause'][:5]:
                print(f"      [{c['priority']}] {c['category']}: {c['text']}")
            outlook = alert.get('outlook', {})
            if outlook:
                print(f"\n    Prognose: Hit-Rate {outlook.get('hit_rate_pct', 0):.0f}%  "
                      f"Med. weiterer Move: {outlook.get('median_magnitude', 0):.1f}%")

    db.close()
    print("\n[LIVE] Fertig.")


_MIN_EVENTS_PER_YEAR = 300  # Baseline, kalibriert an 1h (300 von ~8760 Kerzen/Jahr = 3.4%)

_TF_MINUTES_FOR_TARGET = {
    '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
    '1h': 60, '2h': 120, '4h': 240, '6h': 360, '12h': 720,
    '1d': 1440, '3d': 4320, '1w': 10080,
}


def _target_events_per_year(timeframe: str) -> float:
    """
    Skaliert das Event-Ziel proportional zur Kerzenzahl/Jahr des Timeframes.
    _MIN_EVENTS_PER_YEAR (300) ist an 1h kalibriert (~3.4% aller Kerzen).
    Ohne Skalierung verlangt ein fixes 300/Jahr-Ziel bei 1d (~365 Kerzen/Jahr),
    dass 82% aller Tage "Events" sind — der Detector müsste atr_impulse bis
    zum Boden drücken, um das zu erreichen (IMPULSE-Rauschen dominiert alles).
    """
    minutes = _TF_MINUTES_FOR_TARGET.get(timeframe, 60)
    candles_per_year = (365.25 * 1440) / minutes
    baseline_candles_per_year = (365.25 * 1440) / 60  # 1h
    fraction = _MIN_EVENTS_PER_YEAR / baseline_candles_per_year
    return max(20.0, candles_per_year * fraction)


def _auto_calibrate_min_move(df, all_movements, atr_col='atr_pct',
                              start_date='', end_date='', events_per_year=None):
    """
    Findet optimalen min_move_pct für diesen Coin + Zeitraum.
    Ziel: mindestens `events_per_year` Events pro Analysejahr (timeframe-skaliert).
    Sucht den höchsten Schwellwert der dieses Minimum noch erreicht,
    damit die Events so sauber/signifikant wie möglich sind.
    Wenn kein Schwellwert ausreicht, wird der niedrigste genommen.
    """
    from collections import Counter
    from datetime import datetime

    median_atr = float(df[atr_col].median()) if atr_col in df.columns else 1.5
    target_epy = events_per_year if events_per_year is not None else _MIN_EVENTS_PER_YEAR

    # Analysejahre berechnen
    try:
        t0 = datetime.strptime(start_date[:10], '%Y-%m-%d')
        t1 = datetime.strptime(end_date[:10],   '%Y-%m-%d')
        years = max((t1 - t0).days / 365.25, 0.25)
    except Exception:
        years = 1.0
    min_total = int(target_epy * years)

    # ATR-Multiples + feste Ladder, dedupliziert, in [0.05, 15.0]
    atr_multiples = [round(median_atr * m, 2) for m in (0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)]
    fixed = [0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0]
    candidates = sorted({round(c, 2) for c in atr_multiples + fixed if 0.05 <= c <= 15.0})

    results = []
    for t in candidates:
        filtered = [m for m in all_movements if abs(m.magnitude_pct) >= t]
        total = len(filtered)
        results.append((t, total))

    # Höchsten Schwellwert wählen der noch ≥ min_total Events liefert
    above = [(t, n) for t, n in results if n >= min_total]
    if above:
        best_threshold = above[-1][0]   # höchster Threshold mit ausreichend Events
    else:
        # Kein Threshold erreicht Minimum → niedrigsten nehmen (meiste Events)
        best_threshold = results[0][0] if results else 0.5

    return best_threshold, median_atr, min_total, years, results


def _best_calib_label(total, min_total):
    ratio = total / min_total if min_total else 1
    if ratio >= 1.0:
        return 'OK'
    if ratio >= 0.7:
        return 'KNAPP'
    return 'ZU WENIG DATEN'


def _fmt(val) -> str:
    if val is None:
        return 'N/A'
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


def _fmt_float(val):
    try:
        v = float(val)
        import math
        return None if math.isnan(v) else round(v, 4)
    except (TypeError, ValueError):
        return None


if __name__ == '__main__':
    main()
