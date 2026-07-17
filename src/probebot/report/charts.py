"""
Grafische Darstellung der Forensik-Ergebnisse via matplotlib.

Generiert PNGs die anschliessend per Telegram verschickt werden:
  1. overview_chart    — Preischart + Bewegungs-Marker + Entropy/Hurst Subplot
  2. correlation_chart — Top predictive features per Movement-Typ (horizontal bar)
  3. cluster_chart     — Pattern-Cluster Übersicht
  4. drill_down_chart  — MTF Drill-Down eines spezifischen Events
"""
import os
import tempfile
from pathlib import Path
from typing import List, Optional, Dict

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from matplotlib.colors import LinearSegmentedColormap

from ..detection.detector import Movement

# ─── Color scheme ─────────────────────────────────────────────────────────────
COLORS = {
    'bg': '#0d1117',
    'grid': '#21262d',
    'text': '#e6edf3',
    'text_dim': '#8b949e',
    'up': '#3fb950',
    'down': '#f85149',
    'accent': '#58a6ff',
    'yellow': '#d29922',
    'purple': '#bc8cff',
    'orange': '#ffa657',
}

MOVE_COLORS = {
    'BREAKDOWN': '#f85149',
    'IMPULSE_DOWN': '#ff6b6b',
    'REVERSAL_DOWN': '#ffa657',
    'SQUEEZE_RELEASE_DOWN': '#ff8c42',
    'ACCELERATION_DOWN': '#e84393',
    'GAP_DOWN': '#d29922',
    'BREAKOUT_UP': '#3fb950',
    'IMPULSE_UP': '#63f5a1',
    'REVERSAL_UP': '#58a6ff',
    'SQUEEZE_RELEASE_UP': '#4ac6e8',
    'ACCELERATION_UP': '#bc8cff',
    'GAP_UP': '#ffd700',
}


def setup_dark_style():
    plt.rcParams.update({
        'figure.facecolor': COLORS['bg'],
        'axes.facecolor': COLORS['bg'],
        'axes.edgecolor': COLORS['grid'],
        'axes.labelcolor': COLORS['text'],
        'xtick.color': COLORS['text_dim'],
        'ytick.color': COLORS['text_dim'],
        'grid.color': COLORS['grid'],
        'text.color': COLORS['text'],
        'legend.facecolor': '#161b22',
        'legend.edgecolor': COLORS['grid'],
        'legend.labelcolor': COLORS['text'],
        'font.family': 'monospace',
        'font.size': 9,
    })


# ─── Chart 1: Overview ────────────────────────────────────────────────────────

