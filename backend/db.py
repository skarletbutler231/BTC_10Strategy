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

-- ============================================================================
-- Polymarket BTC 5-minute UP/DOWN markets (for realistic Polymarket backtests).
-- One row per 5-minute window; `start_ts` is the window open on the UTC 5m grid,
-- so it joins directly to a BTC 5m candle's open time. Prices are the Chainlink
-- settlement references (same feed as BTCUSD_CL). resolved_up: 1 up / 0 down.
CREATE TABLE IF NOT EXISTS pm_window (
    start_ts    INTEGER PRIMARY KEY,   -- window open, unix SECONDS (UTC, 5m grid)
    market_id   TEXT,                  -- Polymarket market id
    slug        TEXT,                  -- e.g. 'btc-updown-5m-<start_ts>'
    end_ts      INTEGER,               -- window close = start_ts + 300
    start_price REAL,                  -- Chainlink price at window open
    end_price   REAL,                  -- Chainlink price at window close (== next window's start)
    resolved_up INTEGER,               -- 1 up / 0 down / NULL if unresolved
    resolved_src TEXT                  -- 'chainlink' (recorded outcome) | 'boundary' (next-window start)
) WITHOUT ROWID;

-- Tick-level YES(UP) share price for each window (the tradeable Polymarket odds).
-- One row per (window, second); `yes` is the mid, with book top-of-book bid/ask.
-- Backtests read this to price an entry realistically instead of assuming 0.5.
CREATE TABLE IF NOT EXISTS pm_quote (
    start_ts INTEGER NOT NULL,   -- window this tick belongs to (-> pm_window)
    time     INTEGER NOT NULL,   -- tick time, unix SECONDS (UTC)
    yes      REAL,               -- YES(UP) mid price in [0,1]
    yes_bid  REAL,
    yes_ask  REAL,
    PRIMARY KEY (start_ts, time)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_pm_quote_time ON pm_quote(time);

-- Resumable byte cursor for the append-only stream.jsonl ingester, plus the
-- still-forming ('unsealed') trailing 1-minute candle held back between runs so
-- an incomplete minute is never written as if complete.
CREATE TABLE IF NOT EXISTS stream_cursor (
    source     TEXT PRIMARY KEY,   -- absolute path of the stream file
    offset     INTEGER NOT NULL,   -- bytes consumed (start of first unprocessed line)
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cl_partial (
    symbol  TEXT PRIMARY KEY,      -- carrying symbol (BTCUSD_CL)
    minute  INTEGER NOT NULL,      -- open time of the unsealed minute
    open    REAL, high REAL, low REAL, close REAL,
    last_ts INTEGER NOT NULL       -- newest tick folded into this minute
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
