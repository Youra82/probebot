#!/bin/bash
# run_tests.sh — probebot Smoke Tests

GREEN='\033[0;32m'
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
echo -e "${GREEN}==============================${NC}"
echo -e "${GREEN}  Alle Tests bestanden!${NC}"
echo -e "${GREEN}==============================${NC}"

deactivate