def overview_chart(
    df: pd.DataFrame,
    movements: List[Movement],
    symbol: str,
    timeframe: str,
    out_path: str,
) -> str:
    setup_dark_style()
    has_entropy = 'entropy_20' in df.columns
    has_hurst = 'hurst_60' in df.columns

    n_rows = 1 + int(has_entropy) + int(has_hurst)
    height_ratios = [4] + [1] * (n_rows - 1)

    fig, axes = plt.subplots(
        n_rows, 1, figsize=(16, 3.5 * n_rows),
        gridspec_kw={'height_ratios': height_ratios, 'hspace': 0.08},
        sharex=True,
    )
    if n_rows == 1:
        axes = [axes]

    ax_price = axes[0]
    ts = df['timestamp'] if 'timestamp' in df.columns else pd.RangeIndex(len(df))

    # ── Candlestick-like using high-low range + body fill ──
    for i in range(len(df)):
        o, h, l, c = df['open'].iloc[i], df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i]
        color = COLORS['up'] if c >= o else COLORS['down']
        x = i
        ax_price.plot([x, x], [l, h], color=color, linewidth=0.6, alpha=0.7)
        body_bottom = min(o, c)
        body_top = max(o, c)
        ax_price.bar(x, body_top - body_bottom, bottom=body_bottom,
                     color=color, width=0.7, alpha=0.85, linewidth=0)

    # ── Movement markers ──
    legend_patches = {}
    for m in movements:
        idx = m.idx
        if idx >= len(df):
            continue
        color = MOVE_COLORS.get(m.move_type, COLORS['yellow'])
        y_price = df['high'].iloc[idx] if m.direction == 'UP' else df['low'].iloc[idx]
        offset = (df['high'].max() - df['low'].min()) * 0.02
        y_arrow = y_price + offset if m.direction == 'UP' else y_price - offset
        dy = -offset * 1.5 if m.direction == 'UP' else offset * 1.5
        ax_price.annotate(
            '',
            xy=(idx, y_price),
            xytext=(idx, y_arrow),
            arrowprops=dict(arrowstyle='->', color=color, lw=1.5),
        )
        # Vertical line
        ax_price.axvline(x=idx, color=color, alpha=0.2, linewidth=0.8, linestyle='--')
        # Label: type + magnitude
        label = f"{m.move_type.replace('_', ' ')}\n{m.magnitude_pct:+.1f}%"
        ax_price.text(idx, y_arrow, label, color=color, fontsize=6.5,
                      ha='center', va='bottom' if m.direction == 'UP' else 'top',
                      rotation=0)
        if m.move_type not in legend_patches:
            legend_patches[m.move_type] = mpatches.Patch(color=color, label=m.move_type)

    ax_price.set_ylabel('Price', color=COLORS['text'])
    ax_price.set_title(
        f'PROBEBOT  |  {symbol}  |  {timeframe}  |  {len(movements)} movements detected',
        color=COLORS['accent'], fontsize=12, fontweight='bold', pad=10
    )
    ax_price.grid(True, alpha=0.25, linewidth=0.5)
    if legend_patches:
        ax_price.legend(
            handles=list(legend_patches.values()),
            loc='upper left', fontsize=7, ncol=min(4, len(legend_patches))
        )

    # ── Entropy subplot ──
    row = 1
    if has_entropy:
        ax_ent = axes[row]
        ent = df['entropy_20'].ffill()
        ax_ent.fill_between(range(len(df)), ent, alpha=0.4, color=COLORS['purple'])
        ax_ent.plot(range(len(df)), ent, color=COLORS['purple'], linewidth=0.8)
        # Rolling mean
        ent_mean = ent.rolling(50).mean()
        ax_ent.plot(range(len(df)), ent_mean, color=COLORS['yellow'],
                    linewidth=0.8, linestyle='--', alpha=0.8, label='50-bar mean')
        ax_ent.set_ylabel('Entropy', color=COLORS['text'], fontsize=8)
        ax_ent.grid(True, alpha=0.2, linewidth=0.5)
        # Mark high-entropy zones
        high_ent = ent > ent_mean * 1.15
        ax_ent.fill_between(range(len(df)), ent.min(), ent,
                             where=high_ent, alpha=0.25, color=COLORS['down'], label='High entropy')
        for m in movements:
            if m.idx < len(df):
                color = MOVE_COLORS.get(m.move_type, COLORS['yellow'])
                ax_ent.axvline(x=m.idx, color=color, alpha=0.3, linewidth=0.6)
        row += 1

    # ── Hurst subplot ──
    if has_hurst:
        ax_hurst = axes[row]
        hurst = df['hurst_60'].ffill()
        ax_hurst.plot(range(len(df)), hurst, color=COLORS['accent'], linewidth=0.9)
        ax_hurst.axhline(0.5, color=COLORS['text_dim'], linestyle='--', linewidth=0.7, alpha=0.7)
        ax_hurst.axhline(0.45, color=COLORS['down'], linestyle=':', linewidth=0.7, alpha=0.6)
        ax_hurst.axhline(0.55, color=COLORS['up'], linestyle=':', linewidth=0.7, alpha=0.6)
        ax_hurst.fill_between(range(len(df)), 0.45, 0.55,
                               alpha=0.08, color=COLORS['yellow'], label='Random walk zone')
        ax_hurst.set_ylabel('Hurst', color=COLORS['text'], fontsize=8)
        ax_hurst.set_ylim(0.2, 0.8)
        ax_hurst.grid(True, alpha=0.2, linewidth=0.5)
        for m in movements:
            if m.idx < len(df):
                color = MOVE_COLORS.get(m.move_type, COLORS['yellow'])
                ax_hurst.axvline(x=m.idx, color=color, alpha=0.3, linewidth=0.6)

    # ── X-axis labels ──
    n = len(df)
    step = max(1, n // 12)
    tick_positions = list(range(0, n, step))
    if 'timestamp' in df.columns:
        tick_labels = [str(df['timestamp'].iloc[i])[:10] for i in tick_positions]
    else:
        tick_labels = [str(i) for i in tick_positions]
    axes[-1].set_xticks(tick_positions)
    axes[-1].set_xticklabels(tick_labels, rotation=30, ha='right', fontsize=7)

    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches='tight', facecolor=COLORS['bg'])
    plt.close(fig)
    return out_path


