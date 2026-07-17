# tests/test_workflow.py
"""
Umfassender LIVE-Workflow-Test gegen Bitget (echtes Konto, echte Orders).

Analog zu dnabot/tests/test_workflow.py und ltbbot/tests/test_workflow.py:
faelscht ein Trading-Signal (als ob die Forensik-Engine es gefunden haette)
und schickt es durch probebots ECHTE trade_manager._execute_trade()-Logik
gegen die echte Bitget-API. Kein Mock der Exchange -- das hier prueft
genau das, was ein Mock nicht pruefen kann: platziert Bitget die Order
wirklich, mit den richtigen Parametern, und laesst sie sich sauber wieder
schliessen.

Ueberspringt sich selbst automatisch (pytest.skip) wenn keine secret.json
mit einem 'probebot'-Account vorhanden ist -- unkritisch fuer CI/andere
Maschinen ohne Live-Zugang. Sehr kleines Risiko (0.1% vom Kontostand,
5x Hebel) -- siehe Kommentare unten.
"""
import pytest
import os
import sys
import json
import logging
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from probebot.utils.exchange import Exchange
from probebot.utils import trade_manager as tm
from probebot.strategy.signal_logic import compute_trade_params

test_logger = logging.getLogger("test-probebot-workflow")
test_logger.setLevel(logging.INFO)
if not test_logger.handlers:
    test_logger.addHandler(logging.StreamHandler(sys.stdout))

SYMBOL    = 'PEPE/USDT:USDT'
TIMEFRAME = '4h'


@pytest.fixture(scope='module')
def test_setup():
    print('\n--- Starte umfassenden LIVE probebot-Workflow-Test (PEPE) ---')
    print('\n[Setup] Bereite Testumgebung vor...')

    secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
    if not os.path.exists(secret_path):
        pytest.skip('secret.json nicht gefunden. Ueberspringe Live-Workflow-Test.')

    with open(secret_path, 'r', encoding='utf-8') as f:
        secrets = json.load(f)

    account = secrets.get('probebot')
    if isinstance(account, list):
        account = account[0] if account else None
    if not account:
        pytest.skip("Es wird ein Account unter 'probebot' in secret.json benoetigt.")

    telegram_config = secrets.get('telegram', {})

    try:
        exchange = Exchange(account)
        if not exchange.markets:
            pytest.fail('Exchange konnte nicht initialisiert werden (Maerkte nicht geladen).')
    except Exception as e:
        pytest.fail(f'Exchange konnte nicht initialisiert werden: {e}')

    print(f'-> Fuehre initiales Aufraeumen fuer {SYMBOL} durch...')
    try:
        exchange.cancel_all_orders(SYMBOL)
        time.sleep(2)
        positions = exchange.fetch_open_positions(SYMBOL)
        if positions:
            exchange.close_position(SYMBOL)
            time.sleep(3)
        print('-> Ausgangszustand ist sauber.')
    except Exception as e:
        pytest.fail(f'Fehler beim initialen Aufraeumen: {e}')

    yield exchange, telegram_config

    print('\n[Teardown] Raeume nach dem Test auf...')
    try:
        print('-> 1. Loesche offene Trigger Orders...')
        exchange.cancel_all_orders(SYMBOL)
        time.sleep(2)

        print('-> 2. Pruefe auf offene Positionen...')
        positions = exchange.fetch_open_positions(SYMBOL)
        if positions:
            print('-> Position nach Test noch offen. Schliesse sie...')
            exchange.close_position(SYMBOL)
            time.sleep(3)
        else:
            print('-> Keine offene Position gefunden.')

        print('-> 3. Loesche verbleibende Trigger Orders (Sicherheitsnetz)...')
        exchange.cancel_all_orders(SYMBOL)
        print('-> Aufraeumen abgeschlossen.')
    except Exception as e:
        print(f'FEHLER beim Aufraeumen nach dem Test: {e}')


