"""Ingest Chainlink Data Streams BTC/USD into the local SQLite store.

Chainlink is the *settlement* source for Polymarket's 5m/15m up-down markets;
Binance (``ingest.py``) is only an ~85% proxy. This records the real thing under
the symbol ``BTCUSD_CL`` so it can be backtested and compared.

Two jobs, one code path — the run window is always
``[max(last_stored+1, now-retention), last_complete_minute]``:

  * First (cold) run backfills everything Data Streams still retains (~3-4 weeks;
    the API returns HTTP 400 past its window). Depth cap via ``--days``.
  * Each later run (schedule per-minute via cron) just appends new minutes, and
    gap-fills automatically if a run was missed — as long as the gap is inside
    the retention window.

Reports arrive at ~1 Hz; we fold them into 1-minute OHLC candles on the UTC grid
(open=first, high=max, low=min, close=last). Data Streams carries **no volume**,
so ``volume`` is stored as 0 — volume-based strategies must skip this symbol.
Only *complete* minutes are written; the still-forming current minute is left for
the next run. Idempotent and resumable via ``INSERT OR IGNORE``.

Usage:
    python3 -m backend.data.ingest_chainlink                 # append / cold backfill
    python3 -m backend.data.ingest_chainlink --days 7        # limit cold-start depth
    python3 -m backend.data.ingest_chainlink --days 0.03     # tiny slice (testing)
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

from .. import chainlink, db

SYMBOL = "BTCUSD_CL"
INTERVAL = "1m"
MINUTE = 60
PAGE_LIMIT = 100          # API hard cap for /reports/page
RETENTION_DAYS = 25.0     # request depth; self-corrects to the real edge below
PAGE_SLEEP = 0.05         # be gentle between page calls
MAX_RETRIES = 4
EDGE_STEP = 6 * 3600      # when a cold start is before retention, jump forward by this


class ChainlinkIngestError(RuntimeError):
    pass


# ---- cursor / bounds --------------------------------------------------------

def _last_stored(conn) -> "int | None":
    row = conn.execute(
        "SELECT MAX(time) AS hi FROM candles WHERE symbol=? AND interval=?",
        (SYMBOL, INTERVAL),
    ).fetchone()
    return row["hi"] if row and row["hi"] is not None else None


def _fetch_page_retry(cursor: int) -> list:
    """chainlink.fetch_page with backoff; raises after MAX_RETRIES."""
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            return chainlink.fetch_page(cursor, limit=PAGE_LIMIT)
        except chainlink.ChainlinkError as e:
            last = e
            # 400 usually means "before retention" — not retryable; surface it.
            if "HTTP 400" in str(e):
                raise
            time.sleep(0.5 * (attempt + 1))
    raise ChainlinkIngestError(f"page fetch failed at {cursor}: {last}")


# ---- OHLC fold --------------------------------------------------------------

def _fold_report(minutes: dict, feed_time: int, price: float) -> None:
    """Add one (timestamp, price) tick to its 1-minute OHLC bucket."""
    m = feed_time - (feed_time % MINUTE)
    c = minutes.get(m)
    if c is None:
        minutes[m] = {"time": m, "open": price, "high": price,
                      "low": price, "close": price, "_last": feed_time}
    else:
        if price > c["high"]:
            c["high"] = price
        if price < c["low"]:
            c["low"] = price
        # reports page in ascending time, so the latest wins the close
        if feed_time >= c["_last"]:
            c["close"] = price
            c["_last"] = feed_time


# ---- run --------------------------------------------------------------------

def run(days: float = RETENTION_DAYS, *, db_path=None) -> dict:
    now = int(time.time())
    last_complete = (now - (now % MINUTE)) - 1   # end of the last full minute
    retention_floor = now - int(days * 86400)

    conn = db.connect(db_path)
    try:
        last = _last_stored(conn)
        if last is None:
            start = retention_floor
            print(f"cold start: backfilling from ~{days:g} day(s) ago", flush=True)
        else:
            start = last + 1
            if start < retention_floor:
                gap_d = (retention_floor - start) / 86400
                print(f"WARNING: last candle is {gap_d:.1f}d older than the "
                      f"retention window — that gap is unrecoverable.", flush=True)
                start = retention_floor

        if start > last_complete:
            print("nothing to do (already current).", flush=True)
            return {"new_rows": 0, "pages": 0}

        print(f"Ingesting {SYMBOL} {INTERVAL}: "
              f"{datetime.fromtimestamp(start, timezone.utc):%Y-%m-%d %H:%M} .. "
              f"{datetime.fromtimestamp(last_complete, timezone.utc):%Y-%m-%d %H:%M} UTC "
              f"-> {db.db_path()}", flush=True)

        minutes: dict = {}
        cursor = start
        pages = 0
        t0 = time.time()
        while cursor <= last_complete:
            try:
                reports = _fetch_page_retry(cursor)
            except chainlink.ChainlinkError as e:
                # HTTP 400 before we've read anything = cold start is past the
                # retention edge; jump forward to find where history begins.
                if "HTTP 400" in str(e) and not minutes and cursor < last_complete:
                    cursor = min(cursor + EDGE_STEP, last_complete)
                    continue
                raise
            if not reports:
                break
            pages += 1
            newest = cursor
            for rep in reports:
                try:
                    px = chainlink.report_price(rep)
                except Exception:  # noqa: BLE001 - skip an undecodable report
                    continue
                ft = px["feed_time"]
                if ft > last_complete:
                    continue
                _fold_report(minutes, ft, px["price"])
                if ft > newest:
                    newest = ft
            if newest <= cursor:      # no forward progress -> stop
                break
            cursor = newest + 1
            if pages % 50 == 0:
                span = datetime.fromtimestamp(newest, timezone.utc)
                print(f"  ...{pages} pages, at {span:%Y-%m-%d %H:%M}, "
                      f"{len(minutes):,} minutes so far", flush=True)
            time.sleep(PAGE_SLEEP)

        rows = [(SYMBOL, INTERVAL, c["time"], c["open"], c["high"],
                 c["low"], c["close"], 0.0)
                for c in (minutes[k] for k in sorted(minutes))]
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO candles "
            "(symbol, interval, time, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        written = conn.total_changes - before

        lo = min(minutes) if minutes else None
        hi = max(minutes) if minutes else None
        dt = time.time() - t0
        span = (f"{datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d %H:%M} .. "
                f"{datetime.fromtimestamp(hi, timezone.utc):%Y-%m-%d %H:%M}"
                if minutes else "—")
        print(f"Done in {dt:.1f}s over {pages} page(s). "
              f"{len(minutes):,} minutes built, {written:,} new rows ({span} UTC).",
              flush=True)
        return {"new_rows": written, "pages": pages, "minutes": len(minutes)}
    finally:
        conn.close()


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="Ingest Chainlink BTC/USD into SQLite.")
    ap.add_argument("--days", type=float, default=RETENTION_DAYS,
                    help="cold-start backfill depth in days (ignored once data exists)")
    ap.add_argument("--db", default=None, help="override DB path")
    args = ap.parse_args(argv)
    try:
        run(args.days, db_path=args.db)
    except (chainlink.ChainlinkError, ChainlinkIngestError) as e:
        print(f"ingest error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
