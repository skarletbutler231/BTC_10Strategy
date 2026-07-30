"""Sub-minute candle reader, built from a 1-second BTC table.

The main store only holds 1-minute candles and resamples *upward*, so anything
below 1m is invisible to it. This module fills that gap by aggregating a
separate 1-second table (``btc_1s.db``) into 5s / 10s / 15s / 20s / 30s bars on
the UTC-epoch grid, the same alignment Binance uses.

    from backend import second_store
    second_store.get_candles("BTCUSDT", "15s", start_ms, end_ms)

TWO LIMITATIONS, both intrinsic to the source table and both worth knowing
before trusting a backtest built on these bars:

1. **No true high/low.** The table stores only ``(ts, open, close)`` per second,
   so a bar's high and low can only be the max/min of the per-second opens and
   closes it contains. That cannot see inside a second. Measured against real
   Binance 1m klines over the same week, the reconstruction reproduces open and
   close essentially exactly (close matched on 10,080/10,080 bars) but
   understates the true bar range by ~4%, with 39% of bars missing the real
   extreme. Wick-sensitive logic is therefore biased: at 1s every bar is a pure
   body with zero wicks, so pin-bar detection cannot fire at all, and the bias
   shrinks as the bar size grows.

2. **No volume.** Reported as 0.0. Any volume-based strategy is meaningless here.

Coverage is whatever the 1s table holds — currently 2026-02-01 to 2026-07-27 —
which is far shorter than the 9 years of 1m history in market.db. Requests
outside it return only the overlapping part.

Path resolution: ``BTC_1S_DB`` in the environment or .env, else the default
below, which sits beside the shared market.db.
"""

from __future__ import annotations

import os
import sqlite3
from typing import List, Optional

from .db import env_value

# interval name -> seconds per bar. 1s is included so the raw table is
# reachable, but see limitation 1 above before using it.
INTERVAL_SECONDS = {"1s": 1, "5s": 5, "10s": 10, "15s": 15, "20s": 20, "30s": 30}

DEFAULT_DB = "/work/david/PolyMarket/database/btc_1s.db"
TABLE = "btc_1s"
SYMBOL = "BTCUSDT"          # the table holds this pair only

# A browser chart plus a backtest payload gets unusable well before this; 5s
# bars hit it after ~9 days, so a wide range at a small interval fails loudly
# rather than trying to ship millions of bars.
MAX_BARS = 200_000


class SubMinuteError(RuntimeError):
    """Raised for an unusable request or a missing 1s database."""


def db_path() -> str:
    return env_value("BTC_1S_DB") or DEFAULT_DB


def available() -> bool:
    return os.path.exists(db_path())


def is_sub_minute(interval: str) -> bool:
    return interval.lower() in INTERVAL_SECONDS


def _connect() -> sqlite3.Connection:
    path = db_path()
    if not os.path.exists(path):
        raise SubMinuteError(
            f"1-second database not found at {path}. Set BTC_1S_DB to point at it.")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def coverage() -> dict:
    """{'min','max','count'} of the 1s table, or zeros when it is absent."""
    if not available():
        return {"min": None, "max": None, "count": 0}
    conn = _connect()
    try:
        lo, hi, n = conn.execute(
            f"SELECT MIN(ts), MAX(ts), COUNT(*) FROM {TABLE}").fetchone()
        return {"min": lo, "max": hi, "count": n}
    finally:
        conn.close()


def get_candles(symbol: str, interval: str, start_ms: int, end_ms: int) -> List[dict]:
    """Aggregate the 1s table into `interval` bars covering [start_ms, end_ms].

    Bars are stamped by their OPEN time on the UTC-epoch grid. A bar is emitted
    for every bucket that has at least one second of data; gaps in the source
    simply produce no bar rather than a synthetic one.
    """
    interval = interval.lower()
    secs = INTERVAL_SECONDS.get(interval)
    if secs is None:
        raise SubMinuteError(f"not a sub-minute interval: {interval}")
    if symbol.upper() != SYMBOL:
        raise SubMinuteError(
            f"the 1-second table only holds {SYMBOL}, not {symbol.upper()}")

    start_sec = start_ms // 1000
    end_sec = end_ms // 1000
    # widen to whole buckets so the first and last bar are not half-built
    lo = (start_sec // secs) * secs
    hi = ((end_sec // secs) + 1) * secs - 1

    est = (hi - lo + 1) // secs
    if est > MAX_BARS:
        raise SubMinuteError(
            f"{est:,} {interval} bars requested, limit is {MAX_BARS:,}. "
            f"Narrow the date range or use a larger interval.")

    conn = _connect()
    try:
        cur = conn.execute(
            f"SELECT ts, open, close FROM {TABLE} WHERE ts BETWEEN ? AND ? ORDER BY ts",
            (lo, hi))
        out: List[dict] = []
        bucket: Optional[int] = None
        cur_bar: Optional[dict] = None
        for ts, o, cl in cur:
            b = ts - ts % secs
            if b != bucket:
                if cur_bar is not None:
                    out.append(cur_bar)
                bucket = b
                cur_bar = {"time": b, "open": o, "high": max(o, cl),
                           "low": min(o, cl), "close": cl, "volume": 0.0}
            else:
                if o > cur_bar["high"]:
                    cur_bar["high"] = o
                if cl > cur_bar["high"]:
                    cur_bar["high"] = cl
                if o < cur_bar["low"]:
                    cur_bar["low"] = o
                if cl < cur_bar["low"]:
                    cur_bar["low"] = cl
                cur_bar["close"] = cl
        if cur_bar is not None:
            out.append(cur_bar)
    finally:
        conn.close()

    # trim to the requested span (the widening above can add an edge bar)
    return [c for c in out if start_sec <= c["time"] <= end_sec]
