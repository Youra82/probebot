"""
Probebot — Interactive Plotly Chart (wie dnabot)

Panels:
  1. Candlestick + Regime-Hintergrund + Trade-Marker + SL/TP-Linien + Equity (2. Y-Achse)
  2. Volumen
  3. ATR
  4. ADX
  5. Momentum Score
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(ROOT / 'src'))

MOVE_COLORS = {
    'BREAKDOWN':            '#ef5350',
    'IMPULSE_DOWN':         '#ff6b6b',
    'REVERSAL_DOWN':        '#ffa657',
    'SQUEEZE_RELEASE_DOWN': '#ff8c42',
    'ACCELERATION_DOWN':    '#e84393',
    'GAP_DOWN':             '#d29922',
    'BREAKOUT_UP':          '#26a69a',
    'IMPULSE_UP':           '#63f5a1',
    'REVERSAL_UP':          '#58a6ff',
    'SQUEEZE_RELEASE_UP':   '#4ac6e8',
    'ACCELERATION_UP':      '#bc8cff',
    'GAP_UP':               '#ffd700',
}

_REGIME_FILL = {
    'TREND': 'rgba(38,166,154,0.20)',
    'RANGE': 'rgba(255,167,38,0.18)',
    'CHAOS': 'rgba(239,83,80,0.22)',
}


def create_chart(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    trades: list,
    stats: dict,
    start_capital: float,
) -> object:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print("  [chart] plotly nicht installiert. pip install plotly")
        return None

    ts = pd.to_datetime(df['timestamp']) if 'timestamp' in df.columns else pd.RangeIndex(len(df))

    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        specs=[
            [{'secondary_y': True}],
            [{'secondary_y': False}],
            [{'secondary_y': False}],
            [{'secondary_y': False}],
            [{'secondary_y': False}],
        ],
        vertical_spacing=0.022,
        row_heights=[0.42, 0.11, 0.16, 0.16, 0.15],
        subplot_titles=['', 'Volumen', 'ATR', 'ADX', 'Momentum Score'],
    )

    # ── Regime-Hintergrund ───────────────────────────────────────────────────
    if 'regime' in df.columns:
        regimes  = df['regime'].fillna('RANGE').tolist()
        ts_list  = ts.tolist()
        prev_reg = None
        blk_start = None
        for i, reg in enumerate(regimes):
            if reg != prev_reg:
                if prev_reg and _REGIME_FILL.get(prev_reg) and blk_start is not None:
                    fig.add_vrect(
                        x0=blk_start, x1=ts_list[i],
                        fillcolor=_REGIME_FILL[prev_reg],
                        layer='below', line_width=0, row=1, col=1,
                    )
                blk_start, prev_reg = ts_list[i], reg
        if prev_reg and _REGIME_FILL.get(prev_reg) and blk_start is not None:
            fig.add_vrect(
                x0=blk_start, x1=ts_list[-1],
                fillcolor=_REGIME_FILL[prev_reg],
                layer='below', line_width=0, row=1, col=1,
            )
        for label, color in [('TREND', '#26a69a'), ('RANGE', '#ffa726'), ('CHAOS', '#ef5350')]:
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode='markers',
                marker=dict(symbol='square', size=10, color=color),
                name=label, showlegend=True,
            ), row=1, col=1, secondary_y=False)

    # ── Panel 1: Candlesticks ────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=ts,
        open=df['open'], high=df['high'],
        low=df['low'],   close=df['close'],
        name='OHLC',
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350',
        showlegend=True,
    ), row=1, col=1, secondary_y=False)

    # ── Trade-Marker & SL/TP-Linien ─────────────────────────────────────────
    entry_long_x, entry_long_y, entry_long_txt   = [], [], []
    entry_short_x, entry_short_y, entry_short_txt = [], [], []
    exit_tp_x, exit_tp_y = [], []
    exit_sl_x, exit_sl_y = [], []
    exit_to_x, exit_to_y = [], []

    for t in trades:
        et  = pd.to_datetime(t['entry_ts'])
        xt  = pd.to_datetime(t['close_ts'])
        tip = (
            f"Typ: {t.get('move_type','')}<br>"
            f"Score: {t.get('score', 0):.1f}<br>"
            f"Dir: {t['direction']}<br>"
            f"Entry: {t['entry_price']:.4f}<br>"
            f"Exit:  {t['close_price']:.4f}<br>"
            f"PnL:   {t['pnl']:+.4f}  ({t.get('close_reason','')})"
        )

        if t['direction'] == 'LONG':
            entry_long_x.append(et); entry_long_y.append(t['entry_price'])
            entry_long_txt.append(tip)
        else:
            entry_short_x.append(et); entry_short_y.append(t['entry_price'])
            entry_short_txt.append(tip)

        cr = t.get('close_reason', '')
        if cr == 'TP':
            exit_tp_x.append(xt); exit_tp_y.append(t['close_price'])
        elif cr == 'SL':
            exit_sl_x.append(xt); exit_sl_y.append(t['close_price'])
        else:
            exit_to_x.append(xt); exit_to_y.append(t['close_price'])

        sl = t.get('sl')
        tp = t.get('tp')
        if sl is not None:
            fig.add_shape(type='line', x0=et, x1=xt, y0=sl, y1=sl,
                          line=dict(color='rgba(239,68,68,0.45)', width=1, dash='dot'))
        if tp is not None:
            fig.add_shape(type='line', x0=et, x1=xt, y0=tp, y1=tp,
                          line=dict(color='rgba(34,197,94,0.45)', width=1, dash='dot'))

    if entry_long_x:
        fig.add_trace(go.Scatter(
            x=entry_long_x, y=entry_long_y, mode='markers',
            marker=dict(color='#26a69a', symbol='triangle-up', size=14,
                        line=dict(width=1, color='#ffffff')),
            name='Entry Long', text=entry_long_txt,
            hovertemplate='%{text}<extra>Entry Long</extra>',
        ), row=1, col=1, secondary_y=False)

    if entry_short_x:
        fig.add_trace(go.Scatter(
            x=entry_short_x, y=entry_short_y, mode='markers',
            marker=dict(color='#ffa726', symbol='triangle-down', size=14,
                        line=dict(width=1, color='#ffffff')),
            name='Entry Short', text=entry_short_txt,
            hovertemplate='%{text}<extra>Entry Short</extra>',
        ), row=1, col=1, secondary_y=False)

    if exit_tp_x:
        fig.add_trace(go.Scatter(
            x=exit_tp_x, y=exit_tp_y, mode='markers',
            marker=dict(color='#00bcd4', symbol='circle', size=11,
                        line=dict(width=1, color='#ffffff')),
            name='Exit TP ✓',
        ), row=1, col=1, secondary_y=False)

    if exit_sl_x:
        fig.add_trace(go.Scatter(
            x=exit_sl_x, y=exit_sl_y, mode='markers',
            marker=dict(color='#ef5350', symbol='x', size=11,
                        line=dict(width=2, color='#ef5350')),
            name='Exit SL ✗',
        ), row=1, col=1, secondary_y=False)

    if exit_to_x:
        fig.add_trace(go.Scatter(
            x=exit_to_x, y=exit_to_y, mode='markers',
            marker=dict(color='#9e9e9e', symbol='square', size=9),
            name='Exit Timeout',
        ), row=1, col=1, secondary_y=False)

    # ── Equity-Kurve (secondary Y) ───────────────────────────────────────────
    sorted_trades = sorted(trades, key=lambda x: x['entry_ts'])
    if sorted_trades:
        eq_x = [pd.to_datetime(ts.iloc[0])]
        eq_y = [start_capital]
        for t in sorted_trades:
            eq_x.append(pd.to_datetime(t['close_ts']))
            eq_y.append(t['capital_after'])

        fig.add_trace(go.Scatter(
            x=eq_x, y=eq_y,
            name='Equity',
            line=dict(color='#5c9bd6', width=1.5),
            hovertemplate='Equity: %{y:.2f} USDT<extra></extra>',
        ), row=1, col=1, secondary_y=True)

    # ── Panel 2: Volumen ─────────────────────────────────────────────────────
    if 'volume' in df.columns:
        vol_colors = ['#26a69a' if c >= o else '#ef5350'
                      for c, o in zip(df['close'], df['open'])]
        fig.add_trace(go.Bar(
            x=ts, y=df['volume'],
            marker_color=vol_colors,
            name='Volumen', showlegend=False, opacity=0.65,
            hovertemplate='Vol: %{y:,.0f}<extra></extra>',
        ), row=2, col=1)

    # ── Panel 3: ATR ─────────────────────────────────────────────────────────
    atr_col = next((c for c in ['atr_14', 'atr_7', 'atr_pct'] if c in df.columns), None)
    if atr_col:
        atr_vals = df[atr_col]
        atr_ma   = atr_vals.rolling(50, min_periods=10).mean().fillna(atr_vals)
        fig.add_trace(go.Scatter(
            x=ts, y=atr_ma, mode='lines',
            line=dict(color='rgba(255,167,38,0.5)', width=1.2, dash='dot'),
            name='ATR-MA(50)', showlegend=False,
        ), row=3, col=1)
        fig.add_trace(go.Scatter(
            x=ts, y=atr_vals, mode='lines',
            line=dict(color='#42a5f5', width=1.5),
            fill='tonexty', fillcolor='rgba(66,165,245,0.08)',
            name='ATR', showlegend=False,
        ), row=3, col=1)
        # Signal-Punkte auf ATR
        for t in trades:
            et  = pd.to_datetime(t['entry_ts'])
            idx = (ts - et).abs().argmin()
            atr_v = float(atr_vals.iloc[idx])
            color = '#26a69a' if t['direction'] == 'LONG' else '#ffa726'
            fig.add_trace(go.Scatter(
                x=[et], y=[atr_v], mode='markers',
                marker=dict(symbol='circle-open', size=8, color=color,
                            line=dict(width=2)),
                showlegend=False,
                hovertemplate=f"Signal: {t.get('move_type','')}<extra></extra>",
            ), row=3, col=1)

    # ── Panel 4: ADX ─────────────────────────────────────────────────────────
    if 'adx' in df.columns:
        fig.add_trace(go.Scatter(
            x=ts, y=df['adx'], mode='lines',
            line=dict(color='#ce93d8', width=1.5),
            fill='tozeroy', fillcolor='rgba(206,147,216,0.08)',
            name='ADX', showlegend=False,
            hovertemplate='ADX: %{y:.2f}<extra></extra>',
        ), row=4, col=1)
        fig.add_hline(y=25.0, line_dash='dot', line_color='rgba(38,166,154,0.55)', row=4, col=1)
        fig.add_hline(y=20.0, line_dash='dot', line_color='rgba(255,167,38,0.55)', row=4, col=1)

    # ── Panel 5: Momentum Score ───────────────────────────────────────────────
    mom_col = next((c for c in ['momentum_score', 'move_readiness', 'trend_score']
                    if c in df.columns), None)
    if mom_col:
        fig.add_trace(go.Scatter(
            x=ts, y=df[mom_col], mode='lines',
            line=dict(color='#ffa657', width=1.3),
            fill='tozeroy', fillcolor='rgba(255,166,87,0.07)',
            name=mom_col, showlegend=False,
            hovertemplate=f'{mom_col}: %{{y:.2f}}<extra></extra>',
        ), row=5, col=1)
        fig.add_hline(y=0, line_dash='dot', line_color='rgba(255,255,255,0.3)', row=5, col=1)

    # ── Layout ───────────────────────────────────────────────────────────────
    pnl    = stats.get('pnl_pct', 0)
    title  = (
        f"{symbol} {timeframe}  |  "
        f"Trades: {stats.get('n_trades', len(trades))}  |  "
        f"WR: {stats.get('win_rate', 0):.1f}%  |  "
        f"PnL: {'+' if pnl >= 0 else ''}{pnl:.1f}%  |  "
        f"MaxDD: {stats.get('max_drawdown', 0):.1f}%  |  "
        f"Sharpe: {stats.get('sharpe', 0):.2f}"
    )

    fig.update_layout(
        title=dict(text=title, font=dict(size=13), x=0.5, xanchor='center'),
        height=1100,
        hovermode='x unified',
        template='plotly_dark',
        dragmode='zoom',
        xaxis_rangeslider_visible=False,
        legend=dict(orientation='h', yanchor='bottom', y=1.01,
                    xanchor='center', x=0.5, font=dict(size=11)),
        margin=dict(l=60, r=70, t=80, b=40),
        yaxis2=dict(title='Equity (USDT)', showgrid=False,
                    tickfont=dict(color='#5c9bd6'),
                    title_font=dict(color='#5c9bd6')),
    )

    fig.update_yaxes(title_text='Preis', row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text='Vol',   row=2, col=1)
    fig.update_yaxes(title_text='ATR',   row=3, col=1)
    fig.update_yaxes(title_text='ADX',   row=4, col=1)
    fig.update_yaxes(title_text='Mom',   row=5, col=1)

    for r in range(1, 6):
        fig.update_xaxes(rangeslider_visible=False, row=r, col=1)

    return fig