# ─── Chart 2: Correlation / Predictive Features ───────────────────────────────

def correlation_chart(
    correlations: Dict[str, List[dict]],
    symbol: str,
    timeframe: str,
    out_path: str,
    top_n: int = 15,
) -> str:
    setup_dark_style()

    move_types = [mt for mt, data in correlations.items() if data]
    if not move_types:
        return None

    n_cols = min(2, len(move_types))
    n_rows = (len(move_types) + n_cols - 1) // n_cols
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(14, max(4, n_rows * 5)))
    if n_rows * n_cols == 1:
        axes = np.array([[axes]])
    elif n_rows == 1:
        axes = axes.reshape(1, -1)
    elif n_cols == 1:
        axes = axes.reshape(-1, 1)

    fig.suptitle(
        f'PROBEBOT — Predictive Features\n{symbol} | {timeframe}',
        color=COLORS['accent'], fontsize=13, fontweight='bold'
    )

    for idx, mtype in enumerate(move_types):
        row, col = divmod(idx, n_cols)
        ax = axes[row][col]

        ranked = [r for r in correlations[mtype] if abs(r['t_statistic']) >= 1.5][:top_n]
        if not ranked:
            ax.axis('off')
            continue

        features = [r['feature'][:28] for r in ranked]
        t_stats = [r['t_statistic'] for r in ranked]
        hit_pcts = [r['predictive_pct'] for r in ranked]

        colors = [COLORS['down'] if t < 0 else COLORS['up'] for t in t_stats]
        y_pos = range(len(features))

        bars = ax.barh(y_pos, t_stats, color=colors, alpha=0.85, height=0.65)

        # Hit% annotations
        for i, (bar, hp) in enumerate(zip(bars, hit_pcts)):
            x_pos = bar.get_width()
            ax.text(
                x_pos + (0.1 if x_pos >= 0 else -0.1),
                i,
                f'{hp:.0f}%',
                va='center', ha='left' if x_pos >= 0 else 'right',
                color=COLORS['text_dim'], fontsize=7,
            )

        ax.set_yticks(y_pos)
        ax.set_yticklabels(features, fontsize=7.5)
        ax.axvline(0, color=COLORS['text_dim'], linewidth=0.8)
        ax.axvline(2, color=COLORS['up'], linewidth=0.5, linestyle=':', alpha=0.5)
        ax.axvline(-2, color=COLORS['down'], linewidth=0.5, linestyle=':', alpha=0.5)
        ax.set_xlabel('t-statistic (Welch)', fontsize=8, color=COLORS['text_dim'])
        color_title = MOVE_COLORS.get(mtype, COLORS['yellow'])
        ax.set_title(f'{mtype}', color=color_title, fontsize=10, fontweight='bold')
        ax.grid(True, axis='x', alpha=0.2)

    # Hide empty axes
    for idx in range(len(move_types), n_rows * n_cols):
        r, c = divmod(idx, n_cols)
        axes[r][c].axis('off')

    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches='tight', facecolor=COLORS['bg'])
    plt.close(fig)
    return out_path


# ─── Chart 3: Cluster Summary ─────────────────────────────────────────────────

