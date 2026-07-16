#!/bin/bash
set -e

echo "--- Sicheres Update wird ausgefuehrt ---"

# 1. Sichere secret.json
echo "1. Erstelle ein Backup von 'secret.json'..."
cp secret.json secret.json.bak

# 2. Hole neuesten Stand von GitHub
echo "2. Hole den neuesten Stand von GitHub..."
git fetch origin

# 3. Setze lokales Verzeichnis hart auf GitHub-Stand zurueck
echo "3. Setze alle Dateien auf den neuesten Stand zurueck und verwerfe lokale Aenderungen..."
git reset --hard origin/main

# 4. Stelle secret.json wieder her
echo "4. Stelle 'secret.json' aus dem Backup wieder her..."
cp secret.json.bak secret.json
rm secret.json.bak

# 5. Loesche Python-Cache
# -exec rm -rf statt -delete: numba (@njit(cache=True) in features/physics.py)
# legt eigene .nbi/.nbc-Cache-Dateien in __pycache__ ab, die keine .pyc sind —
# nach Zeile "find *.pyc -delete" ist der Ordner dadurch nicht leer, und
# "find -type d -delete" scheitert dann (erfordert leere Verzeichnisse). Wegen
# "set -e" oben brach das Skript an dieser Stelle bisher komplett ab, sodass
# Schritt 6 (chmod) und Schritt 7 (pip install) nie ausgefuehrt wurden.
echo "5. Loesche alten Python-Cache fuer einen sauberen Neustart..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 6. Ausfuehrungsrechte setzen
echo "6. Setze Ausfuehrungsrechte fuer alle .sh-Skripte..."
chmod +x *.sh

# 7. Dependencies aktualisieren
echo "7. Aktualisiere Python-Pakete..."
.venv/bin/pip install -r requirements.txt --quiet

echo ""
echo "Update erfolgreich abgeschlossen. probebot ist jetzt auf dem neuesten Stand."
