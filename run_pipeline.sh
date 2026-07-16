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
export PYTHONPATH="$SCRIPT_DIR/src"

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "       probebot — Market Forensics Pipeline"
echo -e "${BLUE}=======================================================${NC}"
echo ""

# ── Modus-Auswahl ─────────────────────────────────────────────────────────────
echo -e "${YELLOW}Was moechtest du tun?${NC}"
echo "  1) Forensik durchfuehren (Phase 1) — neue oder erneute Analyse"
echo "  2) Nur Optimizer (Phase 2) — mit bereits vorhandener Forensik-Analyse"
read -p "Auswahl (1/2) [Standard: 1]: " MODE_INPUT
MODE_INPUT="${MODE_INPUT//[$'\r\n ']/}"
MODE_INPUT="${MODE_INPUT:-1}"
echo ""

# Kurzform expandieren: BTC → BTC/USDT:USDT (in beiden Pfaden gebraucht)
expand_symbol() {
    local s="$1"
    if [[ "$s" == *"/"* ]]; then
        echo "$s"
    else
        echo "${s}/USDT:USDT"
    fi
}

if [[ "$MODE_INPUT" == "2" ]]; then
    # ═══════════════════════════════════════════════════════════════════════
    # KURZER PFAD: nur Phase 2 (Optimizer) mit bereits vorhandener Forensik
    # ═══════════════════════════════════════════════════════════════════════

    # ── Vorhandene Forensik-Analysen automatisch finden ────────────────────────
    # Nur Kombinationen mit mind. einem OOS-validierten, verwendbaren Bewegungstyp
    # (oos_validation.*.use_in_bot=true) anzeigen - dieselbe Pruefung wie in
    # scan_edges.sh. Sonst landen hier auch Analysen ohne jeden Edge, was den
    # Optimizer auf Basis nicht-validierter Signale laufen liesse.
    echo -e "${YELLOW}Suche vorhandene Forensik-Analysen mit validiertem Edge...${NC}"
    FOUND_FILES=(artifacts/db/bot_spec_*.json)
    PAIR_SYMBOLS=()
    PAIR_TFS=()
    PAIR_USABLE=()
    if [ -e "${FOUND_FILES[0]}" ]; then
        for f in "${FOUND_FILES[@]}"; do
            USABLE=$(python3 -c "
import json
try:
    d = json.load(open('$f'))
    oos = d.get('oos_validation', {})
    print(sum(1 for v in oos.values() if v.get('use_in_bot')))
except Exception:
    print(0)
" 2>/dev/null)
            USABLE="${USABLE:-0}"
            if [ "$USABLE" -eq 0 ] 2>/dev/null; then
                continue
            fi

            base=$(basename "$f" .json)
            base=${base#bot_spec_}
            TF_PART="${base##*_}"
            SYM_SAFE="${base%_*}"
            # Erwartetes Muster: COIN_USDT_USDT (z.B. ETH_USDT_USDT) -> ETH/USDT:USDT
            IFS='_' read -ra _parts <<< "$SYM_SAFE"
            if [ "${#_parts[@]}" -eq 3 ]; then
                SYM="${_parts[0]}/${_parts[1]}:${_parts[2]}"
            else
                SYM="$SYM_SAFE"
            fi
            PAIR_SYMBOLS+=("$SYM")
            PAIR_TFS+=("$TF_PART")
            PAIR_USABLE+=("$USABLE")
        done
    fi

    if [ "${#PAIR_SYMBOLS[@]}" -eq 0 ]; then
        echo -e "${RED}Keine Forensik-Analyse mit validiertem Edge gefunden (use_in_bot=true in oos_validation).${NC}"
        echo "  Bitte zuerst Phase 1 (Forensik) ausfuehren (Modus 1) oder scan_edges.sh nutzen."
        deactivate
        exit 1
    fi

    echo ""
    echo -e "${GREEN}Gefundene Forensik-Analysen mit validiertem Edge:${NC}"
    for i in "${!PAIR_SYMBOLS[@]}"; do
        printf "  ${GREEN}%2d)${NC} %-20s %-6s (%s Typ(en) verwendbar)\n" \
            "$((i + 1))" "${PAIR_SYMBOLS[$i]}" "${PAIR_TFS[$i]}" "${PAIR_USABLE[$i]}"
    done
    echo ""
    read -p "Welche verwenden? (Nummern kommagetrennt, 'alle' fuer alle) [Standard: alle]: " SEL_INPUT
    SEL_INPUT=$(echo "$SEL_INPUT" | tr -d '\r\n' | xargs)

    PAIRS=()
    if [[ -z "$SEL_INPUT" || "$SEL_INPUT" == "alle" || "$SEL_INPUT" == "a" ]]; then
        for i in "${!PAIR_SYMBOLS[@]}"; do
            PAIRS+=("${PAIR_SYMBOLS[$i]}|${PAIR_TFS[$i]}")
        done
    else
        IFS=',' read -ra NUMS <<< "$SEL_INPUT"
        for n in "${NUMS[@]}"; do
            n=$(echo "$n" | xargs)
            idx=$((n - 1))
            if [ "$idx" -ge 0 ] && [ "$idx" -lt "${#PAIR_SYMBOLS[@]}" ]; then
                PAIRS+=("${PAIR_SYMBOLS[$idx]}|${PAIR_TFS[$idx]}")
            fi
        done
    fi

    if [ "${#PAIRS[@]}" -eq 0 ]; then
        echo -e "${RED}Keine gueltige Auswahl.${NC}"
        deactivate
        exit 1
    fi

    echo -e "${GREEN}Ausgewaehlt:${NC}"
    for p in "${PAIRS[@]}"; do
        echo -e "  - ${p/|/  }"
    done

    echo ""
    echo -e "${YELLOW}Bestehende Configs erzwungen ueberschreiben (Overfitting-Sperre umgehen)?${NC}"
    read -p "--force nutzen? (j/n) [Standard: n]: " FORCE_INPUT
    FORCE_INPUT="${FORCE_INPUT//[$'\r\n ']/}"
    if [[ "$FORCE_INPUT" =~ ^[jJyY] ]]; then
        OPT_FORCE="--force"
    else
        OPT_FORCE=""
    fi

    RUN_PHASE1="n"

else
    # ═══════════════════════════════════════════════════════════════════════
    # LANGER PFAD: Phase 1 (Forensik) + optional Phase 2
    # ═══════════════════════════════════════════════════════════════════════
    RUN_PHASE1="j"

    # ── Kompletter Neustart? ───────────────────────────────────────────────────
    echo -e "${YELLOW}Kompletten Neustart? Loescht Forensik-DB, Bot-Specs, Optuna-Studies,${NC}"
    echo -e "${YELLOW}Configs, Reports und Daten-Cache — fuer ALLE Symbole/Timeframes.${NC}"
    echo -e "${CYAN}(Offene Live-Positionen in artifacts/tracker/ bleiben unberuehrt.)${NC}"
    read -p "Alles zuruecksetzen? (j/n) [Standard: n]: " FULL_RESET
    FULL_RESET="${FULL_RESET//[$'\r\n ']/}"
    if [[ "$FULL_RESET" =~ ^[jJyY] ]]; then
        rm -f artifacts/db/forensics.db
        rm -f artifacts/db/optuna_probebot.db
        rm -f artifacts/db/bot_spec_*.json
        rm -f artifacts/db/report_*.html
        rm -f artifacts/data/*.parquet
        rm -f artifacts/charts/*.png
        rm -f src/probebot/strategy/configs/config_*.json
        echo -e "${GREEN}✔ Kompletter Reset durchgefuehrt — alle Symbole/Timeframes starten bei Null.${NC}"
    else
        echo -e "${GREEN}✔ Bestehende Ergebnisse werden beibehalten.${NC}"
    fi
    echo ""

    # ── Symbol(e) ────────────────────────────────────────────────────────────────
    DEFAULT_SYMBOL=$(python3 -c "import json; print(json.load(open('settings.json')).get('symbol','BTC/USDT:USDT'))" 2>/dev/null || echo "BTC/USDT:USDT")
    echo -e "${YELLOW}Symbol(e) — Kurzform oder vollstaendig, Leerzeichen = mehrere:${NC}"
    echo -e "${CYAN}  Beispiele: BTC | ETH | BTC ETH SOL | BTC/USDT:USDT${NC}"
    read -p "Symbol(e) [Standard: $DEFAULT_SYMBOL]: " SYMBOL_INPUT
    SYMBOL_INPUT=$(echo "$SYMBOL_INPUT" | tr -d '\r\n' | xargs)

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
    DEFAULT_TF=$(python3 -c "import json; print(json.load(open('settings.json')).get('primary_timeframe','1d'))" 2>/dev/null || echo "1d")
    echo ""
    echo -e "${YELLOW}Timeframe(s) — Leerzeichen = mehrere:${NC}"
    echo -e "  ${GREEN}1d${NC}   — ab 2021 (4.5J), ~365 Kerzen/Jahr   — 1 Luecke ~2 Tage bekannt"
    echo -e "  ${GREEN}4h${NC}   — ab 2021 (4.5J), ~2200 Kerzen/Jahr  — lueckenlos"
    echo -e "  ${GREEN}1h${NC}   — ab 2021 (4.4J), ~8800 Kerzen/Jahr  — lueckenlos"
    echo -e "  ${GREEN}15m${NC}  — ab 2023 (2.5J), ~35000 Kerzen/Jahr — lueckenlos"
    echo -e "  ${YELLOW}5m${NC}   — ab 2024 (1.5J), ~105000 Kerzen/Jahr"
    echo -e "  ${YELLOW}1m${NC}   — ab 2025 (~6 Mon), ~525000 Kerzen/Jahr"
    echo -e "${CYAN}  Beispiele: 1h | 4h 1h | 1d 4h 1h 15m${NC}"
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

    # ── Adaptive Start-Datum je Timeframe ─────────────────────────────────────────
    # Gibt das optimale Start-Datum fuer einen Timeframe zurueck
    tf_default_start() {
        local tf="$1"
        case "$tf" in
            1w|3d|1d|12h|6h|4h|2h|1h) echo "2021-01-01" ;;
            30m|15m)                    echo "2023-01-01" ;;
            5m|3m)                      echo "2024-01-01" ;;
            1m)                         echo "2025-01-01" ;;
            *)  python3 -c "import json; print(json.load(open('settings.json')).get('start_date','2021-01-01'))" 2>/dev/null || echo "2021-01-01" ;;
        esac
    }

    # ── Zeitraum ─────────────────────────────────────────────────────────────────
    echo ""
    echo -e "${YELLOW}Historischer Zeitraum:${NC}"
    echo -e "  ${CYAN}Automatische Defaults je Timeframe:${NC}"
    for tf in "${TIMEFRAMES[@]}"; do
        printf "  ${GREEN}%-6s${NC}  Start: ${GREEN}%s${NC}\n" "$tf" "$(tf_default_start $tf)"
    done
    echo ""
    echo "  Enter = automatisch je Timeframe (empfohlen)"
    echo "  Datum eingeben = gilt fuer alle Timeframes"
    echo ""

    DEFAULT_END=$(date +%Y-%m-%d)

    # Bestimme einen sinnvollen Default-Hinweis fuer die Prompt-Klammer:
    # Bei einem einzelnen TF → dessen Datum; bei mehreren → den fruehesten (liberalsten)
    if [ "${#TIMEFRAMES[@]}" -eq 1 ]; then
        PROMPT_START_HINT="$(tf_default_start ${TIMEFRAMES[0]})"
    else
        # Fruehesten Default nehmen (liberalster Wert = deckt alle ab)
        EARLIEST="2025-12-31"
        for tf in "${TIMEFRAMES[@]}"; do
            d="$(tf_default_start $tf)"
            if [[ "$d" < "$EARLIEST" ]]; then
                EARLIEST="$d"
            fi
        done
        PROMPT_START_HINT="$EARLIEST (auto je TF)"
    fi

    read -p "Start-Datum [Standard: $PROMPT_START_HINT]: " START_INPUT
    START_INPUT=$(echo "$START_INPUT" | tr -d '\r\n' | xargs)
    MANUAL_START="$START_INPUT"

    echo ""
    echo -e "  ${CYAN}┌─────────────────────────────────────────────────────┐${NC}"
    echo -e "  ${CYAN}│${NC}  ${YELLOW}70 / 30 REGEL — automatisch erzwungen:${NC}            ${CYAN}│${NC}"
    echo -e "  ${CYAN}│${NC}                                                     ${CYAN}│${NC}"
    echo -e "  ${CYAN}│${NC}  ${GREEN}70% Training${NC}  Forensik + Optimizer lernen NUR hier  ${CYAN}│${NC}"
    echo -e "  ${CYAN}│${NC}  ${RED}30% OOS${NC}       Optimizer sieht diese Zeit NIEMALS    ${CYAN}│${NC}"
    echo -e "  ${CYAN}│${NC}  ${RED}30% OOS${NC}       show_results.sh prueft hier (ehrlich)  ${CYAN}│${NC}"
    echo -e "  ${CYAN}│${NC}                                                     ${CYAN}│${NC}"
    echo -e "  ${CYAN}│${NC}  split_idx wird automatisch berechnet + gespeichert ${CYAN}│${NC}"
    echo -e "  ${CYAN}└─────────────────────────────────────────────────────┘${NC}"
    echo ""
    read -p "End-Datum   [Standard: $DEFAULT_END  |  70/30 automatisch]: " END_INPUT
    END_INPUT=$(echo "$END_INPUT" | tr -d '\r\n' | xargs)
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
    TYPES_INPUT=$(echo "$TYPES_INPUT" | tr -d '\r\n' | xargs)

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

    if [[ "$DD_FLAG" == "--drill_down" ]]; then
        DEFAULT_TOPN=$(python3 -c "import json; print(json.load(open('settings.json')).get('report_top_n',5))" 2>/dev/null || echo "5")
        read -p "Wie viele Events fuer Drill-Down? [Standard: $DEFAULT_TOPN]: " TOPN_INPUT
        TOPN_INPUT="${TOPN_INPUT//[$'\r\n ']/}"
        TOP_N="${TOPN_INPUT:-$DEFAULT_TOPN}"
    else
        TOP_N=5
    fi

    # ── DB leeren? (nur fragen, wenn nicht schon komplett zurueckgesetzt wurde) ──
    echo ""
    if [[ "$FULL_RESET" =~ ^[jJyY] ]]; then
        echo -e "${CYAN}Kompletter Neustart wurde bereits oben gewaehlt — nichts mehr fuer die gewaehlten Kombinationen zu loeschen.${NC}"
        CLEAR_FLAG="--clear"
        OPT_FORCE="--force"
    else
        read -p "Bestehende DB-Eintraege fuer die gewaehlten Symbole/Timeframes loeschen? (j/n) [Standard: n]: " CLEAR_INPUT
        CLEAR_INPUT="${CLEAR_INPUT//[$'\r\n ']/}"
        if [[ "$CLEAR_INPUT" =~ ^[jJyY] ]]; then
        CLEAR_FLAG="--clear"
        OPT_FORCE="--force"
        echo ""
        echo -e "  ${YELLOW}Vollstaendiger Reset — loesche alle Artifacts fuer gewaehlte Kombinationen:${NC}"
        for _TF in "${TIMEFRAMES[@]}"; do
            for _SYM in "${SYMBOLS[@]}"; do
                _SAFE="${_SYM//[\/:]/_}"
                # Optimizer-Config loeschen
                _CFG="src/probebot/strategy/configs/config_${_SAFE}_${_TF}.json"
                if [ -f "$_CFG" ]; then
                    rm -f "$_CFG"
                    echo -e "  ${RED}Geloescht:${NC} $_CFG"
                fi
                # Optuna-Study loeschen
                python3 -c "
import sys, os
sys.path.insert(0, 'src')
try:
    import optuna
    db = 'artifacts/db/optuna_probebot.db'
    if os.path.exists(db):
        storage = 'sqlite:///' + db
        for m in ['best_profit', 'strict']:
            sname = f'probebot_${_SAFE}_${_TF}_{m}'
            try:
                optuna.delete_study(study_name=sname, storage=storage)
                print(f'  Optuna-Study geloescht: {sname}')
            except Exception:
                pass
except Exception as e:
    pass
" 2>/dev/null
            done
        done
        else
            CLEAR_FLAG=""
            OPT_FORCE=""
        fi
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
    echo -e "  Timeframe(s):${GREEN}${TIMEFRAMES[*]}${NC}"
    if [[ -n "$MANUAL_START" ]]; then
        echo -e "  Zeitraum:    ${GREEN}$MANUAL_START → $END_DATE (alle TFs)${NC}"
    else
        echo -e "  Zeitraum:    ${GREEN}auto je TF → $END_DATE${NC}"
        for tf in "${TIMEFRAMES[@]}"; do
            echo -e "               ${CYAN}$tf: $(tf_default_start $tf) → $END_DATE${NC}"
        done
    fi
    echo -e "  Kombinationen:${GREEN}$((${#SYMBOLS[@]} * ${#TIMEFRAMES[@]})) (${#SYMBOLS[@]} Symbole × ${#TIMEFRAMES[@]} TF)${NC}"
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

    OVERALL_EXIT=0

    # ── Haupt-Loop: alle TF × Symbol-Kombinationen ───────────────────────────────
    for TIMEFRAME in "${TIMEFRAMES[@]}"; do

        # Start-Datum: manuell oder automatisch je TF
        if [[ -n "$MANUAL_START" ]]; then
            START_DATE="$MANUAL_START"
        else
            START_DATE="$(tf_default_start $TIMEFRAME)"
        fi

        echo -e "${BLUE}=======================================================${NC}"
        echo -e "  ${CYAN}Timeframe: ${GREEN}$TIMEFRAME${NC}  ${CYAN}Zeitraum: ${GREEN}$START_DATE → $END_DATE${NC}"
        echo -e "${BLUE}=======================================================${NC}"
        echo ""

        for SYMBOL in "${SYMBOLS[@]}"; do
            echo -e "${BLUE}--- $SYMBOL | $TIMEFRAME | $START_DATE → $END_DATE ---${NC}"

            ARGS=(
                "--symbol"     "$SYMBOL"
                "--timeframe"  "$TIMEFRAME"
                "--start_date" "$START_DATE"
                "--end_date"   "$END_DATE"
                "--top_n"      "$TOP_N"
                "--mode"       "full"
                "$DD_FLAG"
                "--quiet"
            )
            [ -n "$TYPES_INPUT" ] && ARGS+=("--movement_types" "$TYPES_INPUT")
            [ -n "$CLEAR_FLAG"  ] && ARGS+=("$CLEAR_FLAG")
            [ -n "$TG_FLAG"     ] && ARGS+=("$TG_FLAG")

            SYM_SAFE="${SYMBOL//[\/:]/_}"
            LOGFILE="logs/pipeline_${SYM_SAFE}_${TIMEFRAME}_$(date +%Y%m%d_%H%M%S).log"
            mkdir -p logs
            $PYTHON -m probebot.run "${ARGS[@]}" 2>&1 | tee "$LOGFILE"

            EXIT_CODE=${PIPESTATUS[0]}
            if [ $EXIT_CODE -eq 2 ]; then
                # 2 = Symbol nicht gelistet oder keine Daten im Zeitraum — kein
                # Fehler, einfach uebersprungen. Zaehlt nicht zu OVERALL_EXIT,
                # damit die Pipeline fuer die anderen Symbole/TFs normal weiterlaeuft.
                echo -e "${YELLOW}$SYMBOL $TIMEFRAME uebersprungen (nicht gelistet oder keine Daten im Zeitraum).${NC}"
            elif [ $EXIT_CODE -ne 0 ]; then
                echo -e "${RED}Fehler bei $SYMBOL $TIMEFRAME (Exit $EXIT_CODE). Log: $LOGFILE${NC}"
                OVERALL_EXIT=$EXIT_CODE
            else
                echo -e "${GREEN}$SYMBOL $TIMEFRAME abgeschlossen.${NC}"
            fi
            echo ""
        done
    done

    if [ $OVERALL_EXIT -ne 0 ]; then
        echo -e "${RED}Pipeline mit Fehler beendet. Siehe logs/*.log${NC}"
        deactivate
        exit $OVERALL_EXIT
    fi

    echo -e "${GREEN}=======================================================${NC}"
    echo -e "  ${GREEN}Forensik abgeschlossen!${NC}"
    echo -e "${GREEN}=======================================================${NC}"

    # PAIRS aus dem kartesischen Produkt bauen, damit Phase 2 unten (gemeinsam
    # mit Modus 2) einheitlich ueber PAIRS statt verschachtelter Schleifen laeuft.
    PAIRS=()
    for TIMEFRAME in "${TIMEFRAMES[@]}"; do
        for SYMBOL in "${SYMBOLS[@]}"; do
            PAIRS+=("${SYMBOL}|${TIMEFRAME}")
        done
    done
fi

# ── Phase 2: Optimizer ────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "       Phase 2 — Optimizer"
echo -e "${BLUE}=======================================================${NC}"
echo ""
echo "  Optimiert Signal-Schwellenwerte + Risiko-Parameter"
echo "  auf den 70% Trainingsdaten (30% OOS bleibt unsichtbar)."
echo ""

if [[ "$RUN_PHASE1" == "j" ]]; then
    read -p "Optimizer jetzt ausfuehren? (j/n) [Standard: j]: " OPT_INPUT
    OPT_INPUT="${OPT_INPUT//[$'\r\n ']/}"
    OPT_INPUT="${OPT_INPUT:-j}"
else
    # Modus 2 wurde explizit gewaehlt, um den Optimizer laufen zu lassen —
    # nicht nochmal fragen ob er ausgefuehrt werden soll.
    OPT_INPUT="j"
fi

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
    echo "  strict       — zusaetzlich Win-Rate-Minimum"
    read -p "Modus [Standard: best_profit]: " OPT_MODE_INPUT
    OPT_MODE_INPUT="${OPT_MODE_INPUT//[$'\r\n ']/}"
    OPT_MODE="${OPT_MODE_INPUT:-best_profit}"

    DEFAULT_MAXDD=$(python3 -c "import json; print(json.load(open('settings.json')).get('max_drawdown',30))" 2>/dev/null || echo "30")
    read -p "Max. Drawdown % [Standard: $DEFAULT_MAXDD]: " MAXDD_INPUT
    MAXDD_INPUT="${MAXDD_INPUT//[$'\r\n ']/}"
    MAXDD="${MAXDD_INPUT:-$DEFAULT_MAXDD}"

    echo ""
    echo -e "${YELLOW}Optimizer-Hardware:${NC}"
    echo "  vectorized — viele Parameter-Kombinationen gleichzeitig als Tensor-Batch"
    echo "               (deutlich schneller, benoetigt torch)"
    echo "  legacy     — 1 Kombination nach der anderen (bisheriges Verhalten)"
    DEFAULT_ENGINE=$(python3 -c "import json; print(json.load(open('settings.json')).get('optimizer_engine','vectorized'))" 2>/dev/null || echo "vectorized")
    read -p "Engine [Standard: $DEFAULT_ENGINE]: " ENGINE_INPUT
    ENGINE_INPUT="${ENGINE_INPUT//[$'\r\n ']/}"
    OPT_ENGINE="${ENGINE_INPUT:-$DEFAULT_ENGINE}"

    OPT_DEVICE="auto"
    OPT_GPU_BATCH="64"
    if [[ "$OPT_ENGINE" == "vectorized" ]]; then
        echo "  auto — automatisch waehlen (aktuell CPU, siehe gpu_backtester.py: bei"
        echo "         diesem Workload schneller als CUDA durch Kernel-Launch-Overhead)"
        echo "  cpu  — erzwinge CPU"
        echo "  cuda — erzwinge GPU (Fallback auf CPU falls keine CUDA-GPU vorhanden)"
        DEFAULT_DEVICE=$(python3 -c "import json; print(json.load(open('settings.json')).get('optimizer_device','auto'))" 2>/dev/null || echo "auto")
        read -p "Device [Standard: $DEFAULT_DEVICE]: " DEVICE_INPUT
        DEVICE_INPUT="${DEVICE_INPUT//[$'\r\n ']/}"
        OPT_DEVICE="${DEVICE_INPUT:-$DEFAULT_DEVICE}"

        DEFAULT_GPU_BATCH=$(python3 -c "import json; print(json.load(open('settings.json')).get('optimizer_gpu_batch_size',64))" 2>/dev/null || echo "64")
        read -p "Batch-Groesse (parallele Trials pro Durchlauf) [Standard: $DEFAULT_GPU_BATCH]: " GPU_BATCH_INPUT
        GPU_BATCH_INPUT="${GPU_BATCH_INPUT//[$'\r\n ']/}"
        OPT_GPU_BATCH="${GPU_BATCH_INPUT:-$DEFAULT_GPU_BATCH}"
    fi

    echo ""
    echo -e "${BLUE}=======================================================${NC}"
    echo -e "  Optimizer-Konfiguration:"
    echo -e "  Trials:       ${GREEN}$TRIALS${NC}"
    echo -e "  Kapital:      ${GREEN}$CAPITAL USDT${NC}"
    echo -e "  Modus:        ${GREEN}$OPT_MODE${NC}"
    echo -e "  Max. DD:      ${GREEN}$MAXDD%${NC}"
    echo -e "  Engine:       ${GREEN}$OPT_ENGINE${NC}"
    if [[ "$OPT_ENGINE" == "vectorized" ]]; then
        echo -e "  Device:       ${GREEN}$OPT_DEVICE${NC}  |  Batch-Groesse: ${GREEN}$OPT_GPU_BATCH${NC}"
    fi
    echo -e "${BLUE}=======================================================${NC}"
    echo ""

    # Optimizer ueber alle ausgewaehlten Symbol/Timeframe-Paare
    for PAIR in "${PAIRS[@]}"; do
        SYMBOL="${PAIR%|*}"
        TIMEFRAME="${PAIR#*|}"
        SYM_SAFE="${SYMBOL//[\/:]/_}"
        BOT_SPEC="artifacts/db/bot_spec_${SYM_SAFE}_${TIMEFRAME}.json"
        DATA_FILE="artifacts/data/data_${SYM_SAFE}_${TIMEFRAME}.parquet"

        if [ ! -f "$BOT_SPEC" ]; then
            echo -e "${RED}bot_spec nicht gefunden: $BOT_SPEC${NC}"
            echo "  Erst Forensik-Analyse ausfuehren."
            continue
        fi
        if [ ! -f "$DATA_FILE" ]; then
            echo -e "${RED}Daten-Cache nicht gefunden: $DATA_FILE${NC}"
            echo "  Erst Forensik-Analyse ausfuehren."
            continue
        fi

        SPLIT_IDX=$(python3 -c "import json; print(json.load(open('$BOT_SPEC'))['meta']['split_idx'])" 2>/dev/null || echo "0")
        if [ "$SPLIT_IDX" -eq 0 ]; then
            echo -e "${RED}split_idx nicht gefunden in $BOT_SPEC — Forensik neu ausfuehren${NC}"
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
            --engine    "$OPT_ENGINE" \
            --device    "$OPT_DEVICE" \
            --gpu_batch_size "$OPT_GPU_BATCH" \
            ${OPT_FORCE}

        OPT_EXIT=$?
        if [ $OPT_EXIT -eq 0 ]; then
            echo -e "${GREEN}$SYMBOL $TIMEFRAME Optimizer abgeschlossen.${NC}"
        else
            echo -e "${RED}Optimizer Fehler bei $SYMBOL $TIMEFRAME (Exit $OPT_EXIT)${NC}"
        fi
        echo ""
    done

    echo -e "${GREEN}=======================================================${NC}"
    echo -e "  ${GREEN}Optimizer abgeschlossen!${NC}"
    echo ""
    echo "  OOS-Ergebnis pruefen:   bash show_results.sh -> Mode 1"
    echo "  Portfolio optimieren:   bash show_results.sh -> Mode 3"
    echo -e "${GREEN}=======================================================${NC}"
else
    echo ""
    echo "  Optimizer uebersprungen."
    echo "  Manuell starten: python -m probebot.analysis.optimizer --help"
fi

deactivate
