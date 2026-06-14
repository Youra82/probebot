#!/bin/bash
# run_pipeline.sh — probebot Market Forensics Pipeline

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [ ! -f "$PYTHON" ]; then
    echo -e "${RED}FEHLER: .venv nicht gefunden. Erst install.sh ausfuehren!${NC}"
    exit 1
fi
source "$SCRIPT_DIR/.venv/bin/activate"

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "       probebot — Market Forensics Pipeline"
echo -e "${BLUE}=======================================================${NC}"
echo ""

# ── Symbol ──────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL=$(python3 -c "import json; print(json.load(open('settings.json')).get('symbol','BTC/USDT:USDT'))" 2>/dev/null || echo "BTC/USDT:USDT")
echo -e "${YELLOW}Symbol (Futures-Format: BTC/USDT:USDT | ETH/USDT:USDT | SOL/USDT:USDT):${NC}"
read -p "Symbol [Standard: $DEFAULT_SYMBOL]: " SYMBOL_INPUT
SYMBOL_INPUT="${SYMBOL_INPUT//[$'\r\n']/}"
SYMBOL="${SYMBOL_INPUT:-$DEFAULT_SYMBOL}"

# ── Timeframe ────────────────────────────────────────────────────────────────
DEFAULT_TF=$(python3 -c "import json; print(json.load(open('settings.json')).get('primary_timeframe','1d'))" 2>/dev/null || echo "1d")
echo ""
echo -e "${YELLOW}Primaerer Timeframe (Basis fuer Bewegungserkennung):${NC}"
echo "  Empfehlung: 1d = grosse Moves | 4h = mittlere | 1h = kurze"
read -p "Timeframe [Standard: $DEFAULT_TF]: " TF_INPUT
TF_INPUT="${TF_INPUT//[$'\r\n ']/}"
TIMEFRAME="${TF_INPUT:-$DEFAULT_TF}"

# ── Zeitraum ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}Historischer Zeitraum:${NC}"
echo "  Empfehlung:"
printf "  %-6s  %s\n" "1d:"  "2021-01-01 → heute  (4 Jahre, ~1500 Kerzen)"
printf "  %-6s  %s\n" "4h:"  "2023-01-01 → heute  (2 Jahre, ~4000 Kerzen)"
printf "  %-6s  %s\n" "1h:"  "2023-06-01 → heute  (1 Jahr,  ~8000 Kerzen)"
printf "  %-6s  %s\n" "15m:" "2024-01-01 → heute  (6 Monate, ~10000 Kerzen)"

DEFAULT_START=$(python3 -c "import json; print(json.load(open('settings.json')).get('start_date','2022-01-01'))" 2>/dev/null || echo "2022-01-01")
DEFAULT_END=$(python3 -c "import json; print(json.load(open('settings.json')).get('end_date','2025-01-01'))" 2>/dev/null || echo "2025-01-01")
read -p "Start-Datum [Standard: $DEFAULT_START]: " START_INPUT
read -p "End-Datum   [Standard: $DEFAULT_END]:   " END_INPUT
START_INPUT="${START_INPUT//[$'\r\n ']/}"
END_INPUT="${END_INPUT//[$'\r\n ']/}"
START_DATE="${START_INPUT:-$DEFAULT_START}"
END_DATE="${END_INPUT:-$DEFAULT_END}"

# ── Bewegungstypen ───────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}Bewegungstypen filtern (leer = alle):${NC}"
echo "  BREAKDOWN, BREAKOUT_UP"
echo "  IMPULSE_DOWN, IMPULSE_UP"
echo "  REVERSAL_DOWN, REVERSAL_UP"
echo "  SQUEEZE_RELEASE_DOWN, SQUEEZE_RELEASE_UP"
echo "  ACCELERATION_DOWN, ACCELERATION_UP"
echo "  GAP_DOWN, GAP_UP"
read -p "Typen (kommagetrennt, leer = alle): " TYPES_INPUT
TYPES_INPUT="${TYPES_INPUT//[$'\r\n ']/}"

# ── Min. Bewegungsgroesse ────────────────────────────────────────────────────
echo ""
DEFAULT_MINPCT=$(python3 -c "import json; print(json.load(open('settings.json')).get('min_move_pct',2.5))" 2>/dev/null || echo "2.5")
read -p "Minimale Bewegungsgroesse in % [Standard: $DEFAULT_MINPCT]: " MINPCT_INPUT
MINPCT_INPUT="${MINPCT_INPUT//[$'\r\n ']/}"
MIN_PCT="${MINPCT_INPUT:-$DEFAULT_MINPCT}"