def cluster_chart(
    clusters: dict,
    symbol: str,
    timeframe: str,
    out_path: str,
) -> str:
    if not clusters:
        return None

    setup_dark_style()
    n = len(clusters)
    fig, axes = plt.subplots(1, n, figsize=(6 * n, 6))
    if n == 1:
        axes = [axes]

    fig.suptitle(
        f'PROBEBOT — Pattern Clusters\n{symbol} | {timeframe}',
        color=COLORS['accent'], fontsize=13, fontweight='bold'
    )

    cluster_palette = [COLORS['down'], COLORS['accent'], COLORS['yellow'],
                       COLORS['purple'], COLORS['orange'], COLORS['up']]

    for i, (cid, cluster) in enumerate(clusters.items()):
        ax = axes[i]
        feats = cluster['key_features'][:8]
        if not feats:
            ax.axis('off')
            continue

        names = [f['feature'][:20] for f in feats]
        cluster_vals = [f['cluster_mean'] for f in feats]
        other_vals = [f.get('other_mean', f.get('global_mean', 0)) for f in feats]
        y_pos = np.arange(len(names))
        bar_h = 0.35

        color_c = cluster_palette[i % len(cluster_palette)]
        ax.barh(y_pos + bar_h/2, cluster_vals, bar_h, label='This cluster',
                color=color_c, alpha=0.85)
        ax.barh(y_pos - bar_h/2, other_vals, bar_h, label='Other clusters',
                color=COLORS['text_dim'], alpha=0.5)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(names, fontsize=8)
        ax.axvline(0, color=COLORS['text_dim'], linewidth=0.7)
        ax.set_title(
            f"Cluster {cid}\n"
            f"{cluster['n']} events | {cluster['dominant_direction']}\n"
            f"Avg move: {cluster['avg_magnitude_pct']:+.2f}%",
            color=color_c, fontsize=9, fontweight='bold'
        )
        ax.legend(fontsize=7)
        ax.grid(True, axis='x', alpha=0.2)

    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches='tight', facecolor=COLORS['bg'])
    plt.close(fig)
    return out_path


# ─── Chart 4: MTF Drill-Down für einzelnes Event ─────────────────────────────

def drill_down_chart(
    movement: Movement,
    drill_down: dict,
    symbol: str,
    out_path: str,
) -> str:
    if not drill_down:
        return None

    setup_dark_style()
    timeframes = [tf for tf, data in drill_down.items() if isinstance(data, dict) and 'error' not in data]
    if not timeframes:
        return None

    fig, axes = plt.subplots(len(timeframes), 1, figsize=(14, 4 * len(timeframes)))
    if len(timeframes) == 1:
        axes = [axes]

    direction_sym = '▼' if movement.direction == 'DOWN' else '▲'
    move_color = MOVE_COLORS.get(movement.move_type, COLORS['yellow'])
    fig.suptitle(
        f'PROBEBOT — MTF Drill-Down\n'
        f'{symbol}  |  {movement.move_type}  |  {direction_sym} {movement.magnitude_pct:+.1f}%  '
        f'|  {str(movement.timestamp)[:16]}',
        color=move_color, fontsize=12, fontweight='bold'
    )

    for i, tf in enumerate(timeframes):
        ax = axes[i]
        level = drill_down[tf]
        confidence = level.get('entry_confidence', 0)
        entry_ts = level.get('entry_ts', 'N/A')
        precursors = level.get('precursors', [])
        signals = level.get('entry_signals', [])

        # Confidence gauge bar
        conf_color = COLORS['up'] if confidence >= 6 else COLORS['yellow'] if confidence >= 3 else COLORS['down']
        ax.barh(0, confidence, height=0.4, color=conf_color, alpha=0.85)
        ax.barh(0, 10, height=0.4, color=COLORS['grid'], alpha=0.5)
        ax.set_xlim(0, 10)
        ax.set_ylim(-2, 2)

        # Key metrics text
        rsi = level.get('rsi_14', 'N/A')
        adx = level.get('adx', 'N/A')
        entropy = level.get('entropy_20', 'N/A')
        hurst = level.get('hurst_60', 'N/A')
        regime = level.get('regime', 'N/A')

        metrics = (
            f"RSI={_fmt(rsi)}  ADX={_fmt(adx)}  "
            f"Entropy={_fmt(entropy)}  Hurst={_fmt(hurst)}  Regime={regime}"
        )
        ax.text(0.5, 0.55, metrics, transform=ax.transAxes,
                color=COLORS['text_dim'], fontsize=8, ha='center')

        # Entry time
        ax.text(0.5, 0.85, f"Entry: {str(entry_ts)[:16]}", transform=ax.transAxes,
                color=COLORS['accent'], fontsize=9, ha='center', fontweight='bold')

        # Precursors
        if precursors:
            prec_text = '  |  '.join(precursors[:3])
            ax.text(0.02, -0.8, f"⚡ {prec_text}", transform=ax.transAxes,
                    color=COLORS['yellow'], fontsize=7.5, va='center')

        # Signals
        if signals:
            sig_text = '  ✓  '.join(s.split('(')[0].strip() for s in signals[:4])
            ax.text(0.02, -1.4, f"✓ {sig_text}", transform=ax.transAxes,
                    color=COLORS['up'], fontsize=7.5, va='center')

        # Title
        ax.set_title(
            f'{tf}  |  Confidence: {confidence}/10',
            color=conf_color, fontsize=10, fontweight='bold', loc='left'
        )
        ax.set_yticks([])
        ax.set_xticks(range(11))
        ax.set_xticklabels([str(x) for x in range(11)], fontsize=8)
        ax.set_xlabel('Entry Confidence Score (0=no signal, 10=perfect entry)', fontsize=8)
        ax.grid(True, axis='x', alpha=0.2)

        # Vertical confidence marker
        ax.axvline(confidence, color=conf_color, linewidth=2, alpha=0.6)
        ax.text(confidence + 0.1, 0.3, f'{confidence}/10',
                color=conf_color, fontsize=10, fontweight='bold', va='center')

    try:
        plt.tight_layout()
    except Exception:
        pass
    fig.savefig(out_path, dpi=130, bbox_inches='tight', facecolor=COLORS['bg'])
    plt.close(fig)
    return out_path


