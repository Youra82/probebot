#!/bin/bash
# run_live.sh — probebot Live-Scanner
# Prüft ob der Markt GERADE eine signifikante Bewegung macht und erklärt die Ursachen.
#
# Manuell:
#   bash run_live.sh
#
# Als Cron (alle 15 Minuten auf 1h-Daten scannen):
#   */15 * * * * cd /pfad/zu/probebot && bash run_live.sh >> logs/live_cron.log 2>&1
#
# Als Cron (alle 4 Stunden auf 4h-Daten scannen):
#   0 */4 * * * cd /pfad/zu/probebot && bash run_live.sh --timeframe 4h >> logs/live_cron.log 2>&1

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [ ! -f "$PYTHON" ]; then
    echo -e "${RED}Fehler: .venv nicht gefunden. Erst install.sh ausfuehren.${NC}"
    exit 1
fi

source "$SCRIPT_DIR/.venv/bin/activate"
export PYTHONPATH="$SCRIPT_DIR/src"

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "       probebot — Live Scanner"
echo -e "${BLUE}=======================================================${NC}"
echo ""

# ── Parameter aus settings.json oder Defaults ────────────────────────────────
SETTINGS="$SCRIPT_DIR/settings.json"

if [ -f "$SETTINGS" ]; then
    SYMBOL=$(python3 -c "import json; d=json.load(open('$SETTINGS')); print(d.get('symbol','BTC/USDT:USDT'))")
    TIMEFRAME=$(python3 -c "import json; d=json.load(open('$SETTINGS')); print(d.get('primary_timeframe','1h'))")
    MIN_MOVE=$(python3 -c "import json; d=json.load(open('$SETTINGS')); print(d.get('min_move_pct', 1.5))")
    SCAN_CANDLES=$(python3 -c "import json; d=json.load(open('$SETTINGS')); print(d.get('scan_candles', 5))")
else
    SYMBOL="BTC/USDT:USDT"
    TIMEFRAME="1h"
    MIN_MOVE="1.5"
    SCAN_CANDLES="5"
fi

# CLI-Parameter überschreiben settings.json
while [[ $# -gt 0 ]]; do
    case "$1" in
        --symbol)      SYMBOL="$2";       shift 2 ;;
        --timeframe)   TIMEFRAME="$2";    shift 2 ;;
        --min_move)    MIN_MOVE="$2";     shift 2 ;;
        --candles)     SCAN_CANDLES="$2"; shift 2 ;;
        --no_telegram) NO_TG="--no_telegram"; shift ;;
        *) shift ;;
    esac
done

echo -e "${CYAN}Symbol:         ${GREEN}$SYMBOL${NC}"
echo -e "${CYAN}Timeframe:      ${GREEN}$TIMEFRAME${NC}"
echo -e "${CYAN}Min-Move:       ${GREEN}${MIN_MOVE}%${NC}"
echo -e "${CYAN}Letzte Kerzen:  ${GREEN}$SCAN_CANDLES${NC}"
echo ""

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/live_$(date +%Y%m%d_%H%M%S).log"

echo -e "${YELLOW}Starte Live-Scan...${NC}"
echo "Run: $(date)" > "$LOGFILE"

$PYTHON -m probebot.run \
    --symbol "$SYMBOL" \
    --timeframe "$TIMEFRAME" \
    --min_move_pct "$MIN_MOVE" \
    --scan_candles "$SCAN_CANDLES" \
    --mode live \
    $NO_TG \
    2>&1 | tee -a "$LOGFILE"

EXIT_CODE=${PIPESTATUS[0]}

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}Live-Scan abgeschlossen. Log: $LOGFILE${NC}"
else
    echo -e "${RED}Fehler beim Live-Scan (Exit $EXIT_CODE). Siehe $LOGFILE${NC}"
fi

deactivate
echo ""
