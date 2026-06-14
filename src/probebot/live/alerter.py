"""
Telegram-Alerts für den Live-Scanner.
Formatiert die Kausal-Analyse als übersichtliche Nachrichten.
"""
from pathlib import Path
from typing import Optional

import numpy as np

from ..detection.detector import Movement
from ..report.charts import (
    save_chart, overview_chart, drill_down_chart
)
from ..utils.telegram import send_message, send_photo


PRIORITY_EMOJI = {1: '🔴', 2: '🟡', 3: '⚪'}
CATEGORY_EMOJI = {
    'Chaos/Entropy':         '🌀',
    'Entropy Squeeze':       '🗜',
    'Regime':                '📊',
    'RSI Divergenz':         '📉',
    'RSI Überkauft':         '🔥',
    'RSI Überverkauft':      '🧊',
    'EMA Struktur':          '📐',
    'Volumen':               '📦',
    'CVD / Order Flow':      '🏦',
    'Volatilitäts-Squeeze':  '⚡',
    'Wick-Druck':            '📌',
    'Struktur-Bruch':        '🧱',
    'Kerzen-Muster':         '🕯',
    'Historisches Muster':   '🔗',
}


def send_live_alert(
    alert: dict,
    bot_token: str,
    chat_id: str,
    df=None,
    all_movements=None,
) -> None:
    """Sendet vollständige Live-Alert-Analyse per Telegram."""
    if not bot_token or not chat_id:
        return

    m: Movement = alert['movement']
    cause: list = alert['cause']
    similar: list = alert['similar']
    outlook: dict = alert['outlook']
    dd: dict = alert['drill_down']
    state: dict = alert['current_state']
    symbol: str = alert['symbol']
    timeframe: str = alert['timeframe']

    # ── Nachricht 1: Bewegungs-Alarm ────────────────────────────────────────
    msg1 = _format_alert_header(m, symbol, timeframe, state)
    send_message(bot_token, chat_id, msg1)

    # ── Nachricht 2: Ursachen-Analyse ────────────────────────────────────────
    if cause:
        msg2 = _format_causes(cause, m.direction)
        send_message(bot_token, chat_id, msg2)

    # ── Nachricht 3: Historischer Vergleich + Prognose ────────────────────────
    if similar or outlook:
        msg3 = _format_outlook(similar, outlook, m.direction)
        send_message(bot_token, chat_id, msg3)

    # ── Nachricht 4: MTF Drill-Down ───────────────────────────────────────────
    if dd:
        msg4 = _format_drill_down(dd)
        send_message(bot_token, chat_id, msg4)

    # ── Chart: Overview mit aktuellem Event markiert ──────────────────────────
    if df is not None and all_movements is not None:
        sym_safe = symbol.replace('/', '_').replace(':', '_')
        ts_safe = str(m.timestamp)[:10]
        p_overview = save_chart(
            overview_chart,
            df=df, movements=all_movements,
            symbol=symbol, timeframe=timeframe,
            prefix=f'live_overview_{sym_safe}_{timeframe}_{ts_safe}',
        )
        if p_overview:
            send_photo(
                bot_token, chat_id, p_overview,
                f"📊 Live Overview — {symbol} | {timeframe}"
            )

    # ── Chart: Drill-Down ────────────────────────────────────────────────────
    if dd:
        sym_safe = symbol.replace('/', '_').replace(':', '_')
        ts_safe = str(m.timestamp)[:10]
        p_dd = save_chart(
            drill_down_chart,
            movement=m, drill_down=dd, symbol=symbol,
            prefix=f'live_drilldown_{sym_safe}_{timeframe}_{ts_safe}',
        )
        if p_dd:
            direction_sym = '▼' if m.direction == 'DOWN' else '▲'
            send_photo(
                bot_token, chat_id, p_dd,
                f"🔬 MTF Drill-Down — {m.move_type} {direction_sym} {m.magnitude_pct:+.1f}%"
            )


def send_no_alert(bot_token: str, chat_id: str, symbol: str, timeframe: str, min_pct: float):
    """Kurze Meldung wenn kein signifikanter Move erkannt."""
    send_message(
        bot_token, chat_id,
        f"✅ <b>PROBEBOT Live-Scan</b>\n"
        f"<code>{symbol}</code> | <code>{timeframe}</code>\n"
        f"Kein signifikanter Move ≥{min_pct}% in letzten Kerzen."
    )


# ─── Formatter ────────────────────────────────────────────────────────────────

def _format_alert_header(m: Movement, symbol: str, timeframe: str, state: dict) -> str:
    dir_sym = '▼' if m.direction == 'DOWN' else '▲'
    alert_emoji = '🚨' if abs(m.magnitude_pct) >= 4 else '⚠️'
    color_word = 'CRASH' if (m.direction == 'DOWN' and abs(m.magnitude_pct) >= 5) else \
                 'PUMP' if (m.direction == 'UP' and abs(m.magnitude_pct) >= 5) else \
                 m.move_type.replace('_', ' ')

    regime = state.get('regime', 'N/A')
    rsi = state.get('rsi_14')
    adx = state.get('adx')
    ent = state.get('entropy_20')
    hurst = state.get('hurst_60')
    ts_str = str(state.get('timestamp', ''))[:16]
    close_price = state.get('close', 0)
    move_ready = state.get('move_readiness')
    trend_sc = state.get('trend_score')

    lines = [
        f"{alert_emoji} <b>LIVE ALERT — {color_word}</b>",
        f"",
        f"Symbol:    <code>{symbol}</code>  |  <code>{timeframe}</code>",
        f"Zeitpunkt: <code>{ts_safe(m.timestamp)}</code>",
        f"Kurs:      <code>{close_price:,.2f}</code>",
        f"Move:      <b>{dir_sym} {m.magnitude_pct:+.2f}%</b>  ({m.atr_multiple:.1f}× ATR)",
        f"Typ:       <b>{m.move_type}</b>",
        f"",
        f"📊 Aktueller Markt-Zustand:",
        f"  Regime:        <b>{regime}</b>",
        f"  RSI (14):      <code>{_fmt(rsi)}</code>",
        f"  ADX:           <code>{_fmt(adx)}</code>",
        f"  Entropy:       <code>{_fmt(ent)}</code>",
        f"  Hurst:         <code>{_fmt(hurst)}</code>",
        f"  Trend-Score:   <code>{_fmt(trend_sc)}</code>/10",
        f"  Move-Ready:    <code>{_fmt(move_ready)}</code>/10",
    ]
    return '\n'.join(lines)


