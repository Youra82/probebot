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

# ── Symbol(e) ────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL=$(python3 -c "import json; print(json.load(open('settings.json')).get('symbol','BTC/USDT:USDT'))" 2>/dev/null || echo "BTC/USDT:USDT")
echo -e "${YELLOW}Symbol(e) — Kurzform oder vollstaendig, Leerzeichen = mehrere:${NC}"
echo -e "${CYAN}  Beispiele: BTC | ETH | BTC ETH SOL | BTC/USDT:USDT${NC}"
read -p "Symbol(e) [Standard: $DEFAULT_SYMBOL]: " SYMBOL_INPUT
SYMBOL_INPUT=$(echo "$SYMBOL_INPUT" | tr -d '\r\n' | xargs)

# Kurzform expandieren: BTC → BTC/USDT:USDT
expand_symbol() {
    local s="$1"
    if [[ "$s" == *"/"* ]]; then
        echo "$s"
    else
        echo "${s}/USDT:USDT"
    fi
}

# Mehrere Symbole → Array
SYMBOLS=()
if [[ -z "$SYMBOL_INPUT" ]]; then
    SYMBOLS=("$DEFAULT_SYMBOL")
else
    for s in $SYMBOL_INPUT; do
        SYMBOLS+=("$(expand_symbol "$s")")
    done
fi

echo -e "${GREEN}  Symbole: ${SYMBOLS[*]}${NC}"

# ── Timeframe ────────────────────────────────────────────────────────────────
DEFAULT_TF=$(python3 -c "import json; print(json.load(open('settings.json')).get('primary_timeframe','1d'))" 2>/dev/null || echo "1d")
echo ""
echo -e "${YELLOW}Primaerer Timeframe (Basis fuer Bewegungserkennung):${NC}"
echo -e "  ${GREEN}1d${NC}   — ab 2021 (4.5J), ~365 Kerzen/Jahr   — 1 Luecke ~2 Tage bekannt"
echo -e "  ${GREEN}4h${NC}   — ab 2021 (4.5J), ~2200 Kerzen/Jahr  — lueckenlos"
echo -e "  ${GREEN}1h${NC}   — ab 2021 (4.4J), ~8800 Kerzen/Jahr  — lueckenlos"
echo -e "  ${GREEN}15m${NC}  — ab 2023 (2.5J), ~35000 Kerzen/Jahr — lueckenlos"
echo -e "  ${YELLOW}5m${NC}   — ab 2024 (1.5J), ~105000 Kerzen/Jahr"
echo -e "  ${YELLOW}1m${NC}   — ab 2025 (~6 Mon), ~525000 Kerzen/Jahr"
read -p "Timeframe [Standard: $DEFAULT_TF]: " TF_INPUT
TF_INPUT="${TF_INPUT//[$'\r\n ']/}"
TIMEFRAME="${TF_INPUT:-$DEFAULT_TF}"

# ── Zeitraum ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}Historischer Zeitraum:${NC}"
echo "  Empfehlung:"
printf "  %-6s  %s\n" "1d:"  "2021-01-01 → heute  (4.5 Jahre, ~1620 Kerzen)"
printf "  %-6s  %s\n" "4h:"  "2021-01-01 → heute  (4.5 Jahre, ~9800 Kerzen)"
printf "  %-6s  %s\n" "1h:"  "2021-01-01 → heute  (4.4 Jahre, ~35000 Kerzen)"
printf "  %-6s  %s\n" "15m:" "2023-01-01 → heute  (2.5 Jahre, ~86000 Kerzen)"
printf "  %-6s  %s\n" "5m:"  "2024-01-01 → heute  (1.5 Jahre, ~153000 Kerzen)"

# Start-Default ist timeframe-adaptiv (Bitget limitiert historische Daten je TF)
case "$TIMEFRAME" in
    1w|3d|1d) DEFAULT_START="2021-01-01" ;;
    12h|6h|4h|2h|1h) DEFAULT_START="2021-01-01" ;;
    30m|15m) DEFAULT_START="2023-01-01" ;;
    5m|3m) DEFAULT_START="2024-01-01" ;;
    1m) DEFAULT_START="2025-01-01" ;;
    *) DEFAULT_START=$(python3 -c "import json; print(json.load(open('settings.json')).get('start_date','2022-01-01'))" 2>/dev/null || echo "2022-01-01") ;;
