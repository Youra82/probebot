#!/bin/bash
# run_analysis.sh — probebot Wissenschaftliche Analysen
#
# Alle 20 Analysen unter einem Befehl. Interaktive Auswahl.
# Laeuft auf den echten OOS-Configs (src/probebot/strategy/configs/) und
# ihren echten Bewegungsdaten — kein Blick auf Trainingsdaten.
#
# Ausfuehrung:
#   ./run_analysis.sh
#   ./run_analysis.sh --no-telegram    (kein Telegram, nur lokale Charts)

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
NO_TELEGRAM=""

for arg in "$@"; do
    [[ "$arg" == "--no-telegram" ]] && NO_TELEGRAM="--no-telegram"
done

if [ ! -f "$PYTHON" ]; then
    echo -e "${RED}FEHLER: .venv nicht gefunden. Erst install.sh ausfuehren!${NC}"
    exit 1
fi
source "$SCRIPT_DIR/.venv/bin/activate"
export PYTHONPATH="$SCRIPT_DIR/src"

echo ""
echo "======================================================="
echo -e "  ${BOLD}probebot — Wissenschaftliche Analysen${NC}"
echo "======================================================="
echo ""
echo -e "  ${CYAN}── Priorität 1: Fundament ─────────────────────────${NC}"
echo "   1) Walk-Forward Stabilitaets-Test"
echo "   2) Slippage & Fee Impact"
echo "   3) Monte Carlo Simulation (Bootstrap)"
echo "   4) Bootstrap Signifikanztest"
echo ""
echo -e "  ${CYAN}── Priorität 2: Direkte Gewinnoptimierung ──────────${NC}"
echo "   5) RR-Ratio Sweep"
echo "   6) Score-Threshold Sweep"
echo "   7) Stop-Loss % Sweep"
echo "   8) Parameter Sensitivity (Tornado-Diagramm)"
echo ""
echo -e "  ${CYAN}── Priorität 3: Systemverbesserung ─────────────────${NC}"
echo "   9) Multi-Symbol Signal-Konfluenz"
echo "  10) Signal-Decay-Analyse"
echo "  11) Anti-Korrelations-Portfolio"
echo "  12) Kelly Position Sizing"
echo ""
echo -e "  ${CYAN}── Priorität 4–6: Feintuning & Portfolio ───────────${NC}"
echo "  13) Regime Performance Analysis"
echo "  14) Signal-Staerke-Analyse"
echo "  15) Multi-Type Signal-Konfluenz"
echo "  16) Volatilitaets-Filter Optimierung"
echo "  17) Tageszeit-Analyse"
echo "  18) Regime-adaptive Parameter"
echo "  19) Drawdown Duration Analysis"
echo ""
echo -e "  ${CYAN}── Robustheit ───────────────────────────────────────${NC}"
echo "  20) Split-Punkt-Robustheit"
echo ""
echo "   0) Alle Analysen nacheinander ausfuehren"
echo ""
read -p "Auswahl (0-20): " MODE
MODE="${MODE//[$'\r\n ']/}"
echo ""

run_mode() {
    local m="$1"
    case "$m" in
    1)  echo -e "${GREEN}▶ Walk-Forward Stabilitaets-Test${NC}"
        read -p "Fenstergroesse in Wochen [Standard: 4]: " W
        W="${W//[$'\r\n ']/}"; [[ "$W" =~ ^[0-9]+$ ]] || W=4
        $PYTHON -m probebot.analysis.walk_forward --window-weeks "$W" $NO_TELEGRAM ;;
    2)  echo -e "${GREEN}▶ Slippage & Fee Impact${NC}"
        $PYTHON -m probebot.analysis.fee_impact $NO_TELEGRAM ;;
    3)  echo -e "${GREEN}▶ Monte Carlo Simulation${NC}"
        read -p "Anzahl Simulationen [Standard: 10000]: " S
        S="${S//[$'\r\n ']/}"; [[ "$S" =~ ^[0-9]+$ ]] || S=10000
        $PYTHON -m probebot.analysis.monte_carlo --simulations "$S" $NO_TELEGRAM ;;
    4)  echo -e "${GREEN}▶ Bootstrap Signifikanztest${NC}"
        $PYTHON -m probebot.analysis.bootstrap_test $NO_TELEGRAM ;;
    5)  echo -e "${GREEN}▶ RR-Ratio Sweep${NC}"
        $PYTHON -m probebot.analysis.param_sweep --param rr $NO_TELEGRAM ;;
    6)  echo -e "${GREEN}▶ Score-Threshold Sweep${NC}"
        $PYTHON -m probebot.analysis.param_sweep --param score $NO_TELEGRAM ;;
    7)  echo -e "${GREEN}▶ Stop-Loss % Sweep${NC}"
        $PYTHON -m probebot.analysis.param_sweep --param sl $NO_TELEGRAM ;;
    8)  echo -e "${GREEN}▶ Parameter Sensitivity${NC}"
        $PYTHON -m probebot.analysis.sensitivity $NO_TELEGRAM ;;
    9)  echo -e "${GREEN}▶ Multi-Symbol Signal-Konfluenz${NC}"
        read -p "Gleichzeitigkeit-Fenster in Stunden [Standard: 2]: " WH
        WH="${WH//[$'\r\n ']/}"; [[ "$WH" =~ ^[0-9]+$ ]] || WH=2
        $PYTHON -m probebot.analysis.multi_confirmation --window-hours "$WH" ;;
    10) echo -e "${GREEN}▶ Signal-Decay-Analyse${NC}"
        $PYTHON -m probebot.analysis.signal_decay $NO_TELEGRAM ;;
    11) echo -e "${GREEN}▶ Anti-Korrelations-Portfolio${NC}"
        $PYTHON -m probebot.analysis.correlation $NO_TELEGRAM ;;
    12) echo -e "${GREEN}▶ Kelly Position Sizing${NC}"
        $PYTHON -m probebot.analysis.kelly_sizing $NO_TELEGRAM ;;
    13) echo -e "${GREEN}▶ Regime Performance Analysis${NC}"
        $PYTHON -m probebot.analysis.regime_analysis $NO_TELEGRAM ;;
    14) echo -e "${GREEN}▶ Signal-Staerke-Analyse${NC}"
        $PYTHON -m probebot.analysis.score_strength $NO_TELEGRAM ;;
    15) echo -e "${GREEN}▶ Multi-Type Signal-Konfluenz${NC}"
        $PYTHON -m probebot.analysis.multi_type_confluence $NO_TELEGRAM ;;
    16) echo -e "${GREEN}▶ Volatilitaets-Filter Optimierung${NC}"
        $PYTHON -m probebot.analysis.volatility_filter $NO_TELEGRAM ;;
    17) echo -e "${GREEN}▶ Tageszeit-Analyse${NC}"
        $PYTHON -m probebot.analysis.time_analysis $NO_TELEGRAM ;;
    18) echo -e "${GREEN}▶ Regime-adaptive Parameter${NC}"
        $PYTHON -m probebot.analysis.regime_adaptive $NO_TELEGRAM ;;
    19) echo -e "${GREEN}▶ Drawdown Duration Analysis${NC}"
        $PYTHON -m probebot.analysis.drawdown_duration $NO_TELEGRAM ;;
    20) echo -e "${GREEN}▶ Split-Punkt-Robustheit${NC}"
        $PYTHON -m probebot.analysis.split_sensitivity $NO_TELEGRAM ;;
    *)  echo -e "${RED}Ungueltige Auswahl: $m${NC}" ;;
    esac
}

