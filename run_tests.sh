#!/bin/bash
# run_tests.sh — probebot Smoke Tests

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "--- Starte probebot Smoke Tests ---"

if [ ! -f ".venv/bin/activate" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Erst install.sh ausfuehren.${NC}"
    exit 1
fi
source .venv/bin/activate

export PYTHONPATH="$(pwd)/src"

echo ""
echo "Test 1: Feature-Engine + Detector (synthetische Daten)..."
python3 test_smoke.py
if [ $? -ne 0 ]; then
    echo -e "${RED}Test 1 FEHLGESCHLAGEN.${NC}"
    deactivate; exit 1
fi

echo ""
echo "Test 2: Chart-Generierung..."
python3 test_charts.py
if [ $? -ne 0 ]; then
    echo -e "${RED}Test 2 FEHLGESCHLAGEN.${NC}"
    deactivate; exit 1
fi

echo ""
echo "Test 3: GPU-Backtester Parity (gpu_backtester vs. backtester)..."
python3 test_gpu_parity.py
if [ $? -ne 0 ]; then
    echo -e "${RED}Test 3 FEHLGESCHLAGEN.${NC}"
    deactivate; exit 1
fi

echo ""
echo "Test 4: Portfolio-Simulator (Tie-Break, Kapital, Cross-Timeframe, Exit-Logik)..."
python3 test_portfolio_simulator.py
if [ $? -ne 0 ]; then
    echo -e "${RED}Test 4 FEHLGESCHLAGEN.${NC}"
    deactivate; exit 1
fi

echo ""
echo "Test 5: Trade-Manager (gemockte Exchange: Entry/SL/TP, Notfall-Close, Close-Erkennung)..."
python3 test_trade_manager.py
if [ $? -ne 0 ]; then
    echo -e "${RED}Test 5 FEHLGESCHLAGEN.${NC}"
    deactivate; exit 1
fi

echo ""
echo "Test 6: Live-Workflow (echte Bitget-Order, ueberspringt sich selbst ohne secret.json)..."
if python3 -m pytest tests/ -v -s; then
    echo -e "${GREEN}Test 6 erfolgreich (bestanden oder uebersprungen).${NC}"
else
    PYTEST_EXIT_CODE=$?
    if [ $PYTEST_EXIT_CODE -eq 5 ]; then
        echo -e "${YELLOW}Test 6: keine Tests gefunden — uebersprungen.${NC}"
    else
        echo -e "${RED}Test 6 FEHLGESCHLAGEN.${NC}"
        deactivate; exit 1
    fi
fi

echo ""
echo -e "${GREEN}==============================${NC}"
echo -e "${GREEN}  Alle Tests bestanden!${NC}"
echo -e "${GREEN}==============================${NC}"

deactivate