esac
DEFAULT_END=$(date +%Y-%m-%d)
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

# min_move_pct wird automatisch kalibriert (300 Events/Jahr Ziel) — kein manueller Input nötig

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
echo -e "  Symbol(e):   ${GREEN}${SYMBOLS[*]}${NC}"
echo -e "  Timeframe:   ${GREEN}$TIMEFRAME${NC}"
echo -e "  Zeitraum:    ${GREEN}$START_DATE → $END_DATE${NC}"
echo -e "  Min Move:    ${GREEN}auto (300 Events/Jahr)${NC}"
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

export PYTHONPATH="$SCRIPT_DIR/src"
OVERALL_EXIT=0

for SYMBOL in "${SYMBOLS[@]}"; do
    echo -e "${BLUE}--- Symbol: $SYMBOL ---${NC}"

    ARGS=(
        "--symbol"     "$SYMBOL"
        "--timeframe"  "$TIMEFRAME"
        "--start_date" "$START_DATE"
        "--end_date"   "$END_DATE"
        "--top_n"      "$TOP_N"
        "--mode"       "full"
        "$DD_FLAG"
    )
    [ -n "$TYPES_INPUT" ] && ARGS+=("--movement_types" "$TYPES_INPUT")
    [ -n "$CLEAR_FLAG"  ] && ARGS+=("$CLEAR_FLAG")
    [ -n "$TG_FLAG"     ] && ARGS+=("$TG_FLAG")

    SYM_SAFE="${SYMBOL//[\/:]/_}"
    LOGFILE="logs/pipeline_${SYM_SAFE}_$(date +%Y%m%d_%H%M%S).log"
    $PYTHON -m probebot.run "${ARGS[@]}" 2>&1 | tee "$LOGFILE"

    EXIT_CODE=${PIPESTATUS[0]}
    if [ $EXIT_CODE -ne 0 ]; then
        echo -e "${RED}Fehler bei $SYMBOL (Exit $EXIT_CODE). Log: $LOGFILE${NC}"
        OVERALL_EXIT=$EXIT_CODE
    else
        echo -e "${GREEN}$SYMBOL abgeschlossen.${NC}"
    fi
    echo ""
done

if [ $OVERALL_EXIT -ne 0 ]; then
    echo -e "${RED}Pipeline mit Fehler beendet. Siehe logs/*.log${NC}"
    deactivate
    exit $OVERALL_EXIT
fi

echo -e "${GREEN}=======================================================${NC}"
echo -e "  ${GREEN}Forensik abgeschlossen!${NC}"
echo -e "${GREEN}=======================================================${NC}"

# ── Phase 2: Optimizer ────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "       Phase 2 — Optimizer"
echo -e "${BLUE}=======================================================${NC}"
echo ""
echo "  Optimiert Signal-Schwellenwerte + Risiko-Parameter"
echo "  auf den 70% Trainingsdaten (30% OOS bleibt unsichtbar)."
echo ""
read -p "Optimizer jetzt ausfuehren? (j/n) [Standard: j]: " OPT_INPUT
OPT_INPUT="${OPT_INPUT//[$'\r\n ']/}"
OPT_INPUT="${OPT_INPUT:-j}"