if [ "$MODE" == "0" ]; then
    echo -e "${YELLOW}▶ Alle 20 Analysen werden nacheinander ausgefuehrt (Standardwerte).${NC}\n"
    export PROBEBOT_BATCH=1
    for i in $(seq 1 20); do
        echo ""
        echo -e "${CYAN}══════════════════════════════════════════════════════${NC}"
        echo -e "${CYAN}  Analyse $i / 20${NC}"
        echo -e "${CYAN}══════════════════════════════════════════════════════${NC}"
        case "$i" in
            1)  $PYTHON -m probebot.analysis.walk_forward --window-weeks 4 $NO_TELEGRAM 2>/dev/null || true ;;
            2)  $PYTHON -m probebot.analysis.fee_impact $NO_TELEGRAM 2>/dev/null || true ;;
            3)  $PYTHON -m probebot.analysis.monte_carlo --simulations 10000 $NO_TELEGRAM 2>/dev/null || true ;;
            4)  $PYTHON -m probebot.analysis.bootstrap_test $NO_TELEGRAM 2>/dev/null || true ;;
            5)  $PYTHON -m probebot.analysis.param_sweep --param rr $NO_TELEGRAM 2>/dev/null || true ;;
            6)  $PYTHON -m probebot.analysis.param_sweep --param score $NO_TELEGRAM 2>/dev/null || true ;;
            7)  $PYTHON -m probebot.analysis.param_sweep --param sl $NO_TELEGRAM 2>/dev/null || true ;;
            8)  $PYTHON -m probebot.analysis.sensitivity $NO_TELEGRAM 2>/dev/null || true ;;
            9)  $PYTHON -m probebot.analysis.multi_confirmation --window-hours 2 2>/dev/null || true ;;
            10) $PYTHON -m probebot.analysis.signal_decay $NO_TELEGRAM 2>/dev/null || true ;;
            11) $PYTHON -m probebot.analysis.correlation $NO_TELEGRAM 2>/dev/null || true ;;
            12) $PYTHON -m probebot.analysis.kelly_sizing $NO_TELEGRAM 2>/dev/null || true ;;
            13) $PYTHON -m probebot.analysis.regime_analysis $NO_TELEGRAM 2>/dev/null || true ;;
            14) $PYTHON -m probebot.analysis.score_strength $NO_TELEGRAM 2>/dev/null || true ;;
            15) $PYTHON -m probebot.analysis.multi_type_confluence $NO_TELEGRAM 2>/dev/null || true ;;
            16) $PYTHON -m probebot.analysis.volatility_filter $NO_TELEGRAM 2>/dev/null || true ;;
            17) $PYTHON -m probebot.analysis.time_analysis $NO_TELEGRAM 2>/dev/null || true ;;
            18) $PYTHON -m probebot.analysis.regime_adaptive $NO_TELEGRAM 2>/dev/null || true ;;
            19) $PYTHON -m probebot.analysis.drawdown_duration $NO_TELEGRAM 2>/dev/null || true ;;
            20) $PYTHON -m probebot.analysis.split_sensitivity $NO_TELEGRAM 2>/dev/null || true ;;
        esac
    done
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Alle Analysen abgeschlossen.${NC}"
    echo -e "${GREEN}  Charts gespeichert in: docs/                  ${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════${NC}"
else
    run_mode "$MODE"
fi

deactivate
