#!/bin/bash
# Probebot — Market Forensics Runner

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR/src"

echo "=== PROBEBOT — Market Forensics ==="

# Default: full scan on BTC 1D
python -m probebot.run \
  --symbol "BTC/USDT:USDT" \
  --timeframe 1d \
  --start_date 2022-01-01 \
  --end_date 2025-01-01 \
  --mode full \
  --drill_down \
  --top_n 5 \
  "$@"
