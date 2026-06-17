# src/probebot/utils/exchange.py
"""Bitget Futures API wrapper for probebot live trading."""
import ccxt
import pandas as pd
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class Exchange:
    def __init__(self, account_config: dict):
        self.account = account_config
        self.exchange = ccxt.bitget({
            'apiKey':   account_config.get('api_key') or account_config.get('apiKey'),
            'secret':   account_config.get('api_secret') or account_config.get('secret'),
            'password': account_config.get('passphrase') or account_config.get('password'),
            'options':  {'defaultType': 'swap'},
            'enableRateLimit': True,
        })
        try:
            self.markets = self.exchange.load_markets()
            logger.info("Maerkte erfolgreich geladen.")
        except Exception as e:
            logger.critical(f"Maerkte konnten nicht geladen werden: {e}")
            self.markets = {}

    # ── OHLCV ─────────────────────────────────────────────────────────────────

    def fetch_recent_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        if not self.markets:
            return pd.DataFrame()
        tf_ms  = self.exchange.parse_timeframe(timeframe) * 1000
        since  = self.exchange.milliseconds() - tf_ms * limit
        rows   = []

        while since < self.exchange.milliseconds():
            try:
                batch = self.exchange.fetch_ohlcv(symbol, timeframe, since, 200)
                if not batch:
                    break
                rows.extend(batch)
                since = batch[-1][0] + tf_ms
                time.sleep(self.exchange.rateLimit / 1000)
            except ccxt.RateLimitExceeded:
                logger.warning("Rate limit — warte 5s...")
                time.sleep(5)
            except Exception as e:
                logger.error(f"OHLCV-Fehler: {e}")
                break

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.sort_values('timestamp', inplace=True)
        df.drop_duplicates('timestamp', keep='last', inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df.iloc[-limit:]

    # ── Balance ───────────────────────────────────────────────────────────────

    def fetch_balance_usdt(self) -> float:
        try:
            params  = {'marginCoin': 'USDT', 'productType': 'USDT-FUTURES'}
            balance = self.exchange.fetch_balance(params=params)
            usdt = 0.0
            if 'USDT' in balance and balance['USDT'].get('free') is not None:
                usdt = float(balance['USDT']['free'])
            elif 'info' in balance and isinstance(balance['info'], list):
                for item in balance['info']:
                    if item.get('marginCoin') == 'USDT':
                        usdt = float(item.get('available', 0.0))
                        break
            if usdt == 0.0 and 'total' in balance and 'USDT' in balance.get('total', {}):
                usdt = float(balance['total']['USDT'])
            logger.info(f"Verfuegbares Guthaben: {usdt:.2f} USDT")
            return usdt
        except ccxt.AuthenticationError as e:
            logger.critical(f"Authentifizierungsfehler: {e}")
            return 0.0
        except Exception as e:
            logger.error(f"Fehler beim Guthaben-Abruf: {e}", exc_info=True)
            return 0.0

    # ── Precision ─────────────────────────────────────────────────────────────

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        try:
            return self.exchange.amount_to_precision(symbol, amount)
        except Exception:
            return str(amount)

    def price_to_precision(self, symbol: str, price: float) -> str:
        try:
            return self.exchange.price_to_precision(symbol, price)
        except Exception:
            return str(price)

    def fetch_min_amount(self, symbol: str) -> float:
        try:
            if symbol not in self.markets:
                self.markets = self.exchange.load_markets()
            min_a = self.markets[symbol].get('limits', {}).get('amount', {}).get('min')
            return float(min_a) if min_a is not None else 0.0
        except Exception:
            return 0.0

    # ── Positions ─────────────────────────────────────────────────────────────

    def fetch_open_positions(self, symbol: str) -> list:
        try:
            params = {'productType': 'USDT-FUTURES', 'marginCoin': 'USDT'}
            positions = self.exchange.fetch_positions([symbol], params=params)
            return [
                p for p in positions
                if abs(float(p.get('contracts') or p.get('contractSize') or 0)) > 1e-9
            ]
        except Exception as e:
            logger.error(f"Fehler bei offenen Positionen fuer {symbol}: {e}", exc_info=True)
            return []

    # ── Margin / Leverage ─────────────────────────────────────────────────────

    def set_margin_mode(self, symbol: str, margin_mode: str = 'isolated'):
        try:
            params = {'productType': 'USDT-FUTURES', 'marginCoin': 'USDT'}
            self.exchange.set_margin_mode(margin_mode.lower(), symbol, params=params)
            logger.info(f"Margin-Modus {symbol}: {margin_mode}")
        except ccxt.ExchangeError as e:
            if any(x in str(e) for x in ['Margin mode is the same', '40051']):
                logger.debug(f"Margin-Modus bereits {margin_mode}.")
            else:
                logger.error(f"Fehler Margin-Modus: {e}")
        except Exception as e:
            logger.error(f"Fehler Margin-Modus: {e}")

    def set_leverage(self, symbol: str, leverage: int, margin_mode: str = 'isolated'):
        try:
            params = {
                'productType': 'USDT-FUTURES',
                'marginCoin':  'USDT',
                'marginMode':  margin_mode.lower(),
            }
            self.exchange.set_leverage(leverage, symbol, params=params)
            logger.info(f"Hebel {symbol}: {leverage}x")
        except ccxt.ExchangeError as e:
            if any(x in str(e) for x in ['Leverage not changed', '40052']):
                logger.debug(f"Hebel bereits {leverage}x.")
            else:
                logger.error(f"Fehler Hebel: {e}")
        except Exception as e:
            logger.error(f"Fehler Hebel: {e}")

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_market_order(self, symbol: str, side: str, amount: float,
                           reduce: bool = False, margin_mode: str = 'isolated'):
        try:
            params = {
                'productType': 'USDT-FUTURES',
                'marginCoin':  'USDT',
                'marginMode':  margin_mode,
                'hedged':      True,
                'reduceOnly':  reduce,
            }
            amt_str = self.amount_to_precision(symbol, amount)
            logger.info(f"Market Order: {side.upper()} {amt_str} {symbol} reduce={reduce}")
            return self.exchange.create_order(symbol, 'market', side, float(amt_str), params=params)
        except ccxt.InsufficientFunds as e:
            logger.error(f"Nicht genuegend Guthaben: {e}")
            raise
        except Exception as e:
            logger.error(f"Fehler Market Order: {e}", exc_info=True)
            raise

    def place_trigger_market_order(self, symbol: str, side: str, amount: float,
                                   trigger_price: float, reduce: bool = False):
        try:
            amt_str = self.amount_to_precision(symbol, amount)
            tp_str  = self.price_to_precision(symbol, trigger_price)
            params  = {
                'triggerPrice': tp_str,
                'reduceOnly':   reduce,
                'productType':  'USDT-FUTURES',
                'marginMode':   'isolated',
                'hedged':       True,
            }
            logger.info(f"Trigger Order: {side.upper()} {amt_str} {symbol} @ {tp_str}")
            return self.exchange.create_order(symbol, 'market', side, float(amt_str), params=params)
        except Exception as e:
            logger.error(f"Fehler Trigger Order: {e}", exc_info=True)
            raise

    def cancel_all_orders(self, symbol: str):
        for stop_flag in [False, True]:
            try:
                self.exchange.cancel_all_orders(
                    symbol, params={'productType': 'USDT-FUTURES', 'stop': stop_flag}
                )
                time.sleep(0.5)
            except ccxt.ExchangeError as e:
                if not any(x in str(e) for x in ['Order not found', 'no order to cancel', '22001']):
                    logger.error(f"Fehler beim Stornieren (stop={stop_flag}): {e}")
            except Exception as e:
                logger.error(f"Fehler beim Stornieren: {e}")

    def close_position(self, symbol: str):
        try:
            positions = self.fetch_open_positions(symbol)
            if not positions:
                logger.warning(f"Keine offene Position zum Schliessen: {symbol}")
                return None
            pos        = positions[0]
            close_side = 'sell' if pos['side'] == 'long' else 'buy'
            amount     = float(pos.get('contracts') or pos.get('contractSize') or 0)
            logger.info(f"Schliesse {pos['side']} Position {symbol} ({amount} Kontrakte)")
            return self.place_market_order(symbol, close_side, amount, reduce=True)
        except Exception as e:
            logger.error(f"Fehler beim Schliessen der Position: {e}")
            raise
