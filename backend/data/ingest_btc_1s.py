"""Seed ``market.db`` with per-second BTCUSDT candles from the standalone btc_1s.db.

``btc_1s.db`` is a separate SQLite file holding Binance BTCUSDT **1-second**
klines as ``btc_1s(ts, open, close)`` — verified against the 1m candles already
in ``market.db``: for every minute the 1s open at ``:00`` equals the 1m open and
the 1s close at ``:59`` equals the 1m close.

This folds that file into the shared ``candles`` table as
``(symbol='BTCUSDT', interval='1s')`` so the per-second history lives in the same
store as everything else and ``backend.data.binance_1s_stream`` can carry it
forward live.

**The source carries only open/close**, so high/low are synthesised as
``max(open, close)`` / ``min(open, close)`` — a true bound on the real 1s range,
never wider than it — and volume is written as 0. Rows appended later by
``binance_1s_stream`` carry Binance's real high/low/volume.

Tell the two apart by TIME, not by ``volume=0``: the days this loader wrote are
exactly the ``(BTCUSDT, 1s)`` partitions in ``ingest_log``. A real 1s kline can
legitimately have zero volume — ~7% of live seconds see no trade at all — so
volume is not a provenance marker.

Existing readers are unaffected: every query in the codebase filters on
``interval`` as well as ``symbol``, and the 1m base interval is untouched.

Loading is idempotent and resumable — one ``ingest_log`` partition per UTC day,
committed per day so the per-minute cron ingesters are never starved of the
write lock.

Usage:
    python3 -m backend.data.ingest_btc_1s                       # load everything new
    python3 -m backend.data.ingest_btc_1s --from 2026-06-01     # a slice
    python3 -m backend.data.ingest_btc_1s --dry-run             # show the plan only
    python3 -m backend.data.ingest_btc_1s --force               # re-load logged days
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import db

SYMBOL = "BTCUSDT"
INTERVAL = "1s"
DAY = 86_400
CHECKPOINT_EVERY = 10        # days between passive WAL checkpoints


def source_path(override: "str | None" = None) -> Path:
    """Resolved path to btc_1s.db.

    Precedence: ``--src`` > ``BTC_1S_DB`` (env or .env) > a ``btc_1s.db``
    sitting next to the configured market.db.
    """
    if override:
        return Path(override).expanduser()
    env = db.env_value("BTC_1S_DB")
    if env:
        return Path(env).expanduser()
    return db.db_path().parent / "btc_1s.db"


# ---- helpers ----------------------------------------------------------------

def _day_start(ts: int) -> int:
    return ts - (ts % DAY)


def _day_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _parse_day(s: str) -> int:
    """'YYYY-MM-DD' -> unix seconds at 00:00:00 UTC."""
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _done_partitions(conn) -> set:
    cur = conn.execute(
        "SELECT partition FROM ingest_log WHERE symbol=? AND interval=?",
        (SYMBOL, INTERVAL),
    )
    return {r["partition"] for r in cur}


# ---- run --------------------------------------------------------------------

def run(*, src: "str | None" = None, db_path=None, start: "str | None" = None,
        end: "str | None" = None, force: bool = False, dry_run: bool = False) -> dict:
    src_file = source_path(src)
    if not src_file.exists():
        raise FileNotFoundError(f"btc_1s source not found: {src_file}")

    conn = db.connect(db_path)
    t0 = time.time()
    try:
        # Attached read-only via the same connection so the copy stays in C:
        # 86,400 rows per statement, one transaction per UTC day.
        conn.execute("ATTACH DATABASE ? AS src", (str(src_file),))
        row = conn.execute("SELECT MIN(ts) lo, MAX(ts) hi, COUNT(*) n FROM src.btc_1s").fetchone()
        if not row or row["n"] == 0:
            print(f"{src_file} holds no rows; nothing to do.", flush=True)
            return {"new_rows": 0, "days": 0, "seconds": 0.0}

        lo = _day_start(int(row["lo"]))
        hi = int(row["hi"])
        if start:
            lo = max(lo, _day_start(_parse_day(start)))
        if end:
            hi = min(hi, _parse_day(end) + DAY - 1)

        done = set() if force else _done_partitions(conn)
        days = [d for d in range(lo, hi + 1, DAY) if force or _day_str(d) not in done]

        eff_db = db_path or db.db_path()
        print(f"Source {src_file}: {row['n']:,} rows "
              f"({_day_str(int(row['lo']))} .. {_day_str(int(row['hi']))} UTC)", flush=True)
        print(f"Target {eff_db}  candles({SYMBOL}, {INTERVAL})", flush=True)
        print(f"  {len(days)} day partition(s) to load"
              f"{f', {len(done)} already logged' if done else ''}.", flush=True)
        if dry_run:
            return {"new_rows": 0, "days": len(days), "seconds": time.time() - t0}

        now_ts = int(time.time())
        grand = 0
        for i, d0 in enumerate(days, 1):
            d1 = min(d0 + DAY, hi + 1)
            before = conn.total_changes
            # max()/min() with two arguments are SQLite's SCALAR forms here, not
            # aggregates: the real 1s high/low are unknown, so bound them by the
            # open/close pair the source does carry.
            conn.execute(
                "INSERT OR IGNORE INTO candles "
                "  (symbol, interval, time, open, high, low, close, volume) "
                "SELECT ?, ?, ts, open, max(open, close), min(open, close), close, 0.0 "
                "  FROM src.btc_1s WHERE ts >= ? AND ts < ?",
                (SYMBOL, INTERVAL, d0, d1),
            )
            written = conn.total_changes - before
            n_src = conn.execute(
                "SELECT COUNT(*) c FROM src.btc_1s WHERE ts >= ? AND ts < ?", (d0, d1)
            ).fetchone()["c"]
            conn.execute(
                "INSERT OR REPLACE INTO ingest_log "
                "  (symbol, interval, partition, rows, sha256, loaded_at) VALUES (?,?,?,?,NULL,?)",
                (SYMBOL, INTERVAL, _day_str(d0), n_src, now_ts),
            )
            conn.commit()
            grand += written
            print(f"  [{i:>3}/{len(days)}] {_day_str(d0)}  +{written:>6,} rows "
                  f"(src {n_src:>6,}, total {grand:,})", flush=True)
            # Keep the WAL from ballooning while the per-minute cron writers hold
            # readers open; PASSIVE never blocks them.
            if i % CHECKPOINT_EVERY == 0:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    finally:
        try:
            conn.execute("DETACH DATABASE src")
        except Exception:  # noqa: BLE001 - never attached / already gone
            pass
        conn.close()

    dt = time.time() - t0
    cov = coverage(db_path=db_path)
    span = "—"
    if cov["n"]:
        span = (f"{datetime.fromtimestamp(cov['lo'], timezone.utc):%Y-%m-%d %H:%M:%S} .. "
                f"{datetime.fromtimestamp(cov['hi'], timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print(f"\nDone in {dt:.1f}s. New rows: {grand:,}.\n"
          f"  DB now holds {cov['n']:,} {SYMBOL} {INTERVAL} candles ({span}).", flush=True)
    return {"new_rows": grand, "days": len(days), "seconds": dt}


def coverage(*, db_path=None) -> dict:
    """(min_time, max_time, count) for the 1s series in market.db."""
    conn = db.connect(db_path, readonly=True)
    try:
        r = conn.execute(
            "SELECT MIN(time) lo, MAX(time) hi, COUNT(*) n "
            "FROM candles WHERE symbol=? AND interval=?",
            (SYMBOL, INTERVAL),
        ).fetchone()
    finally:
        conn.close()
    return {"lo": r["lo"], "hi": r["hi"], "n": r["n"]}


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="Seed market.db 1s candles from btc_1s.db.")
    ap.add_argument("--src", default=None, help="btc_1s.db path (else BTC_1S_DB / sibling of market.db)")
    ap.add_argument("--db", default=None, help="override market.db path (else MARKET_DB)")
    ap.add_argument("--from", dest="start", default=None, help="YYYY-MM-DD (UTC), inclusive")
    ap.add_argument("--to", dest="end", default=None, help="YYYY-MM-DD (UTC), inclusive")
    ap.add_argument("--force", action="store_true", help="re-load days already in ingest_log")
    ap.add_argument("--dry-run", action="store_true", help="report the plan without writing")
    args = ap.parse_args(argv)
    try:
        run(src=args.src, db_path=args.db, start=args.start, end=args.end,
            force=args.force, dry_run=args.dry_run)
    except FileNotFoundError as e:
        print(f"{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
