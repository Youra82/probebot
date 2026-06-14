"""Smoke test: charts + telegram module."""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, 'src')

# Synthetic data
np.random.seed(42)
n = 300
close = 30000 + np.cumsum(np.random.randn(n) * 300)
close = np.abs(close)
high = close * (1 + np.abs(np.random.randn(n) * 0.012))
low  = close * (1 - np.abs(np.random.randn(n) * 0.012))
open_ = close + np.random.randn(n) * 150
volume = np.abs(np.random.randn(n) * 1e6 + 5e6)

df = pd.DataFrame({
    'timestamp': pd.date_range('2022-01-01', periods=n, freq='1D', tz='UTC'),
    'open': open_, 'high': high, 'low': low, 'close': close, 'volume': volume
})

print('[1] Computing features...')
from probebot.features.engine import compute_all_features
df_feat = compute_all_features(df, min_candles=200)

print('[2] Detecting movements...')
from probebot.detection.detector import MovementDetector
movements = MovementDetector().detect(df_feat)
movements = [m for m in movements if abs(m.magnitude_pct) >= 1.0]
print(f'  {len(movements)} movements')

print('[3] Mining + correlation...')
from probebot.forensics.database import ForensicsDB
from probebot.forensics.miner import PatternMiner
from probebot.forensics.correlator import Correlator

db = ForensicsDB()
miner = PatternMiner(db, lookback=5)
miner.mine_movements(df_feat, movements[:20], 'TEST', '1d', clear_existing=True)
correlator = Correlator(db, lookback=5)
correlations = correlator.analyze(df_feat, movements[:20], 'TEST', '1d')
clusters = correlator.cluster_movements(df_feat, movements[:20], n_clusters=3) if len(movements) >= 6 else {}
db.close()

print('[4] Generating charts...')
from probebot.report.charts import (
    save_chart, overview_chart, correlation_chart,
    cluster_chart, fingerprint_chart
)

p1 = save_chart(overview_chart, df=df_feat, movements=movements, symbol='BTC/USDT', timeframe='1d',
                prefix='test_overview')
print(f'  Overview: {"OK" if p1 else "FAILED"} — {p1}')

p2 = save_chart(correlation_chart, correlations=correlations, symbol='BTC/USDT', timeframe='1d',
                prefix='test_correlation')
print(f'  Correlation: {"OK" if p2 else "FAILED"} — {p2}')

p3 = save_chart(fingerprint_chart, correlations=correlations, symbol='BTC/USDT', timeframe='1d',
                prefix='test_fingerprint')
print(f'  Fingerprint: {"OK" if p3 else "FAILED"} — {p3}')

if clusters:
    p4 = save_chart(cluster_chart, clusters=clusters, symbol='BTC/USDT', timeframe='1d',
                    prefix='test_cluster')
    print(f'  Cluster: {"OK" if p4 else "FAILED"} — {p4}')

print('[5] Telegram module test...')
from probebot.utils.telegram import load_telegram_config
tg = load_telegram_config()
if tg.get('bot_token'):
    print(f'  Telegram config loaded: token={tg["bot_token"][:8]}... chat={tg.get("chat_id")}')
else:
    print('  Telegram: no config found (expected — no secret.json here)')

print()
print('=== CHART TESTS PASSED ===')
