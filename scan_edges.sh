#!/bin/bash
# scan_edges.sh — Mehrere Symbol/Timeframe-Kombinationen automatisch durchsuchen.
#
# Laeuft die volle Forensik (5-Kandidaten Perioden-Suche, wie run_pipeline.sh
# Phase 1) fuer jede angegebene Kombination durch — kein Drill-Down, kein
# Telegram, keine manuelle Rueckfrage zwischen den Kombinationen. Wird ein
# OOS-validierter Bewegungstyp gefunden, startet automatisch der Optimizer
# (Phase 2) fuer genau diese Kombination.
#
# Die Forensik-Pipeline selbst wurde performance-optimiert (Numba-JIT auf den
# Physik-Features, vektorisierte OOS-Validierung/Movement-Detection) — ein
# kompletter Lauf dauert dadurch nur noch einen Bruchteil der vorherigen Zeit,
# bei exakt identischen Ergebnissen. Deshalb reicht ein einfacher Batch-Loop
# ohne Abkuerzungen bei der Erkennungsqualitaet.

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
export PYTHONPATH="$SCRIPT_DIR/src"

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "       probebot — Edge-Scanner"
echo -e "${BLUE}=======================================================${NC}"
echo -e "${CYAN}Volle Forensik pro Kombination (kein Drill-Down/Telegram),${NC}"
echo -e "${CYAN}bei gefundenem Edge automatisch Optimizer.${NC}"
echo ""

expand_symbol() {
    local s="$1"
    if [[ "$s" == *"/"* ]]; then
        echo "$s"
    else
        echo "${s}/USDT:USDT"
    fi
}

tf_default_start() {
    local tf="$1"
    case "$tf" in
        1w|3d|1d|12h|6h|4h|2h|1h) echo "2021-01-01" ;;
        30m|15m)                    echo "2023-01-01" ;;
        5m|3m)                      echo "2024-01-01" ;;
        1m)                         echo "2025-01-01" ;;
        *)                          echo "2021-01-01" ;;
    esac
}

# ── Symbol(e) ────────────────────────────────────────────────────────────────
echo -e "${YELLOW}Symbol(e) — Kurzform oder vollstaendig, Leerzeichen = mehrere:${NC}"
echo -e "${CYAN}  Beispiel: BTC ETH XRP BNB ADA DOT LTC BCH ATOM ETC TRX FIL LINK UNI SOL DOGE AVAX NEAR XLM ICP${NC}"
read -p "Symbol(e): " SYMBOL_INPUT
SYMBOL_INPUT=$(echo "$SYMBOL_INPUT" | tr -d '\r\n' | xargs)
if [[ -z "$SYMBOL_INPUT" ]]; then
    echo -e "${RED}Kein Symbol angegeben.${NC}"
    deactivate
    exit 1
fi
SYMBOLS=()
for s in $SYMBOL_INPUT; do
    SYMBOLS+=("$(expand_symbol "$s")")
done
echo -e "${GREEN}  Symbole: ${SYMBOLS[*]}${NC}"

# ── Timeframe(s) ──────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}Timeframe(s) — Leerzeichen = mehrere:${NC}"
read -p "Timeframe(s) [Standard: 1h]: " TF_INPUT
TF_INPUT=$(echo "$TF_INPUT" | tr -d '\r\n' | xargs)
TIMEFRAMES=()
if [[ -z "$TF_INPUT" ]]; then
    TIMEFRAMES=("1h")
else
    for t in $TF_INPUT; do
        TIMEFRAMES+=("$t")
    done
fi
echo -e "${GREEN}  Timeframes: ${TIMEFRAMES[*]}${NC}"

# ── Zeitraum ─────────────────────────────────────────────────────────────────
echo ""
read -p "Start-Datum [Standard: automatisch je Timeframe]: " MANUAL_START
MANUAL_START=$(echo "$MANUAL_START" | tr -d '\r\n' | xargs)
DEFAULT_END=$(date +%Y-%m-%d)
read -p "End-Datum [Standard: $DEFAULT_END]: " END_INPUT
END_DATE=$(echo "$END_INPUT" | tr -d '\r\n' | xargs)
END_DATE="${END_DATE:-$DEFAULT_END}"

# ── Optimizer-Parameter fuer automatischen Deep-Dive bei Fund ─────────────────
echo ""
echo -e "${YELLOW}Optimizer-Parameter (nur genutzt wenn ein Edge gefunden wird):${NC}"
DEFAULT_TRIALS=$(python3 -c "import json; print(json.load(open('settings.json')).get('optimizer_trials',100))" 2>/dev/null || echo "100")
read -p "Trials [Standard: $DEFAULT_TRIALS]: " TRIALS_INPUT
TRIALS="${TRIALS_INPUT:-$DEFAULT_TRIALS}"
DEFAULT_CAPITAL=$(python3 -c "import json; print(json.load(open('settings.json')).get('start_capital',100))" 2>/dev/null || echo "100")
read -p "Start-Kapital USDT [Standard: $DEFAULT_CAPITAL]: " CAP_INPUT
CAPITAL="${CAP_INPUT:-$DEFAULT_CAPITAL}"
read -p "Modus (best_profit/strict) [Standard: best_profit]: " MODE_INPUT
OPT_MODE="${MODE_INPUT:-best_profit}"
DEFAULT_MAXDD=$(python3 -c "import json; print(json.load(open('settings.json')).get('max_drawdown',30))" 2>/dev/null || echo "30")
read -p "Max. Drawdown % [Standard: $DEFAULT_MAXDD]: " MAXDD_INPUT
MAXDD="${MAXDD_INPUT:-$DEFAULT_MAXDD}"

