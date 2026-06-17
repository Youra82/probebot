# master_runner.py
"""
probebot Master Runner

Liest active_strategies aus settings.json und fuehrt fuer jede Strategie
run.py aus (--mode check oder --mode signal).

Ablauf:
  1. Alle aktiven Strategien aus settings.json -> live_trading_settings.active_strategies
  2. FALL A: Strategie hat offene Position -> mode=check
  3. FALL B: Strategie ist frei           -> mode=signal
     Stoppe wenn max_open_positions erreicht.

Cron-Beispiel (jede Stunde fuer 1h-Strategien):
  0 * * * * cd /pfad/zu/probebot && .venv/bin/python3 master_runner.py >> logs/master_runner.log 2>&1
"""
import json
import logging
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

# Logging
log_dir  = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'master_runner.log')),
        logging.StreamHandler(),
    ]
)

ACTIVE_POSITIONS_PATH = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker', 'active_positions.json')
RUN_SCRIPT            = os.path.join(PROJECT_ROOT, 'src', 'probebot', 'strategy', 'run.py')


def _read_positions() -> list:
    if not os.path.exists(ACTIVE_POSITIONS_PATH):
        return []
    try:
        with open(ACTIVE_POSITIONS_PATH) as f:
            return json.load(f) or []
    except Exception:
        return []


def _run_strategy(python: str, symbol: str, timeframe: str, mode: str):
    cmd = [python, RUN_SCRIPT, '--symbol', symbol, '--timeframe', timeframe, '--mode', mode]
    logging.info(f"  -> {mode.upper()} {symbol} ({timeframe})")
    try:
        result = subprocess.run(cmd, timeout=180)
        if result.returncode != 0:
            logging.warning(f"  Exit {result.returncode}: {symbol} ({timeframe})")
    except subprocess.TimeoutExpired:
        logging.error(f"  Timeout (180s): {symbol} ({timeframe})")
    except Exception as e:
        logging.error(f"  Fehler: {e}")


def main():
    logging.info("=" * 55)
    logging.info("probebot Master Runner")
    logging.info("=" * 55)

    # Python interpreter
    python = os.path.join(PROJECT_ROOT, '.venv', 'bin', 'python3')
    if not os.path.exists(python):
        python = os.path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe')
    if not os.path.exists(python):
        python = sys.executable
        logging.warning(f"Kein .venv gefunden, verwende: {python}")

    # Load settings
    try:
        with open(os.path.join(PROJECT_ROOT, 'settings.json'), encoding='utf-8') as f:
            settings = json.load(f)
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), encoding='utf-8') as f:
            secrets = json.load(f)
    except FileNotFoundError as e:
        logging.critical(f"Datei nicht gefunden: {e}")
        return
    except json.JSONDecodeError as e:
        logging.critical(f"JSON-Fehler: {e}")
        return

    if not secrets.get('probebot'):
        logging.critical("Kein 'probebot'-Account in secret.json. Bitte eintragen.")
        return

    live  = settings.get('live_trading_settings', {})
    max_p = int(live.get('max_open_positions', 5))
    strats = [s for s in live.get('active_strategies', [])
              if isinstance(s, dict) and s.get('active', False)]

    if not strats:
        logging.warning(
            "Keine aktiven Strategien in settings.json -> live_trading_settings -> active_strategies.\n"
            "Tipp: Erst show_results.sh -> Mode 3 ausfuehren um Portfolio zu erstellen."
        )
        return

    logging.info(f"Aktive Strategien: {len(strats)} | Max. Positionen: {max_p}")

    active_positions = _read_positions()
    active_keys      = {(p['symbol'], p['timeframe']) for p in active_positions}
    strat_keys       = {(s['symbol'], s['timeframe']) for s in strats}

    # ── A: Check open positions ───────────────────────────────────────────────
    to_check = [(s['symbol'], s['timeframe']) for s in strats
                if (s['symbol'], s['timeframe']) in active_keys]

    if to_check:
        logging.info(f"Offene Trades: {len(to_check)} — pruefe Positionen...")
        for sym, tf in to_check:
            _run_strategy(python, sym, tf, 'check')
    else:
        logging.info("Keine offenen Trades.")

    # ── B: Signal check for free strategies ───────────────────────────────────
    active_positions = _read_positions()
    active_keys      = {(p['symbol'], p['timeframe']) for p in active_positions
                        if (p['symbol'], p['timeframe']) in strat_keys}
    n_open = len(active_keys)

    if n_open >= max_p:
        logging.info(f"Max. Positionen ({max_p}) belegt. Kein Signal-Check.")
        logging.info("Master Runner beendet.")
        return

    logging.info(
        f"Offene Trades: {n_open}/{max_p} — "
        f"Signal-Check fuer {len(strats) - n_open} freie Strategie(n)..."
    )

    for s in strats:
        sym = s['symbol']
        tf  = s['timeframe']

        if (sym, tf) in active_keys:
            logging.info(f"  {sym} ({tf}): in Trade — ueberspringe.")
            continue

        _run_strategy(python, sym, tf, 'signal')

        # Re-read state after each signal run
        active_positions = _read_positions()
        active_keys      = {(p['symbol'], p['timeframe']) for p in active_positions
                            if (p['symbol'], p['timeframe']) in strat_keys}
        n_open = len(active_keys)

        if n_open >= max_p:
            logging.info(f"Max. Positionen ({max_p}) erreicht — stoppe Signal-Suche.")
            break

        time.sleep(1)

    logging.info("Master Runner beendet.")


if __name__ == '__main__':
    main()
