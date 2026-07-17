#!/bin/bash
# run_tests.sh — probebot Sicherheitscheck

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "--- Starte probebot Sicherheitscheck ---"

if [ ! -f ".venv/bin/activate" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Erst install.sh ausfuehren.${NC}"
    exit 1
fi
source .venv/bin/activate

export PYTHONPATH="$(pwd)/src"

echo "Fuehre Pytest aus (Live-Workflow-Test)..."
if python3 -m pytest tests/ -v -s; then
    echo -e "${GREEN}Pytest erfolgreich durchgelaufen. Alle Tests bestanden.${NC}"
    EXIT_CODE=0
else
    PYTEST_EXIT_CODE=$?
    if [ $PYTEST_EXIT_CODE -eq 5 ]; then
        echo -e "${GREEN}Pytest beendet: Keine Tests zum Ausfuehren gefunden.${NC}"
        EXIT_CODE=0
    else
        echo -e "${RED}Pytest fehlgeschlagen (Exit Code: $PYTEST_EXIT_CODE).${NC}"
        EXIT_CODE=$PYTEST_EXIT_CODE
    fi
fi

deactivate
echo "--- Sicherheitscheck abgeschlossen ---"
exit $EXIT_CODE