TOTAL=$((${#SYMBOLS[@]} * ${#TIMEFRAMES[@]}))
echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "  ${TOTAL} Kombinationen werden nacheinander gescannt."
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

mkdir -p logs
FOUND_PAIRS=()
NO_EDGE_PAIRS=()
SKIPPED_PAIRS=()
ERROR_PAIRS=()
SCAN_START_TS=$(date +%s)

for TIMEFRAME in "${TIMEFRAMES[@]}"; do
    if [[ -n "$MANUAL_START" ]]; then
        START_DATE="$MANUAL_START"
    else
        START_DATE="$(tf_default_start $TIMEFRAME)"
    fi

    for SYMBOL in "${SYMBOLS[@]}"; do
        echo ""
        echo -e "${BLUE}=== Scanne $SYMBOL $TIMEFRAME ($START_DATE → $END_DATE) ===${NC}"

        SYM_SAFE="${SYMBOL//[\/:]/_}"
        LOGFILE="logs/scan_${SYM_SAFE}_${TIMEFRAME}_$(date +%Y%m%d_%H%M%S).log"

        "$PYTHON" -m probebot.run \
            --symbol "$SYMBOL" --timeframe "$TIMEFRAME" \
            --start_date "$START_DATE" --end_date "$END_DATE" \
            --mode full --no_drill_down --no_telegram --quiet --clear \
            2>&1 | tee "$LOGFILE"
        EXIT_CODE=${PIPESTATUS[0]}

        if [ $EXIT_CODE -eq 2 ]; then
            echo -e "${YELLOW}$SYMBOL $TIMEFRAME uebersprungen (nicht gelistet oder keine Daten).${NC}"
            SKIPPED_PAIRS+=("$SYMBOL|$TIMEFRAME")
            continue
        elif [ $EXIT_CODE -ne 0 ]; then
            echo -e "${RED}Fehler bei $SYMBOL $TIMEFRAME (Exit $EXIT_CODE). Log: $LOGFILE${NC}"
            ERROR_PAIRS+=("$SYMBOL|$TIMEFRAME")
            continue
        fi

        BOT_SPEC="artifacts/db/bot_spec_${SYM_SAFE}_${TIMEFRAME}.json"
        DATA_FILE="artifacts/data/data_${SYM_SAFE}_${TIMEFRAME}.parquet"

        USABLE=$(python3 -c "
import json
try:
    d = json.load(open('$BOT_SPEC'))
    oos = d.get('oos_validation', {})
    print(sum(1 for v in oos.values() if v.get('use_in_bot')))
except Exception:
    print(0)
" 2>/dev/null)
        USABLE="${USABLE:-0}"

        if [ "$USABLE" -gt 0 ] 2>/dev/null; then
            echo -e "${GREEN}✔ EDGE GEFUNDEN — $USABLE Typ(en) OOS-validiert. Starte Optimizer...${NC}"
            FOUND_PAIRS+=("$SYMBOL|$TIMEFRAME|$USABLE")

            SPLIT_IDX=$(python3 -c "import json; print(json.load(open('$BOT_SPEC'))['meta']['split_idx'])" 2>/dev/null || echo "0")
            if [ "$SPLIT_IDX" -eq 0 ] 2>/dev/null; then
                echo -e "${RED}split_idx nicht gefunden — Optimizer uebersprungen.${NC}"
            else
                "$PYTHON" -m probebot.analysis.optimizer \
                    --symbol "$SYMBOL" --timeframe "$TIMEFRAME" \
                    --bot_spec "$BOT_SPEC" --data "$DATA_FILE" \
                    --split_idx "$SPLIT_IDX" --trials "$TRIALS" \
                    --capital "$CAPITAL" --max_dd "$MAXDD" --mode "$OPT_MODE"
            fi
        else
            echo -e "${YELLOW}kein Edge gefunden.${NC}"
            NO_EDGE_PAIRS+=("$SYMBOL|$TIMEFRAME")
        fi
    done
done

SCAN_END_TS=$(date +%s)
ELAPSED=$((SCAN_END_TS - SCAN_START_TS))

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "       Scan abgeschlossen ($((ELAPSED/60))m $((ELAPSED%60))s)"
echo -e "${BLUE}=======================================================${NC}"
echo ""
echo -e "${GREEN}Edges gefunden (${#FOUND_PAIRS[@]}):${NC}"
if [ "${#FOUND_PAIRS[@]}" -eq 0 ]; then
    echo "  (keine)"
else
    for p in "${FOUND_PAIRS[@]}"; do
        IFS='|' read -r sym tf n <<< "$p"
        echo -e "  ✔ $sym $tf — $n Typ(en) verwendbar"
    done
fi
echo ""
echo -e "${YELLOW}Kein Edge (${#NO_EDGE_PAIRS[@]}):${NC}"
for p in "${NO_EDGE_PAIRS[@]}"; do
    echo -e "  - ${p/|/  }"
done
if [ "${#SKIPPED_PAIRS[@]}" -gt 0 ]; then
    echo ""
    echo -e "${CYAN}Uebersprungen (${#SKIPPED_PAIRS[@]}, nicht gelistet/keine Daten):${NC}"
    for p in "${SKIPPED_PAIRS[@]}"; do
        echo -e "  - ${p/|/  }"
    done
fi
if [ "${#ERROR_PAIRS[@]}" -gt 0 ]; then
    echo ""
    echo -e "${RED}Fehler (${#ERROR_PAIRS[@]}, siehe logs/):${NC}"
    for p in "${ERROR_PAIRS[@]}"; do
        echo -e "  - ${p/|/  }"
    done
fi
echo ""
echo "  OOS-Ergebnis pruefen:   bash show_results.sh -> Mode 1"
echo -e "${BLUE}=======================================================${NC}"

deactivate
