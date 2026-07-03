#!/bin/bash
# run_optimizer.sh — Phase 2 (Optimizer) einzeln nachholen, ohne Phase 1 neu zu starten.
# Braucht bereits vorhandene bot_spec_*.json + data_*.parquet aus einem frueheren
# run_pipeline.sh-Lauf (auch wenn dort der Optimizer damals uebersprungen wurde).

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
echo -e "       probebot — Phase 2: Optimizer (einzeln nachholen)"
echo -e "${BLUE}=======================================================${NC}"
echo ""
echo -e "${CYAN}Nutzt bereits vorhandene bot_spec_*.json + data_*.parquet aus Phase 1.${NC}"
echo ""

# ── Symbol(e) ────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL=$(python3 -c "import json; print(json.load(open('settings.json')).get('symbol','BTC/USDT:USDT'))" 2>/dev/null || echo "BTC/USDT:USDT")
echo -e "${YELLOW}Symbol(e) — Kurzform oder vollstaendig, Leerzeichen = mehrere:${NC}"
echo -e "${CYAN}  Beispiele: BTC | ETH | BTC ETH SOL | BTC/USDT:USDT${NC}"
read -p "Symbol(e) [Standard: $DEFAULT_SYMBOL]: " SYMBOL_INPUT
SYMBOL_INPUT=$(echo "$SYMBOL_INPUT" | tr -d '\r\n' | xargs)

expand_symbol() {
    local s="$1"
    if [[ "$s" == *"/"* ]]; then
        echo "$s"
    else
        echo "${s}/USDT:USDT"
    fi
}

SYMBOLS=()
if [[ -z "$SYMBOL_INPUT" ]]; then
    SYMBOLS=("$DEFAULT_SYMBOL")
else
    for s in $SYMBOL_INPUT; do
        SYMBOLS+=("$(expand_symbol "$s")")
    done
fi
echo -e "${GREEN}  Symbole: ${SYMBOLS[*]}${NC}"

# ── Timeframe(s) ──────────────────────────────────────────────────────────────
DEFAULT_TF=$(python3 -c "import json; print(json.load(open('settings.json')).get('primary_timeframe','1h'))" 2>/dev/null || echo "1h")
echo ""
echo -e "${YELLOW}Timeframe(s) — Leerzeichen = mehrere:${NC}"
read -p "Timeframe(s) [Standard: $DEFAULT_TF]: " TF_INPUT
TF_INPUT=$(echo "$TF_INPUT" | tr -d '\r\n' | xargs)

TIMEFRAMES=()
if [[ -z "$TF_INPUT" ]]; then
    TIMEFRAMES=("$DEFAULT_TF")
else
    for t in $TF_INPUT; do
        TIMEFRAMES+=("$t")
    done
fi
echo -e "${GREEN}  Timeframes: ${TIMEFRAMES[*]}${NC}"

# ── Optimizer-Parameter ────────────────────────────────────────────────────────
DEFAULT_TRIALS=$(python3 -c "import json; print(json.load(open('settings.json')).get('optimizer_trials',100))" 2>/dev/null || echo "100")
echo ""
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
echo "  strict       — zusaetzlich Win-Rate-Minimum"
read -p "Modus [Standard: best_profit]: " OPT_MODE_INPUT
OPT_MODE_INPUT="${OPT_MODE_INPUT//[$'\r\n ']/}"
OPT_MODE="${OPT_MODE_INPUT:-best_profit}"

DEFAULT_MAXDD=$(python3 -c "import json; print(json.load(open('settings.json')).get('max_drawdown',30))" 2>/dev/null || echo "30")
read -p "Max. Drawdown % [Standard: $DEFAULT_MAXDD]: " MAXDD_INPUT
MAXDD_INPUT="${MAXDD_INPUT//[$'\r\n ']/}"
MAXDD="${MAXDD_INPUT:-$DEFAULT_MAXDD}"

echo ""
echo -e "${YELLOW}Bestehende Config erzwungen ueberschreiben (Overfitting-Sperre umgehen)?${NC}"
read -p "--force nutzen? (j/n) [Standard: n]: " FORCE_INPUT
FORCE_INPUT="${FORCE_INPUT//[$'\r\n ']/}"
if [[ "$FORCE_INPUT" =~ ^[jJyY] ]]; then
    OPT_FORCE="--force"
else
    OPT_FORCE=""
fi

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "  Trials: ${GREEN}$TRIALS${NC}  Kapital: ${GREEN}$CAPITAL USDT${NC}  Modus: ${GREEN}$OPT_MODE${NC}  Max.DD: ${GREEN}$MAXDD%${NC}"
echo -e "${BLUE}=======================================================${NC}"
echo ""

export PYTHONPATH="$SCRIPT_DIR/src"

for TIMEFRAME in "${TIMEFRAMES[@]}"; do
    for SYMBOL in "${SYMBOLS[@]}"; do
        SYM_SAFE="${SYMBOL//[\/:]/_}"
        BOT_SPEC="artifacts/db/bot_spec_${SYM_SAFE}_${TIMEFRAME}.json"
        DATA_FILE="artifacts/data/data_${SYM_SAFE}_${TIMEFRAME}.parquet"

        if [ ! -f "$BOT_SPEC" ]; then
            echo -e "${RED}bot_spec nicht gefunden: $BOT_SPEC${NC}"
            echo "  Erst run_pipeline.sh (Phase 1) fuer $SYMBOL $TIMEFRAME ausfuehren."
            continue
        fi
        if [ ! -f "$DATA_FILE" ]; then
            echo -e "${RED}Daten-Cache nicht gefunden: $DATA_FILE${NC}"
            echo "  Erst run_pipeline.sh (Phase 1) fuer $SYMBOL $TIMEFRAME ausfuehren."
            continue
        fi

        SPLIT_IDX=$(python3 -c "import json; print(json.load(open('$BOT_SPEC'))['meta']['split_idx'])" 2>/dev/null || echo "0")
        if [ "$SPLIT_IDX" -eq 0 ]; then
            echo -e "${RED}split_idx nicht gefunden in $BOT_SPEC — Phase 1 neu ausfuehren${NC}"
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
            --mode      "$OPT_MODE" \
            ${OPT_FORCE}

        OPT_EXIT=$?
        if [ $OPT_EXIT -eq 0 ]; then
            echo -e "${GREEN}$SYMBOL $TIMEFRAME Optimizer abgeschlossen.${NC}"
        else
            echo -e "${RED}Optimizer Fehler bei $SYMBOL $TIMEFRAME (Exit $OPT_EXIT)${NC}"
        fi
        echo ""
    done
done

echo -e "${GREEN}=======================================================${NC}"
echo -e "  ${GREEN}Optimizer abgeschlossen!${NC}"
echo ""
echo "  OOS-Ergebnis pruefen:   bash show_results.sh -> Mode 1"
echo "  Portfolio optimieren:   bash show_results.sh -> Mode 3"
echo -e "${GREEN}=======================================================${NC}"

deactivate
