#!/bin/bash
# show_results.sh — probebot Ergebnis-Anzeige

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [ ! -f "$PYTHON" ]; then
    echo -e "${RED}Fehler: .venv nicht gefunden. Erst install.sh ausfuehren.${NC}"
    exit 1
fi
source "$SCRIPT_DIR/.venv/bin/activate"
export PYTHONPATH="$SCRIPT_DIR/src"

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "       probebot — Ergebnis-Anzeige"
echo -e "${BLUE}=======================================================${NC}"
echo ""
echo -e "${YELLOW}Was moechtest du anzeigen?${NC}"
echo ""
echo -e "  ${GREEN}Trading Bot Evaluation (OOS 30%):${NC}"
echo "  1) OOS-Backtest aller Configs — Ranking-Tabelle"
echo "  2) Portfolio-Simulation (mehrere Configs kombinieren)"
echo "  3) Auto-Portfolio-Optimizer (greedy, DD-Constraint)"
echo "  4) Equity-Chart erstellen + Telegram"
echo ""
echo -e "  ${CYAN}Forensik / Rohdaten:${NC}"
echo "  5) Staerkste Praediktoren (Top-Features pro Bewegungstyp)"
echo "  6) Alle gespeicherten Bewegungen (letzte 50)"
echo "  7) Pattern-Cluster Zusammenfassung"
echo "  8) Letzten JSON-Report anzeigen"
echo "  9) Bewegung nach Datum suchen"
echo ""
read -p "Auswahl (1-9) [Standard: 1]: " MODE
MODE="${MODE//[$'\r\n ']/}"
MODE="${MODE:-1}"

# ── Modi 1-4: Trading Bot Evaluation (Python show_results.py) ─────────────────
if [[ "$MODE" =~ ^[1-4]$ ]]; then
    $PYTHON -m probebot.analysis.show_results --mode "$MODE"
    deactivate
    exit 0
fi

# ── Modi 5-9: Forensik / Rohdaten (inline Python) ─────────────────────────────
$PYTHON - <<PYEOF
import json, sqlite3, sys
from pathlib import Path

ROOT = Path('$SCRIPT_DIR')
db_path = ROOT / 'artifacts' / 'db' / 'forensics.db'

G  = '\033[0;32m'
Y  = '\033[1;33m'
C  = '\033[0;36m'
R  = '\033[0;31m'
B  = '\033[0;34m'
NC = '\033[0m'

mode = '$MODE'

if not db_path.exists():
    print(f"{R}Datenbank nicht gefunden. Erst run_pipeline.sh ausfuehren.{NC}")
    sys.exit(0)

conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row

if mode == '5':
    print(f"\n{Y}=== Staerkste Praediktoren (t-Statistik >= 2.0) ==={NC}\n")
    rows = conn.execute(
        "SELECT * FROM commonalities WHERE abs(t_statistic) >= 2.0 "
        "ORDER BY move_type, abs(t_statistic) DESC"
    ).fetchall()
    cur_type = None
    for r in rows:
        if r['move_type'] != cur_type:
            cur_type = r['move_type']
            print(f"\n{G}  {cur_type}{NC}  ({r['n_events']} Events | {r['symbol']} {r['timeframe']})")
            print(f"  {'Feature':<38}  {'t-Stat':>7}  {'Vor Move':>10}  {'Gesamt':>10}  {'Hit%':>6}")
            print(f"  {'-'*38}  {'-'*7}  {'-'*10}  {'-'*10}  {'-'*6}")
        sign = '↑' if r['t_statistic'] > 0 else '↓'
        color = G if r['t_statistic'] > 0 else R
        print(f"  {color}{sign} {r['feature']:<36}{NC}  "
              f"{color}{r['t_statistic']:>+7.2f}{NC}  "
              f"{r['mean_before']:>10.4f}  "
              f"{r['mean_all']:>10.4f}  "
              f"{r['predictive_pct']:>5.0f}%")

elif mode == '6':
    rows = conn.execute(
        "SELECT * FROM movements ORDER BY symbol, timeframe, timestamp DESC LIMIT 50"
    ).fetchall()
    print(f"\n{Y}=== Letzte 50 Bewegungen ==={NC}\n")
    print(f"  {'Zeitpunkt':<18}  {'Typ':<22}  {'Dir':>4}  {'Mag':>7}  {'ATR':>5}  Kontext")
    print(f"  {'-'*18}  {'-'*22}  {'-'*4}  {'-'*7}  {'-'*5}  {'-'*30}")
    for r in rows:
        ctx = json.loads(r['context']) if r['context'] else {}
        regime = ctx.get('regime', '?')
        rsi = ctx.get('rsi_14')
        rsi_s = f"RSI={rsi:.0f}" if rsi else ''
        dir_sym = '▼' if r['direction'] == 'DOWN' else '▲'
        color = R if r['direction'] == 'DOWN' else G
        print(f"  {str(r['timestamp'])[:16]:<18}  "
              f"{color}{r['move_type']:<22}{NC}  "
              f"{color}{dir_sym}{NC}{r['direction']:>3}  "
              f"{r['magnitude_pct']:>+6.2f}%  "
              f"{r['atr_multiple']:>4.1f}x  "
              f"{regime}  {rsi_s}")

