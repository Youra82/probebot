"""
test_trade_manager.py — Korrektheits-Checks fuer trade_manager.py mit
GEMOCKTER Exchange (kein Bitget-Zugriff, kein Guthaben noetig, kein Netzwerk).

Anders als ltbbot/tests/test_workflow.py und test_trailing_stop.py (die
echte Trigger-Orders auf Bitget platzieren und ein gefuelltes Konto in
secret.json brauchen), prueft dieser Test dieselbe Kernfrage — werden
Positionen korrekt eroeffnet/geschlossen, TP/SL korrekt gesetzt — komplett
gegen eine unittest.mock.MagicMock-Exchange. Kein pytest -- eigenstaendig
ausfuehrbar, gleiche Konvention wie test_smoke.py/test_gpu_parity.py.

Geprueft:
  1. _execute_trade() HYBRID: Positionsgroesse (compute_contracts-Formel),
     Entry-Order, SL/TP-Trigger-Preise, Tracker-Inhalt
  2. _execute_trade() BREAKOUT: Trailing-Stop-Pfad statt festem TP
  3. SL-Order schlaegt fehl -> Notfall-Close der Position (kein Tracker-Eintrag)
  4. full_trade_cycle() bei offener Position: ensure_tp_sl re-created fehlende
     SL/TP Trigger-Orders
  5. full_trade_cycle() erkennt TP-Close korrekt und setzt Tracker auf idle

Ausfuehrung:
    python test_trade_manager.py
"""
import sys
import tempfile
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, 'src')

from probebot.utils import trade_manager as tm

logger = logging.getLogger("test-trade-manager")
logger.addHandler(logging.NullHandler())


def _mock_exchange(**overrides):
    ex = MagicMock()
    ex.fetch_balance_usdt.return_value = 1000.0
    ex.fetch_min_amount.return_value = 0.001
    ex.place_market_order.return_value = {'average': None, 'filled': None}
    ex.place_trigger_market_order.side_effect = [{'id': 'sl-1'}, {'id': 'tp-1'}]
    ex.fetch_open_positions.return_value = []
    ex.fetch_open_trigger_orders.return_value = []
    ex.fetch_closed_trigger_orders.return_value = []
    for k, v in overrides.items():
        setattr(ex, k, v)
    return ex


def _signal(side='long', entry_price=100.0, strategy='HYBRID', move_type='IMPULSE_UP', last_row=None):
    from probebot.strategy.signal_logic import compute_trade_params
    row = last_row or {}
    risk = {'sl_pct': 1.0, 'tp_rr': 2.0}
    tp = compute_trade_params(strategy, move_type, row, entry_price, side, risk)
    return {
        'side': side, 'entry_price': entry_price, 'strategy': strategy,
        'move_type': move_type, 'last_row': row, 'trade_params': tp,
        'score': 42.0, 'n_met': 3, 'n_total': 4, 'hit_rate': 0.75,
    }


def test_execute_trade_hybrid():
    print("Test 1: _execute_trade() HYBRID -- Positionsgroesse, Entry, SL/TP ...")
    ex = _mock_exchange()
    ex.place_market_order.return_value = {'average': 100.0, 'filled': 10.0}

    config = {'risk': {'sl_pct': 1.0, 'tp_rr': 2.0, 'leverage': 10, 'risk_per_trade_pct': 1.0}}
    signal = _signal(side='long', entry_price=100.0, strategy='HYBRID')

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(tm, 'TRACKER_DIR', Path(tmpdir)), \
             patch('probebot.utils.telegram.send_message'):
            ok = tm._execute_trade(ex, 'BTC/USDT:USDT', '1h', signal, config, {}, logger)

            if not ok:
                print("  FEHLER: _execute_trade gab False zurueck.")
                return False

            # balance=1000, risk=1% -> risk_usdt=10; sl_dist = 100*1%=1.0 -> contracts=10
            entry_side, entry_amount = ex.place_market_order.call_args[0][1], ex.place_market_order.call_args[0][2]
            if entry_side != 'buy' or abs(entry_amount - 10.0) > 1e-6:
                print(f"  FEHLER: Entry-Order side={entry_side} amount={entry_amount} (erwartet buy/10.0)")
                return False

            # SL bei fallback sl_pct=1% -> 99.0, TP bei tp_rr=2 -> 102.0
            sl_call = ex.place_trigger_market_order.call_args_list[0]
            tp_call = ex.place_trigger_market_order.call_args_list[1]
            sl_side, sl_amount, sl_price = sl_call[0][1], sl_call[0][2], sl_call[0][3]
            tp_side, tp_amount, tp_price = tp_call[0][1], tp_call[0][2], tp_call[0][3]

            ok2 = True
            if sl_side != 'sell' or abs(sl_price - 99.0) > 1e-6:
                print(f"  FEHLER: SL side={sl_side} price={sl_price} (erwartet sell/99.0)")
                ok2 = False
            if tp_side != 'sell' or abs(tp_price - 102.0) > 1e-6:
                print(f"  FEHLER: TP side={tp_side} price={tp_price} (erwartet sell/102.0)")
                ok2 = False

            tracker = tm.read_tracker('BTC/USDT:USDT', '1h')
            if tracker.get('status') != 'open' or tracker.get('sl_order_id') != 'sl-1' \
               or tracker.get('tp_order_id') != 'tp-1' or abs(tracker.get('contracts', 0) - 10.0) > 1e-6:
                print(f"  FEHLER: Tracker unerwartet: {tracker}")
                ok2 = False

        print(f"  Entry: {entry_side} {entry_amount} @ 100.0  |  SL: {sl_price}  TP: {tp_price}  "
              f"|  Tracker: status={tracker.get('status')}  ->  {'OK' if ok2 else 'FEHLGESCHLAGEN'}")
        return ok2