# ─── Chart 5: Pre-condition Fingerprint (Radar) ───────────────────────────────

def fingerprint_chart(
    correlations: Dict[str, List[dict]],
    symbol: str,
    timeframe: str,
    out_path: str,
    top_features: int = 8,
) -> str:
    """Radar chart showing which features are elevated/suppressed before each move type."""
    move_types = [mt for mt, data in correlations.items()
                  if data and len([r for r in data if abs(r['t_statistic']) >= 1.5]) >= 3]
    if not move_types:
        return None

    setup_dark_style()
    fig, axes = plt.subplots(
        1, len(move_types),
        figsize=(5.5 * len(move_types), 5.5),
        subplot_kw=dict(polar=True),
    )
    if len(move_types) == 1:
        axes = [axes]

    fig.suptitle(
        f'PROBEBOT — Pre-Condition Fingerprint\n{symbol} | {timeframe}',
        color=COLORS['accent'], fontsize=12, fontweight='bold'
    )

    for i, mtype in enumerate(move_types):
        ax = axes[i]
        ranked = sorted(correlations[mtype], key=lambda r: abs(r['t_statistic']), reverse=True)
        top = [r for r in ranked if abs(r['t_statistic']) >= 1.5][:top_features]
        if not top:
            ax.axis('off')
            continue

        labels = [r['feature'][:16] for r in top]
        # Normalize lift to [-1, 1]
        lifts = np.array([np.clip(r['lift_factor'], -2, 2) / 2 for r in top])
        # Shift to [0, 1] for radar
        values = (lifts + 1) / 2

        N = len(labels)
        angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
        values_plot = values.tolist() + [values[0]]
        angles += angles[:1]

        color = MOVE_COLORS.get(mtype, COLORS['yellow'])
        ax.plot(angles, values_plot, color=color, linewidth=2)
        ax.fill(angles, values_plot, color=color, alpha=0.2)

        # Reference circles
        ax.plot(angles, [0.5] * (N + 1), color=COLORS['text_dim'],
                linewidth=0.7, linestyle='--', alpha=0.5)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, fontsize=7.5, color=COLORS['text'])
        ax.set_yticks([0.25, 0.5, 0.75])
        ax.set_yticklabels(['below avg', 'avg', 'above avg'], fontsize=6.5, color=COLORS['text_dim'])
        ax.set_ylim(0, 1)
        ax.set_facecolor(COLORS['bg'])
        ax.spines['polar'].set_color(COLORS['grid'])
        ax.grid(color=COLORS['grid'], alpha=0.4)
        ax.set_title(mtype, color=color, fontsize=10, fontweight='bold', pad=15)

    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches='tight', facecolor=COLORS['bg'])
    plt.close(fig)
    return out_path


