import ccxt
import json
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Optional


TIMEFRAME_MINUTES = {
    '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
    '1h': 60, '2h': 120, '4h': 240, '6h': 360, '12h': 720,
    '1d': 1440, '1w': 10080
}

DRILL_DOWN_CHAIN = ['1d', '4h', '1h', '15m', '5m', '1m']


class DataLoader:
    def __init__(self, exchange_id: str = 'bitget', secret_path: str = 'secret.json'):
        self.exchange = self._init_exchange(exchange_id, secret_path)

    def _init_exchange(self, exchange_id: str, secret_path: str):
        creds = {}
        try:
            p = Path(secret_path)
            if not p.exists():
                p = Path(__file__).parent.parent.parent.parent / secret_path
            with open(p) as f:
                secrets = json.load(f)
            creds = secrets.get('probebot', secrets.get('ltbbot', {}))
        except Exception:
            pass

        exchange_class = getattr(ccxt, exchange_id)
        return exchange_class({
            'apiKey': creds.get('api_key', ''),
            'secret': creds.get('api_secret', ''),
            'password': creds.get('passphrase', ''),
            'options': {'defaultType': 'swap'},
            'enableRateLimit': True,
        })

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        since = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp() * 1000)
        if end_date:
            until = int(datetime.strptime(end_date, '%Y-%m-%d').timestamp() * 1000)
        else:
            until = int(datetime.now().timestamp() * 1000)

        tf_ms = self.exchange.parse_timeframe(timeframe) * 1000
        all_candles = []
        try:
            while since < until:
                candles = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=200)
                if not candles:
                    break
                all_candles.extend(candles)
                last_ts = candles[-1][0]
                if last_ts >= until:
                    break
                since = last_ts + tf_ms
        except ccxt.BadSymbol:
            # Symbol existiert nicht (mehr) auf der Exchange — wie "keine Daten"
            # behandeln, damit der Aufrufer das als regulären Skip erkennt statt
            # an einem unbehandelten Traceback zu scheitern.
            return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df = df[df['timestamp'] <= pd.to_datetime(until, unit='ms', utc=True)]
        df = df.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
        return df

    def fetch_window_around(
        self,
        symbol: str,
        timeframe: str,
        center_ts: pd.Timestamp,
        candles_before: int = 50,
        candles_after: int = 20,
    ) -> pd.DataFrame:
        tf_minutes = TIMEFRAME_MINUTES.get(timeframe, 60)
        ms_before = candles_before * tf_minutes * 60 * 1000
        ms_after = candles_after * tf_minutes * 60 * 1000
        center_ms = int(center_ts.timestamp() * 1000)

        since_ms = center_ms - ms_before
        until_ms = center_ms + ms_after

        start_str = datetime.utcfromtimestamp(since_ms / 1000).strftime('%Y-%m-%d')
        end_str = datetime.utcfromtimestamp(until_ms / 1000).strftime('%Y-%m-%d %H:%M')

        tf_ms = self.exchange.parse_timeframe(timeframe) * 1000
        all_candles = []
        since = since_ms

        try:
            while since < until_ms:
                batch = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=200)
                if not batch:
                    break
                all_candles.extend(batch)
                last_ts = batch[-1][0]
                if last_ts >= until_ms:
                    break
                since = last_ts + tf_ms
        except Exception:
            pass

        if not all_candles:
            return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df = df[df['timestamp'] <= pd.to_datetime(until_ms, unit='ms', utc=True)]
        df = df.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
        return df