def test_full_probebot_workflow_on_bitget(test_setup):
    exchange, telegram_config = test_setup

    bal = exchange.fetch_balance_usdt()
    print(f'\n--- Verfuegbares Guthaben fuer Test: {bal:.4f} USDT ---')

    if bal < 5.0:
        pytest.skip(f'Zu wenig Guthaben ({bal:.2f} USDT < 5 USDT) fuer Live-Test.')

    # SEHR KLEINES Risiko fuer den Test! 0.1% vom echten Kontostand, 5x Hebel.
    # sl_pct=0.8% -> Notional = risk_usdt / (sl_pct/100) = bal*0.1%/0.8% = bal * 12.5%
    # (z.B. 108 USDT Guthaben -> ~13.5 USDT Notional, ~2.7 USDT Margin bei 5x)
    config = {'risk': {'sl_pct': 0.8, 'tp_rr': 2.0, 'leverage': 5, 'risk_per_trade_pct': 0.1}}

    print(f'-> Setze Margin-Modus: isolated | Leverage: 5x')
    exchange.set_margin_mode(SYMBOL, 'isolated')
    time.sleep(0.5)
    exchange.set_leverage(SYMBOL, 5, 'isolated')
    time.sleep(0.5)

    ticker = exchange.exchange.fetch_ticker(SYMBOL)
    price  = float(ticker['last'])

    print(f'\n[Schritt 1/3] Faelsche Forensik-Signal und oeffne Position...')
    trade_params = compute_trade_params('HYBRID', 'IMPULSE_UP', {}, price, 'long', config['risk'])
    print(f'-> Signal: LONG PEPE @ {price:.8f} | SL={trade_params.sl_price:.8f} | TP={trade_params.tp_price:.8f}')

    mock_signal = {
        'side': 'long', 'entry_price': price, 'strategy': 'HYBRID',
        'move_type': 'IMPULSE_UP', 'last_row': {}, 'trade_params': trade_params,
        'score': 42.0, 'n_met': 3, 'n_total': 4, 'hit_rate': 0.75,
    }

    ok = tm._execute_trade(exchange, SYMBOL, TIMEFRAME, mock_signal, config, telegram_config, test_logger)
    assert ok, 'FEHLER: _execute_trade() gab False zurueck.'

    print('-> Warte 5s auf Order-Ausfuehrung...')
    time.sleep(5)

    print('\n[Schritt 2/3] Ueberpruefe Position und Orders...')
    positions = exchange.fetch_open_positions(SYMBOL)
    if not positions:
        pytest.fail(f'FEHLER: Position nicht eroeffnet. Guthaben war {bal:.2f} USDT.')

    assert len(positions) == 1
    pos_info = positions[0]
    print(f'-> Position erfolgreich eroeffnet: {pos_info["side"].upper()} {pos_info.get("contracts")} PEPE')

    trigger_orders = exchange.fetch_open_trigger_orders(SYMBOL)
    if len(trigger_orders) == 0:
        print('WARNUNG: Keine Trigger-Orders im API-Return gefunden (kann bei PEPE vorkommen).')
    else:
        print(f'-> Trigger-Orders gefunden: {len(trigger_orders)}')

    print('\n[Schritt 3/3] Schliesse die Position und raeume auf...')
    print('-> Loesche Trigger-Orders VOR dem Schliessen...')
    exchange.cancel_all_orders(SYMBOL)
    time.sleep(3)

    print('-> Schliesse Position...')
    close_order = exchange.close_position(SYMBOL)
    assert close_order, 'FEHLER: Konnte Position nicht schliessen!'
    print('-> Position erfolgreich geschlossen.')
    time.sleep(4)

    print('-> Loesche verbleibende Trigger-Orders NACH dem Schliessen...')
    exchange.cancel_all_orders(SYMBOL)
    time.sleep(2)

    final_positions = exchange.fetch_open_positions(SYMBOL)
    final_orders    = exchange.fetch_open_trigger_orders(SYMBOL)

    if len(final_orders) > 0:
        print(f'WARNUNG: Noch {len(final_orders)} Trigger-Orders offen. Versuche erneutes Loeschen...')
        exchange.cancel_all_orders(SYMBOL)
        time.sleep(2)
        final_orders = exchange.fetch_open_trigger_orders(SYMBOL)

    assert len(final_positions) == 0, 'FEHLER: Position sollte geschlossen sein.'
    assert len(final_orders) == 0,    f'FEHLER: Trigger-Orders nicht sauber geloescht! ({len(final_orders)} verbleibend)'

    print('\n--- UMFASSENDER WORKFLOW-TEST ERFOLGREICH! ---')
