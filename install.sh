#!/bin/bash
# probebot — Installations-Skript

echo "=== probebot Installation ==="

# Virtual Environment erstellen
python3 -m venv .venv
echo "venv erstellt."

# Packages installieren
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo "Packages installiert."

# Verzeichnisse anlegen
mkdir -p logs artifacts/db artifacts/charts

# Skripte ausfuehrbar machen
chmod +x *.sh

# secret.json pruefen
if [ ! -f "secret.json" ]; then
    echo ""
    echo "WARNUNG: secret.json fehlt!"
    echo "Bitte secret.json anlegen:"
    echo ""
    echo '{
  "telegram": {
    "bot_token": "DEIN_BOT_TOKEN",
    "chat_id":   "DEINE_CHAT_ID"
  },
  "probebot": {
    "api_key":    "...",
    "api_secret": "...",
    "passphrase": "..."
  }
}'
else
    echo "secret.json gefunden."
fi

echo ""
echo "=== Installation abgeschlossen ==="
echo ""
echo "Naechste Schritte:"
echo "  1. secret.json mit Telegram-Bot und Bitget API-Keys befuellen"
echo "  2. settings.json anpassen (Symbol, Timeframe, Zeitraum)"
echo "  3. Forensik-Analyse starten:"
echo "     ./run_pipeline.sh"
echo ""