# ── Drill-Down ───────────────────────────────────────────────────────────────
echo ""
read -p "MTF Drill-Down aktivieren? (j/n) [Standard: j]: " DD_INPUT
DD_INPUT="${DD_INPUT//[$'\r\n ']/}"
DD_INPUT="${DD_INPUT:-j}"
if [[ "$DD_INPUT" =~ ^[jJyY] ]]; then
    DD_FLAG="--drill_down"
else
    DD_FLAG="--no_drill_down"
fi

# ── Top-N Events fuer Drill-Down ─────────────────────────────────────────────
if [[ "$DD_FLAG" == "--drill_down" ]]; then
    DEFAULT_TOPN=$(python3 -c "import json; print(json.load(open('settings.json')).get('report_top_n',5))" 2>/dev/null || echo "5")
    read -p "Wie viele Events fuer Drill-Down? [Standard: $DEFAULT_TOPN]: " TOPN_INPUT
    TOPN_INPUT="${TOPN_INPUT//[$'\r\n ']/}"
    TOP_N="${TOPN_INPUT:-$DEFAULT_TOPN}"
else
    TOP_N=5
fi

# ── DB leeren? ───────────────────────────────────────────────────────────────
echo ""
read -p "Bestehende DB-Eintraege fuer dieses Symbol/TF loeschen? (j/n) [Standard: n]: " CLEAR_INPUT
CLEAR_INPUT="${CLEAR_INPUT//[$'\r\n ']/}"
if [[ "$CLEAR_INPUT" =~ ^[jJyY] ]]; then
    CLEAR_FLAG="--clear"
else
    CLEAR_FLAG=""
fi

# ── Telegram ─────────────────────────────────────────────────────────────────
echo ""
read -p "Ergebnisse per Telegram senden? (j/n) [Standard: j]: " TG_INPUT
TG_INPUT="${TG_INPUT//[$'\r\n ']/}"
TG_INPUT="${TG_INPUT:-j}"
if [[ "$TG_INPUT" =~ ^[jJyY] ]]; then
    TG_FLAG=""
else
    TG_FLAG="--no_telegram"
fi

# ── Zusammenfassung ───────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "  ${CYAN}Konfiguration:${NC}"
echo -e "  Symbol:      ${GREEN}$SYMBOL${NC}"
echo -e "  Timeframe:   ${GREEN}$TIMEFRAME${NC}"
echo -e "  Zeitraum:    ${GREEN}$START_DATE → $END_DATE${NC}"
echo -e "  Min Move:    ${GREEN}$MIN_PCT%${NC}"
echo -e "  Typen:       ${GREEN}${TYPES_INPUT:-alle}${NC}"
echo -e "  Drill-Down:  ${GREEN}${DD_FLAG}${NC}  Top-N: ${GREEN}$TOP_N${NC}"
echo -e "  Telegram:    ${GREEN}${TG_FLAG:-aktiviert}${NC}"
echo -e "${BLUE}=======================================================${NC}"
echo ""
read -p "Starten? (j/n) [Standard: j]: " CONFIRM
CONFIRM="${CONFIRM//[$'\r\n ']/}"
CONFIRM="${CONFIRM:-j}"
if [[ ! "$CONFIRM" =~ ^[jJyY] ]]; then
    echo "Abgebrochen."
    deactivate
    exit 0
fi

echo ""
echo -e "${YELLOW}Pipeline startet...${NC}"
echo ""

# Argumente zusammenbauen
ARGS=(
    "--symbol"     "$SYMBOL"
    "--timeframe"  "$TIMEFRAME"
    "--start_date" "$START_DATE"
    "--end_date"   "$END_DATE"
    "--min_move_pct" "$MIN_PCT"
    "--top_n"      "$TOP_N"
    "--mode"       "full"
    "$DD_FLAG"
)
[ -n "$TYPES_INPUT" ] && ARGS+=("--movement_types" "$TYPES_INPUT")
[ -n "$CLEAR_FLAG"  ] && ARGS+=("$CLEAR_FLAG")
[ -n "$TG_FLAG"     ] && ARGS+=("$TG_FLAG")

export PYTHONPATH="$SCRIPT_DIR/src"
$PYTHON -m probebot.run "${ARGS[@]}" 2>&1 | tee logs/pipeline_$(date +%Y%m%d_%H%M%S).log

EXIT_CODE=${PIPESTATUS[0]}

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}=======================================================${NC}"
    echo -e "  ${GREEN}Pipeline erfolgreich abgeschlossen!${NC}"
    echo ""
    echo "  Ergebnisse anzeigen:  ./show_results.sh"
    echo "  Status anzeigen:      ./show_status.sh"
    echo -e "${GREEN}=======================================================${NC}"
else
    echo -e "${RED}Pipeline mit Fehler beendet (Exit Code: $EXIT_CODE).${NC}"
    echo "Logs: logs/pipeline_*.log"
fi

deactivate
