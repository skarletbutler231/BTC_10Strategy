"""Bulk-ingest Binance historical klines into the local SQLite store.

Source: the public Binance Vision archive (no API key), which publishes one zip
per symbol/interval/month (and per day for the current, not-yet-monthly period):

    https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip
    https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-07-05.zip

Each zip contains a single headerless CSV whose first six columns are
``open_time, open, high, low, close, volume``. Every zip has a matching
``.CHECKSUM`` (sha256) that we verify before loading.

Only 1m is ingested by design; higher intervals are resampled on read
(see ``backend/store.py``). Loading is idempotent and resumable: completed
partitions are recorded in ``ingest_log`` and skipped on re-run.

Usage:
    python -m backend.data.ingest --symbol BTCUSDT --interval 1m --from 2017-08 --to now
    python -m backend.data.ingest --from 2024-01 --to 2024-03        # a slice
    python -m backend.data.ingest --force                            # re-load everything

Note on timestamps: Binance switched the archive from millisecond to
microsecond precision in 2025. ``_to_seconds`` normalises any unit to unix
seconds so both eras load correctly.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

from .. import db

BASE = "https://data.binance.vision"
USER_AGENT = "btc-10strategy-ingest/1.0"
INSERT_BATCH = 20_000


class IngestError(RuntimeError):
    pass


# ---- time helpers -----------------------------------------------------------

def _to_seconds(ts: int) -> int:
    """Normalise a Binance timestamp (s / ms / us / ns) to unix SECONDS.

    A real unix-seconds value stays below 1e11 until the year 5138, so any
    value at or above that threshold is a finer unit; divide by 1000 until it
    lands back in the seconds range. Candle open times are minute-aligned, so
    the integer divisions are exact.
    """
    ts = int(ts)
    while ts >= 100_000_000_000:  # 1e11
        ts //= 1000
    return ts


def _month_iter(start_ym: str, end_ym: str):
    """Yield (year, month) inclusive from 'YYYY-MM' to 'YYYY-MM'."""
    sy, sm = (int(x) for x in start_ym.split("-"))
    ey, em = (int(x) for x in end_ym.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


# ---- download + verify ------------------------------------------------------

def _fetch(url: str, *, retries: int = 3) -> "bytes | None":
    """GET a URL, returning bytes, ``None`` on 404, raising on other failures."""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last_err = e
        except Exception as e:  # noqa: BLE001 - transient network; retry
            last_err = e
        time.sleep(0.5 * (attempt + 1))
    raise IngestError(f"failed to fetch {url}: {last_err}")


def _verify_checksum(zip_bytes: bytes, checksum_text: str, url: str) -> str:
    """Confirm sha256(zip_bytes) matches the .CHECKSUM file; return the digest."""
    expected = checksum_text.split()[0].strip().lower()
    actual = hashlib.sha256(zip_bytes).hexdigest()
    if expected != actual:
        raise IngestError(f"checksum mismatch for {url}: expected {expected}, got {actual}")
    return actual


def _parse_zip(zip_bytes: bytes, url: str) -> "list[tuple]":
    """Extract the single CSV from a klines zip -> list of OHLCV row tuples.

    Returns rows as (time_s, open, high, low, close, volume). Any header or
    malformed row (non-numeric open_time) is skipped.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        if not names:
            raise IngestError(f"empty zip: {url}")
        with zf.open(names[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", newline="")
            rows: "list[tuple]" = []
            for r in csv.reader(text):
                if len(r) < 6 or not r[0].lstrip("-").isdigit():
                    continue  # header / blank / malformed
                rows.append((
                    _to_seconds(r[0]),
                    float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
                ))
    return rows


# ---- DB writes --------------------------------------------------------------

def _insert(conn, symbol: str, interval: str, rows: "list[tuple]") -> int:
    """INSERT OR IGNORE candle rows; return the count of NEW rows written."""
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO candles "
        "(symbol, interval, time, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(symbol, interval, *row) for row in rows],
    )
    return conn.total_changes - before


def _record(conn, symbol, interval, partition, n_rows, sha, ts):
    conn.execute(
        "INSERT OR REPLACE INTO ingest_log "
        "(symbol, interval, partition, rows, sha256, loaded_at) VALUES (?,?,?,?,?,?)",
        (symbol, interval, partition, n_rows, sha, ts),
    )


def _done_partitions(conn, symbol, interval) -> set:
    cur = conn.execute(
        "SELECT partition FROM ingest_log WHERE symbol=? AND interval=?",
        (symbol, interval),
    )
    return {row["partition"] for row in cur}


# ---- partition loaders ------------------------------------------------------

def _load_one(conn, symbol, interval, kind, partition, now_ts) -> "int | None":
    """Download+verify+load a single monthly or daily partition.

    ``kind`` is 'monthly' or 'daily'. Returns rows written, or ``None`` if the
    archive object does not exist (404) so the caller can fall back.
    """
    fname = f"{symbol}-{interval}-{partition}.zip"
    url = f"{BASE}/data/spot/{kind}/klines/{symbol}/{interval}/{fname}"

    zip_bytes = _fetch(url)
    if zip_bytes is None:
        return None
    checksum_text = _fetch(url + ".CHECKSUM")
    if checksum_text is None:
        raise IngestError(f"missing checksum for {url}")
    sha = _verify_checksum(zip_bytes, checksum_text.decode(), url)

    rows = _parse_zip(zip_bytes, url)
    # Stream inserts in batches to bound memory / transaction size.
    written = 0
    for i in range(0, len(rows), INSERT_BATCH):
        written += _insert(conn, symbol, interval, rows[i:i + INSERT_BATCH])
    _record(conn, symbol, interval, partition, len(rows), sha, now_ts)
    conn.commit()
    return written


def _load_month_with_daily_fallback(conn, symbol, interval, y, m, today, now_ts, force):
    """Load a month; if its monthly zip is absent, load each complete day instead.

    Only days strictly before ``today`` are pulled (the live API fills the
    still-forming current day). Returns (rows_written, source_label).
    """
    ym = f"{y:04d}-{m:02d}"
    done = _done_partitions(conn, symbol, interval)

    if not force and ym in done:
        return 0, "skip(month)"

    written = _load_one(conn, symbol, interval, "monthly", ym, now_ts)
    if written is not None:
        return written, "month"

    # Monthly zip not published yet -> fall back to per-day archives.
    last_day = calendar.monthrange(y, m)[1]
    total = 0
    got_any = False
    for d in range(1, last_day + 1):
        day = date(y, m, d)
        if day >= today:
            break  # today + future: leave to the live gap-fill
        part = day.isoformat()
        if not force and part in done:
            got_any = True
            continue
        w = _load_one(conn, symbol, interval, "daily", part, now_ts)
        if w is not None:
            total += w
            got_any = True
    return total, ("daily" if got_any else "missing")


# ---- top-level run ----------------------------------------------------------

def run(symbol: str, interval: str, start_ym: str, end_ym: str, *,
        db_path=None, force: bool = False) -> dict:
    symbol = symbol.upper()
    interval = interval.lower()
    now = datetime.now(timezone.utc)
    today = now.date()
    now_ts = int(now.timestamp())
    if end_ym == "now":
        end_ym = f"{today.year:04d}-{today.month:02d}"

    conn = db.connect(db_path)
    grand_rows = 0
    t0 = time.time()
    try:
        months = list(_month_iter(start_ym, end_ym))
        print(f"Ingesting {symbol} {interval}: {start_ym} .. {end_ym} "
              f"({len(months)} month(s)) -> {db.db_path()}", flush=True)
        for y, m in months:
            written, src = _load_month_with_daily_fallback(
                conn, symbol, interval, y, m, today, now_ts, force)
            grand_rows += written
            print(f"  {y:04d}-{m:02d}  {src:<12} +{written:>7,} rows "
                  f"(total {grand_rows:,})", flush=True)
    finally:
        conn.close()

    dt = time.time() - t0
    lo, hi, cnt = coverage(symbol, interval, db_path=db_path)
    span = (f"{datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d} .. "
            f"{datetime.fromtimestamp(hi, timezone.utc):%Y-%m-%d}") if cnt else "—"
    print(f"Done in {dt:.1f}s. New rows: {grand_rows:,}. "
          f"DB now holds {cnt:,} {symbol} {interval} candles ({span}).", flush=True)
    return {"new_rows": grand_rows, "total_rows": cnt, "seconds": dt}


def coverage(symbol: str, interval: str, *, db_path=None):
    """Return (min_time, max_time, count) for a symbol/interval in the DB."""
    conn = db.connect(db_path, readonly=True)
    try:
        row = conn.execute(
            "SELECT MIN(time) lo, MAX(time) hi, COUNT(*) n "
            "FROM candles WHERE symbol=? AND interval=?",
            (symbol.upper(), interval.lower()),
        ).fetchone()
    finally:
        conn.close()
    return row["lo"], row["hi"], row["n"]


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="Ingest Binance klines into SQLite.")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1m",
                    help="only 1m is used by the platform; others are resampled on read")
    ap.add_argument("--from", dest="start", default="2017-08", help="YYYY-MM")
    ap.add_argument("--to", dest="end", default="now", help="YYYY-MM or 'now'")
    ap.add_argument("--db", default=None, help="override DB path (else MARKET_DB / data/market.db)")
    ap.add_argument("--force", action="store_true", help="re-load partitions already logged")
    args = ap.parse_args(argv)
    try:
        run(args.symbol, args.interval, args.start, args.end,
            db_path=args.db, force=args.force)
    except IngestError as e:
        print(f"ingest error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
