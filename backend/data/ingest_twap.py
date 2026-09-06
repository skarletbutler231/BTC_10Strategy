"""Ingest the Chainlink BTC/USD 30s + 60s TWAPs from the pmqb capture into ``cl_twap``.

The sibling pmqb bot writes ``twap30`` / ``twap60`` on every snapshot line of
``stream.jsonl`` (Chainlink Data Streams when the paid SDK is up, the Polymarket
RTDS relay otherwise -- the same numbers either way). This tool folds them into
one row per captured SECOND:

  * ``cl_twap`` -- time (unix sec, UTC), twap30, twap60, obs_ts, obs_win.

Deliberately a SEPARATE job from ``ingest_stream``, not another branch inside it:
that ingester's byte cursor is already parked at the end of an 11 GB file, so
teaching it about TWAP would have needed a full ``--reset`` -- rewriting every
pm_quote row -- just to pick up history. This module keeps its own cursor under
the ``twap:`` prefix in ``stream_cursor``, so it can backfill from byte 0 while
the stream ingester carries on untouched.

Like ingest_stream it is a **resumable tail**: first run scans the whole file,
each later run (schedule per-minute) reads only what was appended. Everything is
``INSERT OR IGNORE`` and idempotent, so a re-scan costs time and nothing else.
The TWAP fields only start appearing around 2026-08-04 05:30 UTC -- earlier lines
have no such key and are skipped by a cheap substring test before any JSON parse.

Usage:
    python3 -m backend.data.ingest_twap                  # backfill / append
    python3 -m backend.data.ingest_twap --reset          # rescan from offset 0
    python3 -m backend.data.ingest_twap --stream PATH    # override source file
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from .. import db

DEFAULT_STREAM = "/work/david/PolyMarket/01_EarlyEntry/pmqb/data/stream.jsonl"
CURSOR_PREFIX = "twap:"   # keeps this job's cursor distinct from ingest_stream's
ROW_FLUSH = 200_000       # cl_twap rows buffered before a DB flush
PROGRESS_GB = 1.0         # print a heartbeat every ~this many GB
HAS_TWAP = b'"twap30"'    # cheap prefilter: pre-2026-08-04 lines lack the key entirely


def stream_path() -> str:
    return os.environ.get("STREAM_FILE", DEFAULT_STREAM)


def _load_cursor(conn, key: str) -> int:
    row = conn.execute("SELECT offset FROM stream_cursor WHERE source=?", (key,)).fetchone()
    return int(row["offset"]) if row else 0


def _save_cursor(conn, key: str, offset: int) -> None:
    conn.execute(
        "INSERT INTO stream_cursor (source, offset, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(source) DO UPDATE SET offset=excluded.offset, updated_at=excluded.updated_at",
        (key, offset, int(time.time())))


def _f(v):
    return float(v) if v is not None else None


def run(*, source: str | None = None, reset: bool = False, db_path=None) -> dict:
    source = os.path.abspath(source or stream_path())
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    key = CURSOR_PREFIX + source

    conn = db.connect(db_path)
    try:
        if reset:
            conn.execute("DELETE FROM stream_cursor WHERE source=?", (key,))
            conn.commit()

        cursor = _load_cursor(conn, key)
        size = os.path.getsize(source)
        if cursor > size:                      # file rotated/truncated -> rescan
            print(f"cursor {cursor} > file size {size}; resetting to 0", flush=True)
            cursor = 0
        if cursor >= size:
            print("nothing new to ingest.", flush=True)
            return {"new_bytes": 0, "rows": 0}

        eff_db = db_path or db.db_path()
        print(f"Ingesting TWAP from {source}\n  from byte {cursor:,} / {size:,} "
              f"({(size-cursor)/1e9:.2f} GB new) -> {eff_db}", flush=True)

        buf: list = []
        n_rows = n_seen = 0
        seen_secs: set = set()   # first tick of each second wins; skips ~99% of the DB round trips
        lo_sec = hi_sec = None
        t0 = time.time()
        next_progress = cursor + PROGRESS_GB * 1e9

        def flush():
            nonlocal buf, n_rows
            if buf:
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO cl_twap (time, twap30, twap60, obs_ts, obs_win) "
                    "VALUES (?,?,?,?,?)", buf)
                n_rows += conn.total_changes - before
                buf = []

        with open(source, "rb") as f:
            f.seek(cursor)
            while True:
                start = f.tell()
                raw = f.readline()
                if not raw:
                    cursor = start                       # clean EOF
                    break
                if not raw.endswith(b"\n"):
                    cursor = start                       # partial last line: stop before it
                    break

                if HAS_TWAP not in raw:
                    if start >= next_progress:
                        pct = 100.0 * start / size
                        print(f"  ...{start/1e9:.2f}/{size/1e9:.2f} GB ({pct:.0f}%)  "
                              f"rows={n_rows:,}", flush=True)
                        next_progress = start + PROGRESS_GB * 1e9
                    continue
                try:
                    r = json.loads(raw)
                except Exception:                        # noqa: BLE001
                    continue

                ts = r.get("ts")
                t30, t60 = r.get("twap30"), r.get("twap60")
                if ts is None or (t30 is None and t60 is None):
                    continue
                tsec = int(ts) // 1000
                if tsec in seen_secs:
                    continue
                seen_secs.add(tsec)
                n_seen += 1
                lo_sec = tsec if lo_sec is None else min(lo_sec, tsec)
                hi_sec = tsec if hi_sec is None else max(hi_sec, tsec)

                # obs_ts is whichever window the engine believed the market resolved on when the
                # line was written -- 30s before the 2026-08-14 00:00 UTC cutover, 60s after --
                # so the window is dated rather than assumed. See loop.ts (`RESOLUTION_TWAP_SEC`).
                obs_ts = r.get("twapTs")
                obs_win = None
                if obs_ts is not None:
                    obs_ts = int(obs_ts) // 1000
                    obs_win = 30 if tsec < 1786665600 else 60   # 2026-08-14 00:00:00 UTC
                buf.append((tsec, _f(t30), _f(t60), obs_ts, obs_win))
                if len(buf) >= ROW_FLUSH:
                    flush()
                    conn.commit()

                if start >= next_progress:
                    flush()
                    conn.commit()
                    pct = 100.0 * start / size
                    print(f"  ...{start/1e9:.2f}/{size/1e9:.2f} GB ({pct:.0f}%)  "
                          f"rows={n_rows:,}", flush=True)
                    next_progress = start + PROGRESS_GB * 1e9

        flush()
        _save_cursor(conn, key, cursor)
        conn.commit()

        span = (f"{datetime.fromtimestamp(lo_sec, timezone.utc):%Y-%m-%d %H:%M} .. "
                f"{datetime.fromtimestamp(hi_sec, timezone.utc):%Y-%m-%d %H:%M}"
                if lo_sec is not None else "—")
        print(f"\nDone in {time.time()-t0:.1f}s. twap seconds seen={n_seen:,}\n"
              f"  cl_twap: {n_rows:,} new rows ({span} UTC)\n"
              f"  cursor -> {cursor:,} bytes", flush=True)
        return {"new_bytes": cursor, "rows": n_rows, "seen": n_seen}
    finally:
        conn.close()


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="Ingest Chainlink 30s/60s TWAPs from pmqb stream.jsonl.")
    ap.add_argument("--stream", default=None, help=f"source file (default {DEFAULT_STREAM})")
    ap.add_argument("--reset", action="store_true", help="rescan from offset 0")
    ap.add_argument("--db", default=None, help="override DB path")
    args = ap.parse_args(argv)
    try:
        run(source=args.stream, reset=args.reset, db_path=args.db)
    except FileNotFoundError as e:
        print(f"stream file not found: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