if [[ "$OPT_INPUT" =~ ^[jJyY] ]]; then

    DEFAULT_TRIALS=$(python3 -c "import json; print(json.load(open('settings.json')).get('optimizer_trials',100))" 2>/dev/null || echo "100")
    read -p "Anzahl Trials [Standard: $DEFAULT_TRIALS]: " TRIALS_INPUT
    TRIALS_INPUT="${TRIALS_INPUT//[$'\r\n ']/}"
    TRIALS="${TRIALS_INPUT:-$DEFAULT_TRIALS}"

    DEFAULT_CAPITAL=$(python3 -c "import json; print(json.load(open('settings.json')).get('start_capital',100))" 2>/dev/null || echo "100")
    read -p "Start-Kapital USDT [Standard: $DEFAULT_CAPITAL]: " CAP_INPUT
    CAP_INPUT="${CAP_INPUT//[$'\r\n ']/}"
    CAPITAL="${CAP_INPUT:-$DEFAULT_CAPITAL}"

    echo ""
    echo -e "${YELLOW}Optimizer-Modus:${NC}"
    echo "  best_profit  — maximiert PnL (nur DD-Grenze)"
    echo "  strict       — zusätzlich Win-Rate-Minimum"
    read -p "Modus [Standard: best_profit]: " OPT_MODE_INPUT
    OPT_MODE_INPUT="${OPT_MODE_INPUT//[$'\r\n ']/}"
    OPT_MODE="${OPT_MODE_INPUT:-best_profit}"

    DEFAULT_MAXDD=$(python3 -c "import json; print(json.load(open('settings.json')).get('max_drawdown',30))" 2>/dev/null || echo "30")
    read -p "Max. Drawdown % [Standard: $DEFAULT_MAXDD]: " MAXDD_INPUT
    MAXDD_INPUT="${MAXDD_INPUT//[$'\r\n ']/}"
    MAXDD="${MAXDD_INPUT:-$DEFAULT_MAXDD}"

    echo ""
    echo -e "${BLUE}=======================================================${NC}"
    echo -e "  Optimizer-Konfiguration:"
    echo -e "  Trials:       ${GREEN}$TRIALS${NC}"
    echo -e "  Kapital:      ${GREEN}$CAPITAL USDT${NC}"
    echo -e "  Modus:        ${GREEN}$OPT_MODE${NC}"
    echo -e "  Max. DD:      ${GREEN}$MAXDD%${NC}"
    echo -e "${BLUE}=======================================================${NC}"
    echo ""

    for SYMBOL in "${SYMBOLS[@]}"; do
        SYM_SAFE="${SYMBOL//[\/:]/_}"
        BOT_SPEC="artifacts/db/bot_spec_${SYM_SAFE}_${TIMEFRAME}.json"
        DATA_FILE="artifacts/data/data_${SYM_SAFE}_${TIMEFRAME}.parquet"

        if [ ! -f "$BOT_SPEC" ]; then
            echo -e "${RED}bot_spec nicht gefunden: $BOT_SPEC${NC}"
            echo "  Erst Forensik-Analyse ausführen."
            continue
        fi
        if [ ! -f "$DATA_FILE" ]; then
            echo -e "${RED}Daten-Cache nicht gefunden: $DATA_FILE${NC}"
            echo "  Erst Forensik-Analyse ausführen."
            continue
        fi

        # Read split_idx from bot_spec
        SPLIT_IDX=$(python3 -c "import json; print(json.load(open('$BOT_SPEC'))['meta']['split_idx'])" 2>/dev/null || echo "0")
        if [ "$SPLIT_IDX" -eq 0 ]; then
            echo -e "${RED}split_idx nicht gefunden in $BOT_SPEC — Forensik neu ausführen${NC}"
            continue
        fi

        echo -e "${BLUE}--- Optimizer: $SYMBOL $TIMEFRAME ---${NC}"
        $PYTHON -m probebot.analysis.optimizer \
            --symbol    "$SYMBOL" \
            --timeframe "$TIMEFRAME" \
            --bot_spec  "$BOT_SPEC" \
            --data      "$DATA_FILE" \
            --split_idx "$SPLIT_IDX" \
            --trials    "$TRIALS" \
            --capital   "$CAPITAL" \
            --max_dd    "$MAXDD" \
            --mode      "$OPT_MODE"

        OPT_EXIT=$?
        if [ $OPT_EXIT -eq 0 ]; then
            echo -e "${GREEN}$SYMBOL Optimizer abgeschlossen.${NC}"
        else
            echo -e "${RED}Optimizer Fehler bei $SYMBOL (Exit $OPT_EXIT)${NC}"
        fi
        echo ""
    done

    echo -e "${GREEN}=======================================================${NC}"
    echo -e "  ${GREEN}Optimizer abgeschlossen!${NC}"
    echo ""
    echo "  OOS-Ergebnis prüfen:   bash show_results.sh → Mode 1"
    echo "  Portfolio optimieren:  bash show_results.sh → Mode 3"
    echo -e "${GREEN}=======================================================${NC}"
else
    echo ""
    echo "  Optimizer übersprungen."
    echo "  Manuell starten: python -m probebot.analysis.optimizer --help"
fi

deactivate
