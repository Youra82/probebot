#!/bin/bash
# rescan_all.sh — non-interaktiver Voll-Rescan ueber viele Symbole x Timeframes.
#
# Ruft probebot.run (Phase 1) und probebot.analysis.optimizer (Phase 2) direkt
# auf, ohne die read-p-Prompts von run_pipeline.sh. Gedacht fuer grosse Batch-
# Laeufe (mehrere Stunden/Tage), bei denen interaktives Copy-Paste von Antworten
# fehleranfaellig ist. Ueberspringt Kombinationen, deren config_*.json schon
# existiert (resumable nach Abbruch/SSH-Disconnect) — ausser mit --force.
#
# Usage:
#   bash rescan_all.sh --reset          # einmalig: alles loeschen, dann komplett neu
#   bash rescan_all.sh                  # ohne Reset: macht dort weiter, wo aufgehoert wurde
#   bash rescan_all.sh --force          # erzwingt Neu-Optimierung auch fuer bereits fertige Kombis
#
# Am besten mit nohup im Hintergrund starten, damit ein SSH-Disconnect den
# Lauf nicht abbricht:
#   nohup bash rescan_all.sh --reset > logs/rescan_all.log 2>&1 &
#   tail -f logs/rescan_all.log

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
if [ ! -f "$PYTHON" ]; then
    echo "FEHLER: .venv nicht gefunden. Erst install.sh ausfuehren."
    exit 1
fi
export PYTHONPATH="$SCRIPT_DIR/src"

# Beobachtete Symbole (20) x bisher ueblich gescannte Timeframes (6) — bei
# Bedarf hier anpassen.
SYMBOLS_SHORT=(ADA ATOM AVAX BCH BNB BTC DOGE DOT ETC ETH FIL ICP LINK LTC NEAR SOL TRX UNI XLM XRP)
TIMEFRAMES=(30m 1h 2h 4h 6h 1d)

END_DATE=$(date +%Y-%m-%d)

tf_default_start() {
    case "$1" in
        1w|3d|1d|12h|6h|4h|2h|1h) echo "2021-01-01" ;;
        30m|15m)                   echo "2023-01-01" ;;
        5m|3m)                     echo "2024-01-01" ;;
        1m)                        echo "2025-01-01" ;;
        *)                         echo "2021-01-01" ;;
    esac
}

RESET=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --reset) RESET=1 ;;
        --force) FORCE=1 ;;
    esac
done

if [ "$RESET" -eq 1 ]; then
    echo "=== Kompletter Reset ==="
    rm -f artifacts/db/forensics.db artifacts/db/forensics.db-wal artifacts/db/forensics.db-shm
    rm -f artifacts/db/optuna_probebot.db
    rm -f artifacts/db/bot_spec_*.json
    rm -f artifacts/db/report_*.html
    rm -f artifacts/data/*.parquet
    rm -f artifacts/charts/*.png artifacts/charts/*.html artifacts/charts/*.xlsx
    rm -f docs/*.png
    rm -f src/probebot/strategy/configs/config_*.json
    echo "Reset fertig."
    echo ""
fi

mkdir -p logs

TRIALS=100
CAPITAL=100
MAXDD=30
OPT_MODE=best_profit
ENGINE=vectorized
DEVICE=auto
GPU_BATCH=64

TOTAL=0
DONE=0
SKIPPED=0
FAILED=0

for TF in "${TIMEFRAMES[@]}"; do
    START="$(tf_default_start "$TF")"
    for SYM_SHORT in "${SYMBOLS_SHORT[@]}"; do
        SYMBOL="${SYM_SHORT}/USDT:USDT"
        SYM_SAFE="${SYMBOL//[\/:]/_}"
        TOTAL=$((TOTAL + 1))

        CONFIG_FILE="src/probebot/strategy/configs/config_${SYM_SAFE}_${TF}.json"
        if [ "$FORCE" -eq 0 ] && [ -f "$CONFIG_FILE" ]; then
            echo "[$TOTAL] $SYMBOL $TF — bereits vorhanden, ueberspringe (config existiert)."
            SKIPPED=$((SKIPPED + 1))
            continue
        fi

        LOGFILE="logs/rescan_${SYM_SAFE}_${TF}.log"
        echo "[$TOTAL] $SYMBOL $TF ($START -> $END_DATE) — Phase 1..."

        $PYTHON -m probebot.run \
            --symbol "$SYMBOL" --timeframe "$TF" \
            --start_date "$START" --end_date "$END_DATE" \
            --top_n 5 --mode full --no_drill_down --quiet --no_telegram \
            --clear \
            > "$LOGFILE" 2>&1
        P1_EXIT=$?

        if [ $P1_EXIT -eq 2 ]; then
            echo "    uebersprungen (Symbol nicht gelistet oder keine Daten im Zeitraum)."
            continue
        elif [ $P1_EXIT -ne 0 ]; then
            echo "    FEHLER Phase 1 (Exit $P1_EXIT) — siehe $LOGFILE"
            FAILED=$((FAILED + 1))
            continue
        fi

        BOT_SPEC="artifacts/db/bot_spec_${SYM_SAFE}_${TF}.json"
        DATA_FILE="artifacts/data/data_${SYM_SAFE}_${TF}.parquet"
        if [ ! -f "$BOT_SPEC" ] || [ ! -f "$DATA_FILE" ]; then
            echo "    bot_spec/data fehlt nach Phase 1 — ueberspringe Optimizer."
            continue
        fi
        SPLIT_IDX=$($PYTHON -c "import json; print(json.load(open('$BOT_SPEC'))['meta']['split_idx'])" 2>/dev/null || echo "0")
        if [ "$SPLIT_IDX" = "0" ]; then
            echo "    split_idx fehlt — ueberspringe Optimizer."
            continue
        fi

        echo "    Phase 2 (Optimizer, $TRIALS Trials)..."
        $PYTHON -m probebot.analysis.optimizer \
            --symbol "$SYMBOL" --timeframe "$TF" \
            --bot_spec "$BOT_SPEC" --data "$DATA_FILE" --split_idx "$SPLIT_IDX" \
            --trials "$TRIALS" --capital "$CAPITAL" --max_dd "$MAXDD" \
            --mode "$OPT_MODE" --engine "$ENGINE" --device "$DEVICE" \
            --gpu_batch_size "$GPU_BATCH" --force \
            >> "$LOGFILE" 2>&1
        P2_EXIT=$?

        if [ $P2_EXIT -ne 0 ]; then
            echo "    FEHLER Phase 2 (Exit $P2_EXIT) — siehe $LOGFILE"
            FAILED=$((FAILED + 1))
        else
            echo "    fertig."
            DONE=$((DONE + 1))
        fi
    done
done

echo ""
echo "=== Rescan abgeschlossen ==="
echo "Versucht: $TOTAL | Neu fertig: $DONE | Uebersprungen (schon vorhanden): $SKIPPED | Fehler: $FAILED"
echo "Naechster Schritt: bash push_configs.sh"
