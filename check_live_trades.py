# check_live_trades.py
"""
probebot — echte Bitget-Trade-Historie fuer einen Zeitraum abrufen.

Ergaenzt show_results.sh Modus 1 (der nur den hypothetischen Backtest
zeigt): dieses Skript zeigt was der Live-Bot TATSAECHLICH auf dem
Exchange-Account ausgefuehrt hat (Entry- und SL/TP-Exit-Fills).

Nutzung (im .venv mit ccxt + secret.json):
  python3 check_live_trades.py --start 2026-07-01
  python3 check_live_trades.py --start 2026-07-01 --end 2026-08-14
  python3 check_live_trades.py --start 2026-07-01 --symbol ADA/USDT:USDT
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from probebot.utils.exchange import Exchange  # noqa: E402


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _fmt_ts(ms) -> str:
    if not ms:
        return '?'
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')


def fetch_symbol_trades(exchange: Exchange, symbol: str, since_ms: int, until_ms: int) -> list:
    """Alle Fills (Entry + Exit) fuer ein Symbol im Zeitraum, chronologisch."""
    all_trades = []
    fetch_since = since_ms
    while True:
        try:
            batch = exchange.exchange.fetch_my_trades(
                symbol, since=fetch_since, limit=100,
                params={'productType': 'USDT-FUTURES'},
            )
        except Exception as e:
            print(f"  Fehler beim Abruf {symbol}: {e}")
            break
        if not batch:
            break
        all_trades.extend(batch)
        last_ts = batch[-1].get('timestamp')
        if last_ts is None or last_ts <= fetch_since or len(batch) < 100:
            break
        fetch_since = last_ts + 1

    return [t for t in all_trades
            if t.get('timestamp') and since_ms <= t['timestamp'] <= until_ms]


def main():
    parser = argparse.ArgumentParser(description='probebot -- echte Bitget-Trades im Zeitraum')
    parser.add_argument('--start', required=True, help='YYYY-MM-DD')
    parser.add_argument('--end', default=None, help='YYYY-MM-DD, Standard: heute')
    parser.add_argument('--symbol', default=None, help='Nur dieses Symbol (z.B. ADA/USDT:USDT)')
    args = parser.parse_args()

    end_str = args.end or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    since_ms = _to_ms(args.start)
    until_ms = _to_ms(end_str) + 24 * 3600 * 1000 - 1

    with open(os.path.join(PROJECT_ROOT, 'secret.json'), encoding='utf-8') as f:
        secrets = json.load(f)
    with open(os.path.join(PROJECT_ROOT, 'settings.json'), encoding='utf-8') as f:
        settings = json.load(f)

    account = secrets.get('probebot', {})
    if isinstance(account, list):
        account = account[0] if account else {}
    if not account.get('api_key') and not account.get('apiKey'):
        print("Kein 'probebot'-Account in secret.json.")
        sys.exit(1)

    strategies = settings.get('live_trading_settings', {}).get('active_strategies', [])
    symbols = sorted({s['symbol'] for s in strategies if isinstance(s, dict)})
    if args.symbol:
        symbols = [args.symbol]

    print(f"Zeitraum: {args.start} -> {end_str}  |  {len(symbols)} Symbole")
    exchange = Exchange(account)

    total_fills = 0
    for symbol in symbols:
        trades = fetch_symbol_trades(exchange, symbol, since_ms, until_ms)
        if not trades:
            continue
        print(f"\n{symbol}  ({len(trades)} Fills)")
        for t in trades:
            info = t.get('info', {}) or {}
            pnl = (info.get('profit') or info.get('realizedPnl') or
                   info.get('pnl') or info.get('totalProfits'))
            pnl_s = f"  PnL={pnl}" if pnl not in (None, '0', '0.00000000') else ''
            fee = t.get('fee', {}) or {}
            print(f"  {_fmt_ts(t.get('timestamp')):<17}  {str(t.get('side','?')):<5}  "
                  f"px={t.get('price')}  amt={t.get('amount')}  "
                  f"fee={fee.get('cost', '?')}{pnl_s}")
        total_fills += len(trades)

    print(f"\nGesamt: {total_fills} Fills im Zeitraum ueber {len(symbols)} Symbole.")
    if total_fills == 0:
        print("Keine Fills gefunden -- entweder war der Bot inaktiv oder es gab "
              "in diesem Zeitraum keine ausgefuehrten Trades.")


if __name__ == '__main__':
    main()