def _format_causes(causes: list, direction: str) -> str:
    dir_sym = '▼ SHORT' if direction == 'DOWN' else '▲ LONG'
    lines = [
        f"🔍 <b>WARUM ist das gerade passiert?</b>",
        f"",
        f"Richtung: <b>{dir_sym}</b>",
        f"",
    ]
    for i, c in enumerate(causes[:8], 1):
        cat_emoji = CATEGORY_EMOJI.get(c['category'], '•')
        prio_emoji = PRIORITY_EMOJI.get(c['priority'], '⚪')
        lines.append(f"{prio_emoji} {cat_emoji} <b>{c['category']}</b>")
        lines.append(f"   {c['text']}")
        if i < len(causes):
            lines.append("")

    return '\n'.join(lines)


def _format_outlook(similar: list, outlook: dict, direction: str) -> str:
    lines = [f"🔗 <b>Historischer Vergleich</b>", ""]

    if similar:
        lines.append(f"Ähnlichste historische Events ({len(similar)} gefunden):")
        for s in similar[:4]:
            sim_score = s.get('similarity_score', 0)
            ts = str(s.get('timestamp', ''))[:10]
            mag = s.get('magnitude_pct', 0)
            mtype = s.get('move_type', '?')
            sim_bar = '█' * int(sim_score * 10)
            lines.append(
                f"  <code>{ts}</code>  {mtype}  "
                f"<b>{mag:+.1f}%</b>  Ähnlichkeit: {sim_score:.0%} {sim_bar}"
            )

    if outlook:
        lines.append("")
        lines.append(f"📈 <b>Prognose</b> (aus {outlook.get('n_similar', 0)} ähnlichen Events):")
        lines.append(f"  Hit-Rate gleiche Richtung: <b>{outlook.get('hit_rate_pct', 0):.0f}%</b>")
        lines.append(f"  Medianer weiterer Move:    <b>{outlook.get('median_magnitude', 0):.1f}%</b>")
        lines.append(f"  Maximaler weiterer Move:   <b>{outlook.get('max_magnitude', 0):.1f}%</b>")

        best = outlook.get('best_match')
        if best:
            lines.append("")
            lines.append(f"  Bestes Match: <code>{str(best.get('timestamp',''))[:10]}</code>  "
                         f"{best.get('move_type','?')}  {best.get('magnitude_pct',0):+.1f}%")
            ctx = best.get('context', {})
            if ctx:
                b_regime = ctx.get('regime', '?')
                b_rsi = ctx.get('rsi_14', '?')
                lines.append(f"  Kontext damals: Regime={b_regime}  RSI={_fmt(b_rsi)}")

    return '\n'.join(lines)


def _format_drill_down(dd: dict) -> str:
    lines = [f"⏱ <b>MTF Drill-Down — Entry-Analyse</b>", ""]

    best_tf = None
    best_conf = 0
    best_entry_ts = None
    best_signals = []

    for tf, level in dd.items():
        if not isinstance(level, dict) or 'error' in level:
            continue
        conf = level.get('entry_confidence', 0)
        conf_bar = '█' * conf + '░' * (10 - conf)
        conf_color = 'STARK' if conf >= 7 else 'MITTEL' if conf >= 4 else 'SCHWACH'
        entry_ts = str(level.get('entry_ts', 'N/A'))[:16]
        regime = level.get('regime', '?')
        rsi = level.get('rsi_14')
        hurst = level.get('hurst_60')
        entropy = level.get('entropy_20')

        lines.append(f"<b>{tf}</b>  [{conf_bar}] {conf}/10 ({conf_color})")
        lines.append(f"  Entry: <code>{entry_ts}</code>  Regime: {regime}")
        lines.append(f"  RSI: {_fmt(rsi)}  Hurst: {_fmt(hurst)}  Entropy: {_fmt(entropy)}")

        # Precursors
        precs = level.get('precursors', [])
        for p in precs[:2]:
            lines.append(f"  ⚡ {p}")

        # Signals
        sigs = level.get('entry_signals', [])
        for s in sigs[:2]:
            lines.append(f"  ✓ {s.split('(')[0].strip()}")

        if conf > best_conf:
            best_conf = conf
            best_tf = tf
            best_entry_ts = entry_ts
            best_signals = sigs[:3]

        lines.append("")

    if best_tf:
        lines.append(f"🎯 <b>Bestes Entry-Signal: {best_tf}  ({best_conf}/10)</b>")
        lines.append(f"   Zeitpunkt: <code>{best_entry_ts}</code>")
        for s in best_signals:
            lines.append(f"   ✓ {s.split('(')[0].strip()}")

    return '\n'.join(lines)


# ─── Utils ────────────────────────────────────────────────────────────────────

def _fmt(val) -> str:
    if val is None:
        return 'N/A'
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


def ts_safe(ts) -> str:
    try:
        return str(ts)[:16]
    except Exception:
        return str(ts)