def test_execute_trade_breakout_trailing():
    print("\nTest 2: _execute_trade() BREAKOUT -- Trailing-Stop statt festem TP ...")
    ex = _mock_exchange()
    ex.place_market_order.return_value = {'average': 100.0, 'filled': 5.0}
    ex.place_trailing_stop_order.return_value = {'id': 'trail-1'}
    ex.place_trigger_market_order.side_effect = [{'id': 'sl-1'}]  # nur SL, TP laeuft ueber trailing

    config = {'risk': {'sl_pct': 1.0, 'tp_rr': 2.0, 'leverage': 10, 'risk_per_trade_pct': 1.0}}
    signal = _signal(side='long', entry_price=100.0, strategy='BREAKOUT',
                      move_type='BREAKOUT_UP', last_row={'swing_low': 97.0})

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(tm, 'TRACKER_DIR', Path(tmpdir)), \
             patch('probebot.utils.telegram.send_message'):
            ok = tm._execute_trade(ex, 'ETH/USDT:USDT', '1h', signal, config, {}, logger)

            if not ok:
                print("  FEHLER: _execute_trade gab False zurueck.")
                return False

            trailing_called = ex.place_trailing_stop_order.called
            tp_fixed_called = ex.place_trigger_market_order.call_count > 1
            tracker = tm.read_tracker('ETH/USDT:USDT', '1h')

            ok2 = True
            if not trailing_called:
                print("  FEHLER: place_trailing_stop_order wurde nicht aufgerufen.")
                ok2 = False
            if tp_fixed_called:
                print("  FEHLER: zusaetzlich fester TP via place_trigger_market_order platziert.")
                ok2 = False
            if not tracker.get('use_trailing'):
                print(f"  FEHLER: Tracker use_trailing nicht gesetzt: {tracker}")
                ok2 = False

        print(f"  trailing_called={trailing_called}  tracker.use_trailing={tracker.get('use_trailing')}  "
              f"->  {'OK' if ok2 else 'FEHLGESCHLAGEN'}")
        return ok2


def test_sl_failure_triggers_emergency_close():
    print("\nTest 3: SL-Order schlaegt fehl -> Notfall-Close der Position ...")
    ex = _mock_exchange()
    ex.place_market_order.return_value = {'average': 100.0, 'filled': 10.0}
    ex.place_trigger_market_order.side_effect = Exception("Bitget: insufficient margin")

    config = {'risk': {'sl_pct': 1.0, 'tp_rr': 2.0, 'leverage': 10, 'risk_per_trade_pct': 1.0}}
    signal = _signal(side='long', entry_price=100.0, strategy='HYBRID')

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(tm, 'TRACKER_DIR', Path(tmpdir)), \
             patch('probebot.utils.telegram.send_message'):
            ok = tm._execute_trade(ex, 'SOL/USDT:USDT', '1h', signal, config, {}, logger)
            tracker = tm.read_tracker('SOL/USDT:USDT', '1h')

        ok2 = True
        if ok is not False:
            print(f"  FEHLER: _execute_trade sollte False zurueckgeben, gab {ok}")
            ok2 = False
        if not ex.close_position.called:
            print("  FEHLER: close_position() wurde bei SL-Fehler nicht aufgerufen.")
            ok2 = False
        if tracker.get('status') == 'open':
            print(f"  FEHLER: Tracker zeigt trotz fehlgeschlagener SL-Order 'open': {tracker}")
            ok2 = False

        print(f"  return={ok}  close_position_called={ex.close_position.called}  "
              f"tracker.status={tracker.get('status')}  ->  {'OK' if ok2 else 'FEHLGESCHLAGEN'}")
        return ok2


