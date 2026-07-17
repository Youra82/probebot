#!/bin/bash
# run_unit_tests.sh — probebot synthetische Korrektheits-Checks (kein Live-Zugriff)
#
# Laeuft NICHT als Teil von run_tests.sh (das ist bewusst nur der schnelle
# Live-PEPE-Workflow-Test, ~35s). Dieses Skript prueft stattdessen die
# Backtester-/Simulator-/Trade-Manager-Logik gegen synthetische Daten und
# eine gemockte Exchange -- dauert laenger (GPU-Parity-Batches), aber ohne
# echtes Konto/Netzwerk. Bei Aenderungen an backtester.py, gpu_backtester.py,
# portfolio_simulator.py oder trade_manager.py vor dem Pushen ausfuehren.

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "--- Starte probebot Unit-/Synthetik-Tests ---"

if [ ! -f ".venv/bin/activate" ]; then
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Erst install.sh ausfuehren.${NC}"
    exit 1
fi
source .venv/bin/activate

export PYTHONPATH="$(pwd)/src"

echo "Fuehre Pytest aus (synthetische Tests, kein Live-Zugriff)..."
if python3 -m pytest --ignore=tests -v -s; then
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
echo "--- Unit-/Synthetik-Tests abgeschlossen ---"
exit $EXIT_CODE
