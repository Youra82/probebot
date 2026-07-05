"""SQLite persistence for movement forensics."""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional


_ARTIFACTS_ROOT = (
    Path(os.environ["PROBEBOT_ARTIFACTS_ROOT"])
    if os.environ.get("PROBEBOT_ARTIFACTS_ROOT")
    else Path(__file__).parent.parent.parent.parent
)
DB_PATH = _ARTIFACTS_ROOT / "artifacts" / "db" / "forensics.db"


class ForensicsDB:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._setup()

    def _setup(self):
        c = self.conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript("""
            CREATE TABLE IF NOT EXISTS movements (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                move_type   TEXT NOT NULL,
                direction   TEXT NOT NULL,
                magnitude_pct REAL,
                atr_multiple  REAL,
                context     TEXT,      -- JSON
                preconditions TEXT,    -- JSON: feature vector T-1..T-lookback averaged
                during      TEXT,      -- JSON: feature vector AT move candle
                drill_down  TEXT,      -- JSON: MTF analysis results
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_movements_symbol_tf
                ON movements(symbol, timeframe, move_type);

            CREATE TABLE IF NOT EXISTS commonalities (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT NOT NULL,
                timeframe       TEXT NOT NULL,
                move_type       TEXT NOT NULL,
                direction       TEXT NOT NULL,
                feature         TEXT NOT NULL,
                n_events        INTEGER,
                mean_before     REAL,
                std_before      REAL,
                mean_all        REAL,
                std_all         REAL,
                t_statistic     REAL,
                lift_factor     REAL,
                predictive_pct  REAL,
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_common_type
                ON commonalities(symbol, timeframe, move_type, direction);

            CREATE TABLE IF NOT EXISTS scan_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT,
                timeframe   TEXT,
                start_date  TEXT,
                end_date    TEXT,
                n_movements INTEGER,
                ran_at      TEXT DEFAULT (datetime('now'))
            );
        """)
        c.commit()

    def insert_movement(
        self,
        symbol: str,
        timeframe: str,
        timestamp: str,
        move_type: str,
        direction: str,
        magnitude_pct: float,
        atr_multiple: float,
        context: dict,
        preconditions: dict,
        during: dict,
        drill_down: Optional[dict] = None,
    ) -> int:
        cur = self.conn.execute("""
            INSERT INTO movements
                (symbol, timeframe, timestamp, move_type, direction,
                 magnitude_pct, atr_multiple, context, preconditions, during, drill_down)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol, timeframe, str(timestamp), move_type, direction,
            magnitude_pct, atr_multiple,
            json.dumps(context, default=str),
            json.dumps(preconditions, default=str),
            json.dumps(during, default=str),
            json.dumps(drill_down, default=str) if drill_down else None,
        ))
        self.conn.commit()
        return cur.lastrowid

    def update_drill_down(self, movement_id: int, drill_down: dict):
        self.conn.execute(
            "UPDATE movements SET drill_down=? WHERE id=?",
            (json.dumps(drill_down, default=str), movement_id)
        )
        self.conn.commit()

    def get_movements(
        self,
        symbol: str,
        timeframe: str,
        move_type: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> List[dict]:
        q = "SELECT * FROM movements WHERE symbol=? AND timeframe=?"
        params = [symbol, timeframe]
        if move_type:
            q += " AND move_type=?"
            params.append(move_type)
        if direction:
            q += " AND direction=?"
            params.append(direction)
        q += " ORDER BY timestamp"
        rows = self.conn.execute(q, params).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for key in ('context', 'preconditions', 'during', 'drill_down'):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except Exception:
                        pass
            result.append(d)
        return result

    def upsert_commonality(self, record: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO commonalities
                (symbol, timeframe, move_type, direction, feature,
                 n_events, mean_before, std_before, mean_all, std_all,
                 t_statistic, lift_factor, predictive_pct, updated_at)
            VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,datetime('now'))
        """, (
            record['symbol'], record['timeframe'], record['move_type'],
            record['direction'], record['feature'],
            record['n_events'], record['mean_before'], record['std_before'],
            record['mean_all'], record['std_all'],
            record['t_statistic'], record['lift_factor'], record['predictive_pct'],
        ))
        self.conn.commit()

    def get_commonalities(
        self,
        symbol: str,
        timeframe: str,
        move_type: str,
        direction: Optional[str] = None,
        min_t_stat: float = 2.0,
        top_n: int = 20,
    ) -> List[dict]:
        q = """
            SELECT * FROM commonalities
            WHERE symbol=? AND timeframe=? AND move_type=?
              AND abs(t_statistic) >= ?
        """
        params = [symbol, timeframe, move_type, min_t_stat]
        if direction:
            q += " AND direction=?"
            params.append(direction)
        q += " ORDER BY abs(t_statistic) DESC LIMIT ?"
        params.append(top_n)
        rows = self.conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def log_scan(self, symbol: str, timeframe: str, start: str, end: str, n: int):
        self.conn.execute(
            "INSERT INTO scan_log (symbol, timeframe, start_date, end_date, n_movements) VALUES (?,?,?,?,?)",
            (symbol, timeframe, start, end, n)
        )
        self.conn.commit()

    def clear_movements(self, symbol: str, timeframe: str):
        self.conn.execute(
            "DELETE FROM movements WHERE symbol=? AND timeframe=?",
            (symbol, timeframe)
        )
        self.conn.execute(
            "DELETE FROM commonalities WHERE symbol=? AND timeframe=?",
            (symbol, timeframe)
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
