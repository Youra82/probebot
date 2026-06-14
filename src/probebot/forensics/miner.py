"""
Pattern miner: for each detected movement, extract the pre-condition feature
vectors and store them in the database. Also finds similar past movements.
"""
import numpy as np
import pandas as pd
from typing import List, Optional
from sklearn.preprocessing import RobustScaler
from sklearn.metrics.pairwise import cosine_similarity

from ..detection.detector import Movement
from ..features.engine import feature_vector
from .database import ForensicsDB


class PatternMiner:
    def __init__(self, db: ForensicsDB, lookback: int = 10):
        self.db = db
        self.lookback = lookback

    def mine_movements(
        self,
        df: pd.DataFrame,
        movements: List[Movement],
        symbol: str,
        timeframe: str,
        clear_existing: bool = True,
    ) -> None:
        """
        For each movement: extract pre/during features and store in DB.
        """
        if clear_existing:
            self.db.clear_movements(symbol, timeframe)

        print(f"  [miner] mining {len(movements)} movements...")
        for m in movements:
            i = m.idx

            # Feature vector AT the movement candle
            during = feature_vector(df, i)

            # Average feature vector over the lookback period before the move
            pre_vectors = []
            for offset in range(1, self.lookback + 1):
                j = i - offset
                if j >= 0:
                    pre_vectors.append(feature_vector(df, j))

            preconditions = _average_feature_vectors(pre_vectors)

            # Additional pre-condition signals
            preconditions['_lookback_bars'] = self.lookback
            preconditions['_t_minus_1'] = _safe_fv(df, i - 1)
            preconditions['_t_minus_3'] = _safe_fv(df, i - 3)
            preconditions['_t_minus_5'] = _safe_fv(df, i - 5)

            ts = df['timestamp'].iloc[i] if 'timestamp' in df.columns else str(i)
            self.db.insert_movement(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=ts,
                move_type=m.move_type,
                direction=m.direction,
                magnitude_pct=m.magnitude_pct,
                atr_multiple=m.atr_multiple,
                context=m.context,
                preconditions=preconditions,
                during=during,
            )

        print(f"  [miner] stored {len(movements)} movements in DB")

    def find_similar(
        self,
        df: pd.DataFrame,
        target_idx: int,
        symbol: str,
        timeframe: str,
        move_type: Optional[str] = None,
        top_n: int = 5,
    ) -> List[dict]:
        """
        Find historical movements most similar to the movement at target_idx
        by cosine similarity of their pre-condition vectors.
        """
        target_fv = _average_feature_vectors([
            feature_vector(df, target_idx - offset)
            for offset in range(1, self.lookback + 1)
            if target_idx - offset >= 0
        ])

        all_movements = self.db.get_movements(symbol, timeframe, move_type=move_type)
        if not all_movements:
            return []

        target_keys = set(k for k, v in target_fv.items() if isinstance(v, float) and not np.isnan(v))
        candidates = []
        for mov in all_movements:
            prec = mov.get('preconditions', {})
            if not prec:
                continue
            common_keys = target_keys & set(k for k, v in prec.items() if isinstance(v, float))
            if len(common_keys) < 5:
                continue
            candidates.append((mov, prec, common_keys))

        if not candidates:
            return []

        # Build common feature set
        all_keys = sorted(set.union(*[ck for _, _, ck in candidates]))
        target_vec = np.array([target_fv.get(k, 0.0) for k in all_keys]).reshape(1, -1)
        cand_vecs = np.array([
            [prec.get(k, 0.0) for k in all_keys]
            for _, prec, _ in candidates
        ])

        # Robust scaling
        all_data = np.vstack([target_vec, cand_vecs])
        scaler = RobustScaler()
        try:
            all_scaled = scaler.fit_transform(all_data)
            target_scaled = all_scaled[0:1]
            cand_scaled = all_scaled[1:]
            sims = cosine_similarity(target_scaled, cand_scaled)[0]
        except Exception:
            sims = np.zeros(len(candidates))

        ranked = sorted(zip(sims, [c[0] for c in candidates]), key=lambda x: -x[0])
        result = []
        for sim, mov in ranked[:top_n]:
            mov['similarity_score'] = round(float(sim), 4)
            result.append(mov)
        return result


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _average_feature_vectors(vectors: List[dict]) -> dict:
    if not vectors:
        return {}
    keys = vectors[0].keys()
    result = {}
    for k in keys:
        vals = [v[k] for v in vectors if isinstance(v.get(k), (int, float)) and not np.isnan(float(v[k]))]
        result[k] = float(np.mean(vals)) if vals else np.nan
    return result


def _safe_fv(df: pd.DataFrame, idx: int) -> dict:
    if idx < 0 or idx >= len(df):
        return {}
    return feature_vector(df, idx)