# ─── Chart 6: Live Trade Entry ─────────────────────────────────────────────────

def _extract_ref_level(source: str):
    """Parst 'swing_low=97.0000' oder '1.5×ATR14=2.3400' aus einem sl_source/
    tp_source-String (siehe signal_logic.py) -> (name, value) oder None."""
    import re
    m = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([\d.]+)\s*$', source or '')
    if not m:
        return None
    try:
        return m.group(1), float(m.group(2))
    except ValueError:
        return None


def trade_entry_chart(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    side: str,
    entry_price: float,
    sl_price: float,
    tp_price: Optional[float],
    move_type: str,
    strategy: str,
    score: float,
    hit_rate: float,
    n_met: int,
    n_total: int,
    sl_source: str,
    tp_source: str,
    out_path: str,
    n_candles: int = 40,
) -> str:
    """
    Kerzendiagramm fuer einen live eroeffneten Trade -- wird direkt nach
    Entry+SL/TP-Platzierung per Telegram verschickt (trade_manager.py).
    Zeigt den Entry-Grund optisch: Signal-Kerze hervorgehoben, Entry/SL/TP-
    Linien, und -- falls sl_source/tp_source einen Feature-Wert enthalten
    (z.B. 'swing_low=97.0000') -- eine Referenzlinie fuer genau dieses Level.
    """
    setup_dark_style()
    d = df[['open', 'high', 'low', 'close']].iloc[-n_candles:].reset_index(drop=True)
    n = len(d)
    if n == 0:
        return None

    fig, ax = plt.subplots(figsize=(14, 7))
    fig.patch.set_facecolor(COLORS['bg'])
    ax.set_facecolor(COLORS['bg'])

    y_min = float(d['low'].min())
    y_max = float(d['high'].max())
    for p in filter(None, [entry_price, sl_price, tp_price]):
        y_min = min(y_min, float(p) * 0.999)
        y_max = max(y_max, float(p) * 1.001)
    margin = (y_max - y_min) * 0.14 if y_max > y_min else y_max * 0.02
    y_lo, y_hi = y_min - margin, y_max + margin
    ax.set_xlim(-1, n + 1)
    ax.set_ylim(y_lo, y_hi)

    def _in_range(price):
        return price and y_lo < float(price) < y_hi

    # ── Signal-Kerze hervorheben (letzte Kerze = Kerze auf der das Signal
    # ausgeloest hat) — probebots Entsprechung zu dnabots Genome-Pattern-Band
    sig_color = MOVE_COLORS.get(move_type, COLORS['yellow'])
    ax.axvspan(n - 1.5, n - 0.5, color=sig_color, alpha=0.12, zorder=1)
    ax.text(n - 1, y_lo + (y_hi - y_lo) * 0.01, move_type,
            color=sig_color, fontsize=7.5, ha='center', va='bottom',
            fontfamily='monospace', fontweight='bold', alpha=0.9, zorder=7)

    # ── Kerzen ──
    for i in range(n):
        o, h, l, c = d['open'].iloc[i], d['high'].iloc[i], d['low'].iloc[i], d['close'].iloc[i]
        color = COLORS['up'] if c >= o else COLORS['down']
        ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=2)
        body_h = max(abs(c - o), (h - l) * 0.005 if h > l else h * 0.0005)
        ax.bar(i, body_h, bottom=min(o, c), color=color, width=0.6, linewidth=0, zorder=3)

    # ── Risiko/Reward-Zonen ──
    if _in_range(sl_price):
        ax.axhspan(min(sl_price, entry_price), max(sl_price, entry_price),
                   color=COLORS['down'], alpha=0.07, zorder=1)
    if _in_range(tp_price):
        ax.axhspan(min(tp_price, entry_price), max(tp_price, entry_price),
                   color=COLORS['up'], alpha=0.07, zorder=1)

    # ── Entry/SL/TP Preis-Tags ──
    def _price_tag(price, label, color):
        if not _in_range(price):
            return
        ax.axhline(price, color=color, linewidth=1.5, linestyle='--', zorder=6)
        ax.text(n - 0.3, price, f'  {label}: {price:.6g}  ',
                color=COLORS['bg'], fontsize=8.5, va='center', ha='right',
                fontweight='bold', zorder=8,
                bbox=dict(facecolor=color, edgecolor='none', alpha=0.92,
                          boxstyle='square,pad=0.25'))

    _price_tag(tp_price, 'TP', COLORS['up'])
    _price_tag(entry_price, 'Entry', COLORS['yellow'])
    _price_tag(sl_price, 'SL', COLORS['down'])

    # ── Referenzlinien fuer die tatsaechlichen SL/TP-Quellen (der optische
    # "Grund" fuer den Trade — z.B. der swing_low den der SL nutzt) ──
    for source in (sl_source, tp_source):
        ref = _extract_ref_level(source)
        if ref and _in_range(ref[1]):
            name, val = ref
            ax.axhline(val, color=COLORS['purple'], linewidth=0.8,
                       linestyle=':', alpha=0.7, zorder=5)
            ax.text(0.3, val, f'{name}', color=COLORS['purple'], fontsize=7,
                    va='bottom', ha='left', fontfamily='monospace', alpha=0.85, zorder=8)

    # ── Infobox ──
    side_label = 'LONG ▲' if side == 'long' else 'SHORT ▼'
    sl_pct = abs(entry_price - sl_price) / entry_price * 100 if sl_price and entry_price else 0
    tp_pct = abs(tp_price - entry_price) / entry_price * 100 if tp_price and entry_price else sl_pct * 1.5
    rr = tp_pct / sl_pct if sl_pct > 0 else 0
    info_lines = [
        f"{side_label}   R:R 1:{rr:.1f}",
        f"Strategie: {strategy}   |   {move_type}",
        f"Score: {score:.1f}   Hit: {hit_rate:.0%} ({n_met}/{n_total})",
        "─" * 30,
        f"SL:  {sl_source}",
        f"TP:  {tp_source}",
    ]
    ax.text(0.01, 0.98, '\n'.join(info_lines),
            transform=ax.transAxes, fontsize=8, va='top', ha='left',
            color=COLORS['text'], fontfamily='monospace',
            bbox=dict(facecolor='#161b22', edgecolor=COLORS['grid'],
                      alpha=0.9, boxstyle='round,pad=0.5'), zorder=9)

    ax.set_title(
        f"PROBEBOT  |  {symbol}  {timeframe}  |  {side_label}  |  letzte {n} Kerzen",
        color=COLORS['accent'], fontsize=11, fontweight='bold', pad=10,
    )
    ax.tick_params(colors=COLORS['text_dim'], labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(COLORS['grid'])
    ax.set_xticks([])
    ax.yaxis.tick_right()
    ax.grid(axis='y', color=COLORS['grid'], linewidth=0.4, alpha=0.4, zorder=0)

    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches='tight', facecolor=COLORS['bg'])
    plt.close(fig)
    return out_path


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fmt(val) -> str:
    if val is None:
        return 'N/A'
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


def save_chart(generator_fn, *args, prefix: str = 'chart', **kwargs) -> Optional[str]:
    """Safely call a chart generator, return path or None on error."""
    try:
        out_dir = Path(__file__).parent.parent.parent.parent / 'artifacts' / 'charts'
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(out_dir / f'{prefix}.png')
        result = generator_fn(*args, out_path=out_path, **kwargs)
        return result
    except Exception as e:
        import traceback
        print(f"  [charts] Error generating {prefix}: {e}")
        traceback.print_exc()
        return None
