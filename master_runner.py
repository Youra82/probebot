# master_runner.py
"""
probebot Master Runner

Liest active_strategies aus settings.json und fuehrt fuer jede Strategie
run.py aus (--mode check oder --mode signal).

Ablauf:
  1. Alle aktiven Strategien aus settings.json -> live_trading_settings.active_strategies
  2. FALL A: Strategie hat offene Position (Tracker status='open') -> mode=check
  3. FALL B: Strategie ist frei                                    -> mode=signal
     Stoppe wenn max_open_positions erreicht.

Cron-Beispiel (jede Stunde fuer 1h-Strategien):
  2 * * * * cd /pfad/zu/probebot && .venv/bin/python3 master_runner.py >> logs/master_runner.log 2>&1
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
log_dir = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'master_runner.log')),
        logging.StreamHandler(),
    ]
)

TRACKER_DIR = os.path.join(PROJECT_ROOT, 'artifacts', 'tracker')
RUN_SCRIPT  = os.path.join(PROJECT_ROOT, 'src', 'probebot', 'strategy', 'run.py')


def _has_open_position(symbol: str, timeframe: str) -> bool:
    """
    Check per-symbol tracker file (written by trade_manager.py).
    Reads artifacts/tracker/tracker_BTCUSDTUSDT_1h.json directly
    instead of a single active_positions.json that is never written.
    """
    safe = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    path = os.path.join(TRACKER_DIR, f'tracker_{safe}.json')
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return isinstance(data, dict) and data.get('status') == 'open'
    except Exception:
        return False


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

    # Load settings + secrets
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

    live   = settings.get('live_trading_settings', {})
    max_p  = int(live.get('max_open_positions', 5))
    strats = [s for s in live.get('active_strategies', [])
              if isinstance(s, dict) and s.get('active', False)]

    if not strats:
        logging.warning(
            "Keine aktiven Strategien in settings.json "
            "-> live_trading_settings -> active_strategies.\n"
            "Tipp: Erst show_results.sh -> Mode 3 ausfuehren."
        )
        return

    logging.info(f"Aktive Strategien: {len(strats)} | Max. Positionen: {max_p}")

    # ── A: Check open positions (per-symbol tracker files) ────────────────────
    open_strats = [(s['symbol'], s['timeframe']) for s in strats
                   if _has_open_position(s['symbol'], s['timeframe'])]

    if open_strats:
        logging.info(f"Offene Trades: {len(open_strats)} — pruefe Positionen...")
        for sym, tf in open_strats:
            _run_strategy(python, sym, tf, 'check')
    else:
        logging.info("Keine offenen Trades.")

    # ── B: Signal check for free strategies ───────────────────────────────────
    n_open = sum(1 for s in strats
                 if _has_open_position(s['symbol'], s['timeframe']))

    if n_open >= max_p:
        logging.info(f"Max. Positionen ({max_p}) belegt. Kein Signal-Check.")
        logging.info("Master Runner beendet.")
        return

    logging.info(
        f"Offene Trades: {n_open}/{max_p} — "
        f"Signal-Check fuer freie Strategie(n)..."
    )

    for s in strats:
        sym = s['symbol']
        tf  = s['timeframe']

        if _has_open_position(sym, tf):
            logging.info(f"  {sym} ({tf}): in Trade — ueberspringe.")
            continue

        _run_strategy(python, sym, tf, 'signal')

        # Re-check open count after each signal run
        n_open = sum(1 for st in strats
                     if _has_open_position(st['symbol'], st['timeframe']))

        if n_open >= max_p:
            logging.info(f"Max. Positionen ({max_p}) erreicht — stoppe Signal-Suche.")
            break

        time.sleep(1)

    logging.info("Master Runner beendet.")


if __name__ == '__main__':
    main()