elif mode == '7':
    print(f"\n{Y}=== Pattern-Cluster Zusammenfassung ==={NC}")
    rows = conn.execute("SELECT * FROM movements ORDER BY symbol, timeframe").fetchall()
    if not rows:
        print(f"  {C}Keine Daten. Erst run_pipeline.sh ausfuehren.{NC}")
    else:
        from collections import Counter
        type_count = Counter(r['move_type'] for r in rows)
        dir_count = Counter(r['direction'] for r in rows)
        print(f"\n  Gesamt Bewegungen: {G}{len(rows)}{NC}")
        print(f"  Aufwaerts:  {G}{dir_count.get('UP',0)}{NC}")
        print(f"  Abwaerts:   {R}{dir_count.get('DOWN',0)}{NC}")
        print(f"\n  Bewegungstypen:")
        for mtype, cnt in type_count.most_common():
            up = sum(1 for r in rows if r['move_type'] == mtype and r['direction'] == 'UP')
            dn = cnt - up
            bar_len = int(cnt / max(type_count.values()) * 30)
            bar = '#' * bar_len
            print(f"    {mtype:<26}  {G}{bar:<30}{NC}  {cnt:>3}x  (up:{up} dn:{dn})")

elif mode == '8':
    report_files = sorted(Path('artifacts/db').glob('report_*.json'),
                          key=lambda p: p.stat().st_mtime, reverse=True)
    if not report_files:
        print(f"{R}Kein JSON-Report gefunden.{NC}")
    else:
        latest = report_files[0]
        print(f"\n{Y}=== {latest.name} ==={NC}\n")
        with open(latest) as f:
            data = json.load(f)
        print(f"  Erstellt:    {data.get('generated_at','?')[:19]}")
        print(f"  Symbol:      {data.get('symbol','?')}")
        print(f"  Timeframe:   {data.get('timeframe','?')}")
        print(f"  Zeitraum:    {data.get('period',{}).get('start','?')} -> {data.get('period',{}).get('end','?')}")
        print(f"  Bewegungen:  {data.get('n_movements','?')}")
        corr = data.get('correlations', {})
        for mtype, ranked in corr.items():
            if isinstance(ranked, dict):
                ranked = ranked.get('rows', [])
            top = [r for r in ranked if abs(r.get('t_statistic', 0)) >= 2.0][:5]
            if top:
                print(f"\n  {G}{mtype}{NC}:")
                for r in top:
                    sign = '>' if r['t_statistic'] > 0 else '<'
                    print(f"    {sign} {r['feature']:<35}  t={r['t_statistic']:+.2f}  hit={r['predictive_pct']:.0f}%")

elif mode == '9':
    date_input = input("Datum eingeben (YYYY-MM-DD): ").strip()
    rows = conn.execute(
        "SELECT * FROM movements WHERE timestamp LIKE ? ORDER BY timestamp",
        (f"{date_input}%",)
    ).fetchall()
    if rows:
        print(f"\n{Y}=== Bewegungen am {date_input} ==={NC}\n")
        for r in rows:
            ctx = json.loads(r['context']) if r['context'] else {}
            dd = json.loads(r['drill_down']) if r['drill_down'] else {}
            dir_sym = 'v' if r['direction'] == 'DOWN' else '^'
            color = R if r['direction'] == 'DOWN' else G
            print(f"  {color}{dir_sym} {r['move_type']:<22}  {r['magnitude_pct']:>+6.2f}%  {r['atr_multiple']:.1f}xATR{NC}")
            print(f"  Zeitpunkt: {str(r['timestamp'])[:16]}")
            if ctx:
                print(f"  Regime: {ctx.get('regime','?')}  RSI: {ctx.get('rsi_14','?')}  "
                      f"ADX: {ctx.get('adx','?')}  Entropy: {ctx.get('entropy_20','?')}")
            if dd:
                print(f"  MTF Drill-Down: {list(dd.keys())}")
            prec = json.loads(r['preconditions']) if r['preconditions'] else {}
            if prec:
                print(f"  Anz. Vorbedingungen gespeichert: {len(prec)}")
            print()
    else:
        print(f"  {C}Keine Bewegungen am {date_input} gefunden.{NC}")

conn.close()
PYEOF

deactivate
