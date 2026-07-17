"""Quick smoke test with synthetic data. pytest -- Teil der probebot-Test-Suite."""
import sys
sys.path.insert(0, 'src')

import numpy as np
import pandas as pd


def test_full_pipeline_smoke():
    # Generate synthetic OHLCV data (500 candles)
    np.random.seed(42)
    n = 500
    close = 30000 + np.cumsum(np.random.randn(n) * 200)
    close = np.abs(close)
    high = close * (1 + np.abs(np.random.randn(n) * 0.01))
    low  = close * (1 - np.abs(np.random.randn(n) * 0.01))
    open_ = close + np.random.randn(n) * 100
    volume = np.abs(np.random.randn(n) * 1e6 + 5e6)

    df = pd.DataFrame({
        'timestamp': pd.date_range('2022-01-01', periods=n, freq='1D', tz='UTC'),
        'open': open_, 'high': high, 'low': low, 'close': close, 'volume': volume
    })

    print('Testing full feature pipeline...')
    from probebot.features.engine import compute_all_features, feature_vector
    result = compute_all_features(df, min_candles=200)
    print(f'  Candles: {len(result)}')
    print(f'  Features: {len(result.columns)} columns')
    assert len(result) > 0
    assert len(result.columns) > 0

    print()
    print('Testing movement detection...')
    from probebot.detection.detector import MovementDetector
    detector = MovementDetector()
    movements = detector.detect(result)
    print(f'  Detected: {len(movements)} movements')
    assert len(movements) > 0, "keine Bewegungen im synthetischen Datensatz erkannt"
    m = movements[0]
    print(f'  First: {m.move_type} | {m.direction} | {m.magnitude_pct:+.2f}%')

    print()
    print('Testing feature vector extraction...')
    fv = feature_vector(result, 250)
    numeric = {k: v for k, v in fv.items() if isinstance(v, float) and not np.isnan(v)}
    print(f'  Non-NaN features: {len(numeric)} / {len(fv)}')
    assert len(numeric) > 0

    print()
    print('Testing SQLite database...')
    from probebot.forensics.database import ForensicsDB
    db = ForensicsDB()
    mid = db.insert_movement('TEST', '1d', '2022-06-01', 'BREAKDOWN', 'DOWN', -3.5, 2.1, {}, {}, {})
    print(f'  Inserted movement id={mid}')
    rows = db.get_movements('TEST', '1d')
    print(f'  Retrieved {len(rows)} movements')
    assert len(rows) >= 1
    db.clear_movements('TEST', '1d')
    db.close()

    print()
    print('Testing pattern miner...')
    from probebot.forensics.database import ForensicsDB
    from probebot.forensics.miner import PatternMiner
    db2 = ForensicsDB()
    miner = PatternMiner(db2, lookback=5)
    miner.mine_movements(result, movements[:5], 'TEST', '1d', clear_existing=True)
    db2.close()

    print()
    print('Testing correlator...')
    from probebot.forensics.database import ForensicsDB
    from probebot.forensics.correlator import Correlator
    db3 = ForensicsDB()
    corr = Correlator(db3, lookback=5)
    corr_result, corr_meta = corr.analyze(result, movements[:10], 'TEST', '1d')
    print(f'  Move types analyzed: {list(corr_result.keys())}')
    assert isinstance(corr_result, dict)
    for mtype, ranked in corr_result.items():
        top = [r for r in ranked if abs(r["t_statistic"]) >= 2.0][:3]
        print(f'  {mtype}: {len(ranked)} features, {len(top)} significant')
    db3.close()

    print()
    print('=== ALL SMOKE TESTS PASSED ===')
