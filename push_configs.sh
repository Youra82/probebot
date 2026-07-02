#!/bin/bash
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd "$SCRIPT_DIR"

CONFIGS_DIR="src/probebot/strategy/configs"
BOTSPEC_DIR="artifacts/db"

echo ""
echo -e "${YELLOW}========== CONFIGS + BOT-SPECS PUSHEN ==========${NC}"
echo ""

# Prüfe ob Config-Dateien existieren
CONFIG_COUNT=$(ls "$CONFIGS_DIR"/config_*.json 2>/dev/null | wc -l)
if [ "$CONFIG_COUNT" -eq 0 ]; then
    echo -e "${RED}Keine Konfigurationsdateien gefunden in: $CONFIGS_DIR${NC}"
    echo -e "${YELLOW}Bitte zuerst run_pipeline.sh ausfuehren.${NC}"
    exit 1
fi

echo "Gefundene Configs:"
for f in "$CONFIGS_DIR"/config_*.json; do
    echo "  - $(basename "$f")"
done

# bot_spec_*.json ist normalerweise NICHT von Git getrackt (.gitignore:
# artifacts/db/*.json) — wird aber vom Live-Bot zur Laufzeit gebraucht
# (Entry-Bedingungen). Hier bewusst mit -f mitgepusht, damit Live-Trading
# nach einem git pull auf einer anderen Maschine ohne erneuten Forensik-Lauf
# funktioniert.
BOTSPEC_COUNT=$(ls "$BOTSPEC_DIR"/bot_spec_*.json 2>/dev/null | wc -l)
if [ "$BOTSPEC_COUNT" -gt 0 ]; then
    echo ""
    echo "Gefundene Bot-Specs:"
    for f in "$BOTSPEC_DIR"/bot_spec_*.json; do
        echo "  - $(basename "$f")"
    done
else
    echo ""
    echo -e "${YELLOW}Keine bot_spec_*.json gefunden — nur Configs werden gepusht.${NC}"
    echo -e "${YELLOW}(Live-Trading braucht bot_spec zur Laufzeit — ggf. erst run_pipeline.sh ausfuehren.)${NC}"
fi
echo ""

# Nur Configs, Bot-Specs und settings.json stagen — nichts anderes
git add "$CONFIGS_DIR"/config_*.json
if [ "$BOTSPEC_COUNT" -gt 0 ]; then
    git add -f "$BOTSPEC_DIR"/bot_spec_*.json
fi
git add settings.json 2>/dev/null
STAGED=$(git diff --cached --name-only)

if [ -z "$STAGED" ]; then
    echo -e "${YELLOW}Keine Aenderungen — Configs/Bot-Specs sind bereits aktuell im Repo.${NC}"
    exit 0
fi

echo "Geaenderte Dateien:"
echo "$STAGED" | sed 's/^/  /'
echo ""

# Commit
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
git commit -m "Update: probebot Configs + Bot-Specs aktualisiert ($TIMESTAMP)"

# Push
echo ""
echo -e "${YELLOW}Pushe auf origin/main...${NC}"
git push origin HEAD:main

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}Configs erfolgreich gepusht!${NC}"
else
    echo ""
    echo -e "${YELLOW}Remote hat neuere Commits — fuehre Rebase durch...${NC}"
    git stash
    git pull origin main --rebase
    if [ $? -ne 0 ]; then
        git stash pop 2>/dev/null
        echo -e "${RED}Rebase fehlgeschlagen. Bitte manuell loesen.${NC}"
        exit 1
    fi
    git stash pop 2>/dev/null
    git push origin HEAD:main
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}Configs erfolgreich gepusht!${NC}"
    else
        echo -e "${RED}Push nach Rebase fehlgeschlagen.${NC}"
        exit 1
    fi
fi
