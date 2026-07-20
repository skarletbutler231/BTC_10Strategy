"""DB-backed candle reader with on-read resampling and a live gap-fill.

The platform calls :func:`get_candles`, which returns candle dicts in the exact
shape :func:`backend.binance.fetch_klines` returns::

    {"time": <unix seconds>, "open", "high", "low", "close", "volume": <float>}

Design (see project plan):
  * Only 1-minute candles are stored (ingested by ``backend.data.ingest``).
  * Higher intervals are **resampled on read** by bucketing 1m candles onto the
    UTC-epoch grid — the same alignment Binance uses to build its own klines.
  * Reads are a **hybrid**: history comes from SQLite; if the requested window
    extends past what's ingested (e.g. the still-forming current day), the tail
    is transparently fetched from the Binance API and spliced on.

Set ``USE_DB=0`` to bypass the store entirely and read straight from Binance
(useful before the first backfill, or to compare).
"""

from __future__ import annotations

import os

from . import binance, db

# interval -> seconds. Derived from the Binance millisecond table so the two
# stay in sync.
INTERVAL_SECONDS = {k: v // 1000 for k, v in binance.INTERVAL_MS.items()}
BASE_INTERVAL = "1m"
BASE_SECONDS = INTERVAL_SECONDS[BASE_INTERVAL]


def use_db() -> bool:
    """Whether the DB path is enabled (default on; USE_DB=0 disables)."""
    return os.environ.get("USE_DB", "1").lower() not in ("0", "false", "no")


# ---- low-level reads --------------------------------------------------------

def _db_max_time(symbol: str) -> "int | None":
    """Newest 1m candle time (unix seconds) in the DB, or None if empty."""
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001 - DB file not built yet
        return None
    try:
        row = conn.execute(
            "SELECT MAX(time) AS hi FROM candles WHERE symbol=? AND interval=?",
            (symbol, BASE_INTERVAL),
        ).fetchone()
        return row["hi"] if row else None
    finally:
        conn.close()


def _read_1m(symbol: str, lo_sec: int, hi_sec: int) -> "list[dict]":
    """1m candle dicts from the DB with lo_sec <= time <= hi_sec (ascending)."""
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001
        return []
    try:
        cur = conn.execute(
            "SELECT time, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND interval=? AND time BETWEEN ? AND ? ORDER BY time",
            (symbol, BASE_INTERVAL, lo_sec, hi_sec),
        )
        return [dict(r) for r in cur]
    finally:
        conn.close()


# ---- resampling -------------------------------------------------------------

def _resample(base: "list[dict]", interval_secs: int,
              start_sec: int, end_sec: int) -> "list[dict]":
    """Aggregate 1m candles into ``interval_secs`` buckets on the epoch grid.

    Emits buckets whose OPEN time is in [start_sec, end_sec] (Binance's own
    higher-interval semantics). open=first, high=max, low=min, close=last,
    volume=sum, over the 1m candles that fall inside each bucket.
    """
    if interval_secs == BASE_SECONDS:
        return [c for c in base if start_sec <= c["time"] <= end_sec]

    out: "list[dict]" = []
    cur: "dict | None" = None
    cur_bucket = None
    for c in base:
        bucket = c["time"] - (c["time"] % interval_secs)
        if bucket < start_sec or bucket > end_sec:
            continue
        if bucket != cur_bucket:
            if cur is not None:
                out.append(cur)
            cur_bucket = bucket
            cur = {
                "time": bucket,
                "open": c["open"], "high": c["high"], "low": c["low"],
                "close": c["close"], "volume": c["volume"],
            }
        else:
            cur["high"] = max(cur["high"], c["high"])
            cur["low"] = min(cur["low"], c["low"])
            cur["close"] = c["close"]
            cur["volume"] += c["volume"]
    if cur is not None:
        out.append(cur)
    return out


def _merge_1m(db_rows: "list[dict]", live_rows: "list[dict]") -> "list[dict]":
    """Union two 1m series by time (DB wins on overlap), sorted ascending."""
    by_time = {c["time"]: c for c in live_rows}
    by_time.update({c["time"]: c for c in db_rows})  # DB is authoritative
    return [by_time[t] for t in sorted(by_time)]


# ---- public API -------------------------------------------------------------

def get_candles(symbol: str, interval: str, start_ms: int, end_ms: int) -> "list[dict]":
    """Return candle dicts for [start_ms, end_ms] at ``interval``.

    History from SQLite (resampled from 1m as needed); the tail beyond ingested
    data is filled from the Binance API. Falls back to a pure Binance fetch if
    the DB is disabled or unavailable.
    """
    symbol = symbol.upper()
    interval = interval.lower()
    if interval not in INTERVAL_SECONDS:
        raise binance.BinanceError(f"unsupported interval: {interval}")

    # Store disabled -> behave exactly like the old direct path.
    if not use_db():
        return binance.fetch_klines(symbol, interval, start_ms, end_ms)

    interval_secs = INTERVAL_SECONDS[interval]
    start_sec = start_ms // 1000
    end_sec = end_ms // 1000

    if interval_secs != BASE_SECONDS:
        # Read 1m from the first bucket boundary at/after start (so the leading
        # bucket is complete) through the END of the bucket that contains
        # end_sec (so the trailing bucket is built from its full interval of 1m
        # data — matching how Binance stamps a kline by its open time).
        read_lo = ((start_sec + interval_secs - 1) // interval_secs) * interval_secs
        read_hi = (end_sec - end_sec % interval_secs) + interval_secs - 1
    else:
        read_lo, read_hi = start_sec, end_sec

    base = _read_1m(symbol, read_lo, read_hi)
    db_max = _db_max_time(symbol)

    # Fill the tail past ingested data (forming day / DB behind), best-effort.
    if db_max is None or read_hi > db_max:
        live_from = (db_max + BASE_SECONDS) if (db_max is not None and db_max >= read_lo) \
            else read_lo
        live_to_ms = (read_hi + 1) * 1000
        if live_from * 1000 < live_to_ms:
            try:
                live = binance.fetch_klines(symbol, BASE_INTERVAL, live_from * 1000, live_to_ms)
                base = _merge_1m(base, live)
            except binance.BinanceError:
                if not base:
                    raise  # nothing in DB and live failed -> surface the error

    return _resample(base, interval_secs, start_sec, end_sec)


def coverage(symbol: str) -> dict:
    """Ingested 1m coverage for a symbol: {min, max, count} (unix seconds)."""
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001
        return {"min": None, "max": None, "count": 0}
    try:
        r = conn.execute(
            "SELECT MIN(time) lo, MAX(time) hi, COUNT(*) n "
            "FROM candles WHERE symbol=? AND interval=?",
            (symbol.upper(), BASE_INTERVAL),
        ).fetchone()
        return {"min": r["lo"], "max": r["hi"], "count": r["n"]}
    finally:
        conn.close()