def test_ensure_tp_sl_recreates_missing_orders():
    print("\nTest 4: full_trade_cycle() bei offener Position -- ensure_tp_sl legt fehlende SL/TP neu an ...")
    ex = _mock_exchange()
    ex.fetch_open_positions.return_value = [{'side': 'long', 'unrealizedPnl': 5.0, 'contracts': 10.0}]
    ex.fetch_open_trigger_orders.return_value = []  # SL/TP beide weg
    ex.place_trigger_market_order.side_effect = [{'id': 'sl-new'}]

    tracker_data = {
        'status': 'open', 'symbol': 'BTC/USDT:USDT', 'timeframe': '1h', 'side': 'long',
        'strategy': 'HYBRID', 'move_type': 'IMPULSE_UP', 'entry_price': 100.0,
        'sl_price': 99.0, 'tp_price': 102.0, 'use_trailing': False,
        'trailing_activation': None, 'trailing_pct': 0.0,
        'sl_source': 'x', 'tp_source': 'y', 'contracts': 10.0,
        'sl_order_id': 'sl-old', 'tp_order_id': None,
        'active_since': '2024-01-01T00:00:00+00:00', 'candle_blocked_until': '',
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(tm, 'TRACKER_DIR', Path(tmpdir)), \
             patch('probebot.utils.telegram.send_message'):
            tm._write_tracker('BTC/USDT:USDT', '1h', tracker_data)
            tm.full_trade_cycle(ex, 'BTC/USDT:USDT', '1h', {}, {}, {}, logger, signal=None)
            tracker = tm.read_tracker('BTC/USDT:USDT', '1h')

        ok2 = True
        if not ex.place_trigger_market_order.called:
            print("  FEHLER: fehlende SL-Order wurde nicht neu angelegt.")
            ok2 = False
        if tracker.get('sl_order_id') != 'sl-new':
            print(f"  FEHLER: Tracker sl_order_id nicht aktualisiert: {tracker.get('sl_order_id')}")
            ok2 = False
        if tracker.get('status') != 'open':
            print(f"  FEHLER: Position sollte weiter 'open' bleiben: {tracker}")
            ok2 = False

        print(f"  SL neu angelegt: {ex.place_trigger_market_order.called}  "
              f"neue sl_order_id={tracker.get('sl_order_id')}  ->  {'OK' if ok2 else 'FEHLGESCHLAGEN'}")
        return ok2


def test_full_trade_cycle_detects_tp_close():
    print("\nTest 5: full_trade_cycle() erkennt TP-Close und setzt Tracker auf idle ...")
    ex = _mock_exchange()
    ex.fetch_open_positions.return_value = []  # Position auf der Boerse bereits weg
    ex.fetch_closed_trigger_orders.return_value = [
        {'id': 'tp-1', 'status': 'filled'},
        {'id': 'sl-1', 'status': 'canceled'},
    ]

    tracker_data = {
        'status': 'open', 'symbol': 'LTC/USDT:USDT', 'timeframe': '1h', 'side': 'long',
        'strategy': 'HYBRID', 'move_type': 'IMPULSE_UP', 'entry_price': 50.0,
        'sl_price': 49.5, 'tp_price': 51.0, 'use_trailing': False,
        'trailing_activation': None, 'trailing_pct': 0.0,
        'sl_source': 'x', 'tp_source': 'y', 'contracts': 5.0,
        'sl_order_id': 'sl-1', 'tp_order_id': 'tp-1',
        'active_since': '2024-01-01T00:00:00+00:00', 'candle_blocked_until': '',
    }

    sent_messages = []

    def _capture(token, chat_id, msg):
        sent_messages.append(msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(tm, 'TRACKER_DIR', Path(tmpdir)), \
             patch('probebot.utils.telegram.send_message', side_effect=_capture):
            tm._write_tracker('LTC/USDT:USDT', '1h', tracker_data)
            tm.full_trade_cycle(ex, 'LTC/USDT:USDT', '1h', {}, {}, {}, logger, signal=None)
            tracker = tm.read_tracker('LTC/USDT:USDT', '1h')

        ok2 = True
        if tracker.get('status') != 'idle':
            print(f"  FEHLER: Tracker sollte 'idle' sein: {tracker}")
            ok2 = False
        if not sent_messages or 'TP' not in sent_messages[0]:
            print(f"  FEHLER: Keine/falsche Telegram-Nachricht gesendet: {sent_messages}")
            ok2 = False

        print(f"  tracker.status={tracker.get('status')}  telegram_msg_enthaelt_TP="
              f"{bool(sent_messages and 'TP' in sent_messages[0])}  ->  {'OK' if ok2 else 'FEHLGESCHLAGEN'}")
        return ok2


def main():
    results = [
        test_execute_trade_hybrid(),
        test_execute_trade_breakout_trailing(),
        test_sl_failure_triggers_emergency_close(),
        test_ensure_tp_sl_recreates_missing_orders(),
        test_full_trade_cycle_detects_tp_close(),
    ]
    print("\n" + "=" * 60)
    if all(results):
        print("ALLE TESTS BESTANDEN")
    else:
        print(f"FEHLGESCHLAGEN — {sum(1 for r in results if not r)}/{len(results)} Tests")
    print("=" * 60)
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
