#!/bin/bash
# run_live_bot.sh — probebot Live Trading (manuell oder Cron)
#
# Manuell:   bash run_live_bot.sh
# Cron 1h:   0 * * * * cd /pfad/zu/probebot && bash run_live_bot.sh >> logs/cron_live_bot.log 2>&1
# Cron 4h:   0 */4 * * * cd /pfad/zu/probebot && bash run_live_bot.sh >> logs/cron_live_bot.log 2>&1
# Cron 15m:  */15 * * * * cd /pfad/zu/probebot && bash run_live_bot.sh >> logs/cron_live_bot.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [ ! -f "$PYTHON" ]; then
    echo "FEHLER: .venv nicht gefunden. Erst install.sh ausfuehren."
    exit 1
fi

export PYTHONPATH="$SCRIPT_DIR/src"
"$PYTHON" "$SCRIPT_DIR/master_runner.py"
