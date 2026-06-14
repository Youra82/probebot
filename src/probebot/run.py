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

    # ─── [2] Compute features ────────────────────────────────────────────────
    print(f"\n[2/6] Computing features ({len(df_raw)} candles)...")
    df = compute_all_features(df_raw)
    print(f"  {len(df.columns)} feature columns computed")

    # ─── [3] Detect movements ────────────────────────────────────────────────
    print(f"\n[3/6] Detecting movements...")
    detector = MovementDetector(
        atr_impulse=settings.get('atr_multiplier', 1.5),
        breakout_bars=20,
        reversal_min_run=5,
    )
    movements = detector.detect(df)
    if movement_types:
        movements = [m for m in movements if m.move_type in movement_types]
    movements = [m for m in movements if abs(m.magnitude_pct) >= min_move_pct]
    print(f"  Detected {len(movements)} movements")

    rpt.print_header(symbol, timeframe, start_date, end_date, len(movements))
    rpt.print_movement_summary(movements)

    if not movements:
        msg = f"⚠️ Keine signifikanten Bewegungen ≥{min_move_pct}% gefunden.\nTimeframe: {timeframe} | Symbol: {symbol}"
        print(msg)
        tg_msg(msg)
        db.close()
        return

    # ─── [4] Mine patterns + correlations ────────────────────────────────────
    print(f"\n[4/6] Mining patterns & correlations...")
    miner.mine_movements(df, movements, symbol, timeframe, clear_existing=args.clear)
    all_move_types = list({m.move_type for m in movements})
    correlations = correlator.analyze(df, movements, symbol, timeframe, move_types=all_move_types)
    rpt.print_correlations(correlations, top_n=top_n)

    clusters = {}
    if len(movements) >= 4:
        clusters = correlator.cluster_movements(df, movements, n_clusters=min(4, len(movements) // 2))
        rpt.print_clusters(clusters)

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
    generate_html_report(
        symbol=symbol, timeframe=timeframe,
        start_date=start_date, end_date=end_date,
        movements=movements, correlations=correlations,
        clusters=clusters, output_path=html_path,
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


def _fmt(val) -> str:
    if val is None:
        return 'N/A'
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


if __name__ == '__main__':
    main()
