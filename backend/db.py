"""SQLite market-data store: connection + schema.

One table holds 1-minute OHLCV candles keyed by (symbol, interval, time). Only
1m is ingested; higher intervals are resampled on read in ``store.py``. Time is
unix SECONDS (the same convention the rest of the project uses).

The DB path is ``data/market.db`` at the project root by default; override with
the ``MARKET_DB`` env var. The file is gitignored and built by
``python -m backend.data.ingest``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "market.db"


def db_path() -> Path:
    """Resolved path to the SQLite file (honours the MARKET_DB env var)."""
    env = os.environ.get("MARKET_DB")
    return Path(env).expanduser() if env else DEFAULT_DB_PATH


_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol   TEXT    NOT NULL,
    interval TEXT    NOT NULL,
    time     INTEGER NOT NULL,   -- candle open time, unix SECONDS (UTC)
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   REAL    NOT NULL,
    PRIMARY KEY (symbol, interval, time)
) WITHOUT ROWID;

-- Tracks which monthly/daily partitions have been fully ingested so a re-run
-- can skip them without re-downloading. 'covered_to' is the exclusive upper
-- bound (unix seconds) that has been loaded for this partition.
CREATE TABLE IF NOT EXISTS ingest_log (
    symbol    TEXT    NOT NULL,
    interval  TEXT    NOT NULL,
    partition TEXT    NOT NULL,   -- e.g. '2024-01' (monthly) or '2026-07-05' (daily)
    rows      INTEGER NOT NULL,
    sha256    TEXT,
    loaded_at INTEGER NOT NULL,   -- unix seconds
    PRIMARY KEY (symbol, interval, partition)
);
"""


def connect(path: "str | Path | None" = None, *, readonly: bool = False) -> sqlite3.Connection:
    """Open (and, for writers, initialise) the market-data DB.

    Pragmas favour a single-writer bulk-ingest + many-reader workload:
    WAL journaling, a generous page cache, and memory temp storage.
    """
    p = Path(path) if path else db_path()
    if not readonly:
        p.parent.mkdir(parents=True, exist_ok=True)

    if readonly:
        # Fail loudly rather than silently create an empty DB for read paths.
        uri = f"file:{p}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    else:
        conn = sqlite3.connect(p, timeout=30)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-65536")  # ~64 MB page cache
    if not readonly:
        conn.executescript(_SCHEMA)
    return conn


def init_db(path: "str | Path | None" = None) -> Path:
    """Create the schema if needed and return the DB path."""
    conn = connect(path)
    try:
        return Path(conn.execute("PRAGMA database_list").fetchall()[0]["file"] or db_path())
    finally:
        conn.close()
