"""
Movement detector: finds significant price events in OHLCV+feature data.

Movement types:
  IMPULSE_UP / IMPULSE_DOWN   — single-candle spike > N×ATR
  BREAKOUT_UP / BREAKDOWN      — close breaks above/below N-bar consolidation
  REVERSAL_UP / REVERSAL_DOWN  — trend flip after N consecutive candles
  SQUEEZE_RELEASE_UP/DOWN      — volatility compression then expansion
  ACCELERATION_UP/DOWN         — trend sharply accelerates (momentum surge)
  GAP_UP / GAP_DOWN            — open gapped significantly vs prior close
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Movement:
    idx: int
    timestamp: pd.Timestamp
    move_type: str          # e.g. 'BREAKDOWN'
    direction: str          # 'UP' or 'DOWN'
    magnitude_pct: float    # percentage move
    atr_multiple: float     # how many ATRs was this move?
    context: dict = field(default_factory=dict)   # extra fields (regime, score, etc.)
    preconditions: dict = field(default_factory=dict)  # features N candles before
    during: dict = field(default_factory=dict)    # features AT the move candle


class MovementDetector:
    def __init__(
        self,
        atr_impulse: float = 2.0,
        breakout_bars: int = 20,
        reversal_min_run: int = 5,
        squeeze_atr_z_threshold: float = -1.0,
        gap_pct: float = 0.005,
        lookback: int = 10,
    ):
        self.atr_impulse = atr_impulse
        self.breakout_bars = breakout_bars
        self.reversal_min_run = reversal_min_run
        self.squeeze_atr_z_threshold = squeeze_atr_z_threshold
        self.gap_pct = gap_pct
        self.lookback = lookback

    def detect(self, df: pd.DataFrame) -> List[Movement]:
        movements: List[Movement] = []
        atr = df.get('atr_14', (df['high'] - df['low']).rolling(14).mean())
        close = df['close']
        open_ = df['open']

        for i in range(max(self.lookback + self.breakout_bars, 50), len(df)):
            row = df.iloc[i]
            ts = df.iloc[i]['timestamp'] if 'timestamp' in df.columns else df.index[i]
            a = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else 0.001
            c_now = float(close.iloc[i])
            c_prev = float(close.iloc[i - 1])
            candle_move = (c_now - c_prev) / (c_prev + 1e-10)
            candle_range = float(df['high'].iloc[i] - df['low'].iloc[i])

            move_type = None
            direction = 'UP' if candle_move >= 0 else 'DOWN'

            # 1. IMPULSE: single candle > atr_impulse * ATR
            if candle_range > self.atr_impulse * a:
                if candle_move > 0:
                    move_type = 'IMPULSE_UP'
                else:
                    move_type = 'IMPULSE_DOWN'

            # 2. BREAKOUT above N-bar high
            elif not move_type:
                prev_high = float(close.iloc[i - self.breakout_bars:i].max())
                prev_low = float(close.iloc[i - self.breakout_bars:i].min())
                if c_now > prev_high * 1.001 and c_prev <= prev_high:
                    move_type = 'BREAKOUT_UP'
                    direction = 'UP'
                elif c_now < prev_low * 0.999 and c_prev >= prev_low:
                    move_type = 'BREAKDOWN'
                    direction = 'DOWN'

            # 3. REVERSAL: after N-run of same direction
            if not move_type:
                consec_bull = int(df['consec_bull'].iloc[i - 1]) if 'consec_bull' in df.columns else 0
                consec_bear = int(df['consec_bear'].iloc[i - 1]) if 'consec_bear' in df.columns else 0
                if candle_move < -0.005 and consec_bull >= self.reversal_min_run:
                    move_type = 'REVERSAL_DOWN'
                    direction = 'DOWN'
                elif candle_move > 0.005 and consec_bear >= self.reversal_min_run:
                    move_type = 'REVERSAL_UP'
                    direction = 'UP'

            # 4. SQUEEZE RELEASE: preceded by low ATR, now expanding
            if not move_type:
                atr_z_prev = float(df['atr_z'].iloc[i - 3]) if 'atr_z' in df.columns else 0
                atr_z_now = float(df['atr_z'].iloc[i]) if 'atr_z' in df.columns else 0
                if atr_z_prev < self.squeeze_atr_z_threshold and atr_z_now > 0.5:
                    if candle_move > 0.003:
                        move_type = 'SQUEEZE_RELEASE_UP'
                        direction = 'UP'
                    elif candle_move < -0.003:
                        move_type = 'SQUEEZE_RELEASE_DOWN'
                        direction = 'DOWN'

            # 5. ACCELERATION: strong trend + sudden speed increase
            if not move_type:
                energy_z = float(df['energy_z'].iloc[i]) if 'energy_z' in df.columns else 0
                if energy_z > 3.0 and candle_move > 0.01:
                    move_type = 'ACCELERATION_UP'
                    direction = 'UP'
                elif energy_z > 3.0 and candle_move < -0.01:
                    move_type = 'ACCELERATION_DOWN'
                    direction = 'DOWN'

            # 6. GAP
            if not move_type:
                gap = (float(open_.iloc[i]) - float(close.iloc[i - 1])) / (float(close.iloc[i - 1]) + 1e-10)
                if gap > self.gap_pct:
                    move_type = 'GAP_UP'
                    direction = 'UP'
                elif gap < -self.gap_pct:
                    move_type = 'GAP_DOWN'
                    direction = 'DOWN'

            if move_type:
                atr_mult = candle_range / (a + 1e-10)
                context = self._context(df, i)
                movements.append(Movement(
                    idx=i,
                    timestamp=ts,
                    move_type=move_type,
                    direction=direction,
                    magnitude_pct=round(candle_move * 100, 4),
                    atr_multiple=round(atr_mult, 2),
                    context=context,
                ))

        # Remove duplicate detections on same candle (keep most specific type)
        movements = _deduplicate(movements)
        return movements

    def _context(self, df: pd.DataFrame, i: int) -> dict:
        row = df.iloc[i]
        ctx = {}
        for key in ['regime', 'trend_score', 'momentum_score', 'move_readiness',
                    'adx', 'rsi_14', 'entropy_20', 'hurst_60', 'atr_pct',
                    'volume_ratio', 'ema_alignment', 'supertrend_dir']:
            if key in df.columns:
                val = row[key]
                ctx[key] = float(val) if not isinstance(val, str) else val
        return ctx


def _deduplicate(movements: List[Movement]) -> List[Movement]:
    """Keep only the most specific (highest priority) movement per candle index."""
    priority = {
        'SQUEEZE_RELEASE_DOWN': 0, 'SQUEEZE_RELEASE_UP': 0,
        'BREAKDOWN': 1, 'BREAKOUT_UP': 1,
        'REVERSAL_DOWN': 2, 'REVERSAL_UP': 2,
        'IMPULSE_DOWN': 3, 'IMPULSE_UP': 3,
        'ACCELERATION_DOWN': 4, 'ACCELERATION_UP': 4,
        'GAP_DOWN': 5, 'GAP_UP': 5,
    }
    by_idx = {}
    for m in movements:
        if m.idx not in by_idx:
            by_idx[m.idx] = m
        else:
            if priority.get(m.move_type, 99) < priority.get(by_idx[m.idx].move_type, 99):
                by_idx[m.idx] = m
    return sorted(by_idx.values(), key=lambda m: m.idx)
