#!/bin/bash
# show_status.sh — probebot Datenbank-Status

BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [ ! -f "$PYTHON" ]; then
    echo -e "${RED}Fehler: .venv nicht gefunden. Erst install.sh ausfuehren.${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}======================================================================${NC}"
echo -e "              probebot — Status Dashboard"
echo -e "${BLUE}======================================================================${NC}"

export PYTHONPATH="$SCRIPT_DIR/src"

$PYTHON - <<'PYEOF'
import json, sqlite3
from pathlib import Path
from datetime import datetime
from collections import Counter

ROOT = Path(__file__).parent if '__file__' in dir() else Path('.')

# Colors
G = '\033[0;32m'
Y = '\033[1;33m'
C = '\033[0;36m'
R = '\033[0;31m'
B = '\033[0;34m'
NC = '\033[0m'

# Settings
try:
    with open('settings.json') as f:
        s = json.load(f)
    print(f"\n{Y}[ KONFIGURATION ]{NC}")
    print(f"  Symbol:     {G}{s.get('symbol','?')}{NC}")
    print(f"  Timeframe:  {G}{s.get('primary_timeframe','?')}{NC}")
    print(f"  Zeitraum:   {G}{s.get('start_date','?')} → {s.get('end_date','?')}{NC}")
    print(f"  Min Move:   {G}{s.get('min_move_pct','?')}%{NC}")
    print(f"  Drill-Down: {G}{s.get('drill_down','?')}{NC}")
except Exception as e:
    print(f"  Fehler beim Lesen von settings.json: {e}")

# Database
db_path = Path('artifacts/db/forensics.db')
if not db_path.exists():
    print(f"\n{R}[ DATENBANK ]{NC}")
    print(f"  forensics.db noch nicht vorhanden — erst run_pipeline.sh ausfuehren.")
else:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    print(f"\n{Y}[ DATENBANK — artifacts/db/forensics.db ]{NC}")

    # Bewegungen
    rows = conn.execute("SELECT symbol, timeframe, move_type, direction, COUNT(*) as n, "
                        "MIN(timestamp) as first_ts, MAX(timestamp) as last_ts "
                        "FROM movements GROUP BY symbol, timeframe, move_type, direction "
                        "ORDER BY symbol, timeframe, n DESC").fetchall()
    if rows:
        print(f"\n  {C}Gespeicherte Bewegungen:{NC}")
        cur_sym_tf = None
        total = 0
        for r in rows:
            sym_tf = f"{r['symbol']} | {r['timeframe']}"
            if sym_tf != cur_sym_tf:
                print(f"\n  {G}{sym_tf}{NC}")
                cur_sym_tf = sym_tf
            dir_sym = '▼' if r['direction'] == 'DOWN' else '▲'
            print(f"    {dir_sym} {r['move_type']:<22}  {r['n']:>3}x  "
                  f"  {str(r['first_ts'])[:10]} → {str(r['last_ts'])[:10]}")
            total += r['n']
        print(f"\n  Gesamt: {G}{total} Bewegungen{NC}")
    else:
        print(f"  {C}Noch keine Bewegungen gespeichert.{NC}")

    # Scan-Log
    scan_rows = conn.execute(
        "SELECT * FROM scan_log ORDER BY ran_at DESC LIMIT 5").fetchall()
    if scan_rows:
        print(f"\n  {C}Letzte Scan-Runs:{NC}")
        for r in scan_rows:
            print(f"    {str(r['ran_at'])[:16]}  {r['symbol']} {r['timeframe']}  "
                  f"{r['start_date']} → {r['end_date']}  {r['n_movements']} Bewegungen")

    # Commonalities (top predictors)
    common_rows = conn.execute(
        "SELECT feature, move_type, t_statistic, predictive_pct FROM commonalities "
        "WHERE abs(t_statistic) >= 2.0 ORDER BY abs(t_statistic) DESC LIMIT 10"
    ).fetchall()
    if common_rows:
        print(f"\n  {C}Staerkste Praediktoren (t >= 2.0):{NC}")
        for r in common_rows:
            sign = '↑' if r['t_statistic'] > 0 else '↓'
            print(f"    {sign} {r['feature']:<35}  {r['move_type']:<20}  "
                  f"t={r['t_statistic']:+.2f}  hit={r['predictive_pct']:.0f}%")

    conn.close()

# Charts
print(f"\n{Y}[ CHARTS — artifacts/charts/ ]{NC}")
charts_dir = Path('artifacts/charts')
if charts_dir.exists():
    pngs = sorted(charts_dir.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True)
    if pngs:
        for p in pngs[:8]:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            size_kb = p.stat().st_size // 1024
            print(f"  {G}{p.name:<50}{NC}  {size_kb:>5} KB  {mtime}")
    else:
        print(f"  {C}Noch keine Charts generiert.{NC}")
else:
    print(f"  {C}charts-Verzeichnis nicht gefunden.{NC}")

# Logs
print(f"\n{Y}[ LETZTE LOGS (logs/, 10 Zeilen) ]{NC}")
logs_dir = Path('logs')
if logs_dir.exists():
    # Pipeline logs
    pipeline_logs = sorted(logs_dir.glob('pipeline_*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
    if pipeline_logs:
        print(f"  Letzter Pipeline-Run: {pipeline_logs[0].name}")
        with open(pipeline_logs[0]) as f:
            lines = f.readlines()[-5:]
        for l in lines:
            print(f"    {l.rstrip()}")
    # Live scan logs
    live_logs = sorted(logs_dir.glob('live_*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
    if live_logs:
        print(f"\n  Letzter Live-Scan: {live_logs[0].name}")
        with open(live_logs[0]) as f:
            lines = f.readlines()[-5:]
        for l in lines:
            print(f"    {l.rstrip()}")
    if not pipeline_logs and not live_logs:
        print(f"  {C}Keine Log-Dateien gefunden.{NC}")
else:
    print(f"  {C}logs-Verzeichnis nicht gefunden.{NC}")
PYEOF

echo ""
echo -e "${BLUE}======================================================================${NC}"
