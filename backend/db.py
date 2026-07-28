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


def dotenv_value(key: str) -> "str | None":
    """Read one key from the repo-root .env (best effort).

    Without this, a module run directly (e.g. `python -m backend.data.ingest_stream`,
    or a manual query) would NOT see MARKET_DB — which only lives in .env — and would
    silently target the local ``data/market.db`` instead of the configured shared DB.
    Loading it here makes db_path() correct however the code is invoked. Other
    settings that only live in .env (PMDATA_API_KEY, PMDATA_ARCHIVE) read through
    the same helper.
    """
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return None
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, val = line.partition("=")
            if k.strip() == key:
                return val.split("#", 1)[0].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def env_value(key: str) -> "str | None":
    """``key`` from the real environment, falling back to the repo-root .env."""
    return os.environ.get(key) or dotenv_value(key)


def db_path() -> Path:
    """Resolved path to the SQLite file.

    Precedence: MARKET_DB in the environment > MARKET_DB in .env > the local default.
    """
    env = env_value("MARKET_DB")
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
    p_up_bin   REAL,             -- Binance-fed model P(up) at this tick (predictionBinance.pUp)
    p_up_chain REAL,             -- Chainlink-fed model P(up) at this tick (prediction.pUp)
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

-- ============================================================================
-- PMData (api.pmdata.dev) full-history Polymarket L2 order book, folded to a
-- 1-second grid by ``backend.data.ingest_pmdata``. The raw daily archives are
-- ~30M events/day, so SQLite holds the per-second state and the untouched
-- .zip archives on disk stay the re-ingestable source of truth.
--
-- Complements pm_quote (the pmqb live capture): pm_quote is this machine's own
-- tick capture with the model's p_up attached and only reaches back to the day
-- the bot started; pm_l2_* reaches back to PMData's 2026-02-13 recording start
-- and carries real book depth. Both key on the same 5m ``start_ts`` grid.

-- Top of book + cumulative depth, one row per (5m window, second).
CREATE TABLE IF NOT EXISTS pm_l2_quote (
    start_ts INTEGER NOT NULL,   -- window this second belongs to (-> pm_window)
    time     INTEGER NOT NULL,   -- unix SECONDS (UTC), exchange event time
    bid      REAL,               -- best bid, as reported by the feed
    ask      REAL,               -- best ask, as reported by the feed
    mid      REAL,               -- (bid+ask)/2 when both sides are present
    bid_sz   REAL,               -- resting size at the best bid (shares)
    ask_sz   REAL,
    bid_d1   REAL,               -- cumulative bid size within 1c of the best bid
    ask_d1   REAL,
    bid_d5   REAL,               -- ... within 5c
    ask_d5   REAL,
    bid_d10  REAL,               -- ... within 10c
    ask_d10  REAL,
    n_events INTEGER NOT NULL,   -- L2 events folded into this second
    PRIMARY KEY (start_ts, time)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_pm_l2_quote_time ON pm_l2_quote(time);

-- Full ladder for the same 1-second grid. `ladder` is a zstd-compressed array
-- of 2000 little-endian uint32: index p in [0,1000) is the bid resting at price
-- p/1000, index 1000+p the ask at p/1000; the value is shares*100 (Polymarket
-- sizes carry at most 2dp). Polymarket quotes a 1c grid but drops to 0.1c in
-- the tails, so the ladder is 0.001-resolution. Decode with
-- ``backend.pm_store.decode_ladder``. A rowid table on purpose: ~330-byte blobs
-- are well past the size where WITHOUT ROWID stops paying off.
CREATE TABLE IF NOT EXISTS pm_l2_book (
    start_ts INTEGER NOT NULL,
    time     INTEGER NOT NULL,
    ladder   BLOB NOT NULL,
    UNIQUE (start_ts, time)
);

-- One row per PMData market file (i.e. per 5m window), with the feed's own
-- resolution. Kept separate from pm_window so a PMData backfill never disturbs
-- the Chainlink-sourced windows the existing backtests read.
CREATE TABLE IF NOT EXISTS pm_l2_market (
    start_ts   INTEGER PRIMARY KEY,   -- window open, unix SECONDS (5m grid)
    slug       TEXT NOT NULL,         -- 'btc-updown-5m-<start_ts>'
    first_ts   INTEGER,               -- first/last event time in the file
    last_ts    INTEGER,
    n_events   INTEGER,
    n_book     INTEGER,               -- full-snapshot events (ladder resyncs)
    n_change   INTEGER,               -- price_change events
    outcome    TEXT,                  -- feed's winning_outcome: 'yes' | 'no' | NULL
    resolved_up INTEGER,              -- 1 if UP, 0 if DOWN, NULL if undetermined
    resolved_src TEXT,                -- 'feed' (market_resolved event, authoritative)
                                      -- | 'terminal' (derived from the settled book)
    data_date  TEXT                   -- PMData archive day this came from
) WITHOUT ROWID;

-- Which PMData daily archives have been downloaded and folded in, so a re-run
-- skips them. PMData bills by *day unlocked* (shared across series and data
-- type), which is why the archives are kept rather than re-fetched.
CREATE TABLE IF NOT EXISTS pmdata_day (
    series    TEXT NOT NULL,          -- 'btc-5m'
    data_type TEXT NOT NULL,          -- 'poly_l2'
    data_date TEXT NOT NULL,          -- 'YYYY-MM-DD'
    markets   INTEGER,
    events    INTEGER,
    sec_rows  INTEGER,
    zip_bytes INTEGER,
    loaded_at INTEGER NOT NULL,       -- unix seconds
    PRIMARY KEY (series, data_type, data_date)
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
        _migrate(conn)
    return conn


# Columns added to tables that already exist in a deployed DB. CREATE TABLE IF
# NOT EXISTS silently leaves an older table alone, so each addition needs an
# explicit, idempotent ALTER here.
_ADDED_COLUMNS = (
    ("pm_l2_market", "resolved_src", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if cols and column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


def init_db(path: "str | Path | None" = None) -> Path:
    """Create the schema if needed and return the DB path."""
    conn = connect(path)
    try:
        return Path(conn.execute("PRAGMA database_list").fetchall()[0]["file"] or db_path())
    finally:
        conn.close()
