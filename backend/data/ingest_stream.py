"""Ingest the pmqb capture (``stream.jsonl``) into the local SQLite store.

The sibling pmqb bot records, per tick, both the **Chainlink** BTC price and the
**Polymarket** 5-minute UP/DOWN market book. This tool folds that one file into:

  * ``candles`` under symbol ``BTCUSD_CL`` — 1-minute Chainlink OHLC. (Verified
    identical to the Chainlink Data Streams feed used by ``ingest_chainlink.py``,
    so the two sources share the symbol; ``INSERT OR IGNORE`` reconciles overlap.)
  * ``pm_window`` — one row per 5m market (start/end Chainlink price, up/down).
  * ``pm_quote``  — tick-level YES(UP) price (mid + book bid/ask) for realistic
    Polymarket backtest entry pricing.

It is a **resumable tail**: a byte cursor per source file is kept in
``stream_cursor``. First run backfills the whole file; each later run (schedule
per-minute) reads only what was appended. The trailing, still-forming 1-minute
Chainlink candle is held back in ``cl_partial`` so an incomplete minute is never
written as if sealed. Everything is ``INSERT OR IGNORE`` / idempotent.

Usage:
    python3 -m backend.data.ingest_stream                 # backfill / append
    python3 -m backend.data.ingest_stream --reset         # rescan from offset 0
    python3 -m backend.data.ingest_stream --stream PATH    # override source file
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from .. import db

CL_SYMBOL = "BTCUSD_CL"
INTERVAL = "1m"
MINUTE = 60
WINDOW_SEC = 300
DEFAULT_STREAM = "/work/david/PolyMarket/01_EarlyEntry/pmqb/data/stream.jsonl"
QUOTE_FLUSH = 200_000     # pm_quote rows buffered before a DB flush
PROGRESS_GB = 0.5         # print a heartbeat every ~this many GB


def stream_path() -> str:
    return os.environ.get("STREAM_FILE", DEFAULT_STREAM)


# ---- cursor / partial state -------------------------------------------------

def _load_cursor(conn, source: str) -> int:
    row = conn.execute("SELECT offset FROM stream_cursor WHERE source=?", (source,)).fetchone()
    return int(row["offset"]) if row else 0


def _save_cursor(conn, source: str, offset: int) -> None:
    conn.execute(
        "INSERT INTO stream_cursor (source, offset, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(source) DO UPDATE SET offset=excluded.offset, updated_at=excluded.updated_at",
        (source, offset, int(time.time())))


def _load_partial(conn) -> "dict | None":
    row = conn.execute("SELECT * FROM cl_partial WHERE symbol=?", (CL_SYMBOL,)).fetchone()
    if not row:
        return None
    return {"time": row["minute"], "open": row["open"], "high": row["high"],
            "low": row["low"], "close": row["close"], "_last": row["last_ts"]}


def _save_partial(conn, c: dict) -> None:
    conn.execute(
        "INSERT INTO cl_partial (symbol, minute, open, high, low, close, last_ts) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
        "minute=excluded.minute, open=excluded.open, high=excluded.high, "
        "low=excluded.low, close=excluded.close, last_ts=excluded.last_ts",
        (CL_SYMBOL, c["time"], c["open"], c["high"], c["low"], c["close"], c["_last"]))


# ---- OHLC fold --------------------------------------------------------------

def _fold(minutes: dict, ts: int, price: float) -> None:
    m = ts - (ts % MINUTE)
    c = minutes.get(m)
    if c is None:
        minutes[m] = {"time": m, "open": price, "high": price, "low": price,
                      "close": price, "_last": ts}
    else:
        if price > c["high"]:
            c["high"] = price
        if price < c["low"]:
            c["low"] = price
        if ts >= c["_last"]:
            c["close"] = price
            c["_last"] = ts


# ---- run --------------------------------------------------------------------

def run(*, source: str | None = None, reset: bool = False, db_path=None) -> dict:
    source = os.path.abspath(source or stream_path())
    if not os.path.exists(source):
        raise FileNotFoundError(source)

    conn = db.connect(db_path)
    try:
        if reset:
            conn.execute("DELETE FROM stream_cursor WHERE source=?", (source,))
            conn.execute("DELETE FROM cl_partial WHERE symbol=?", (CL_SYMBOL,))
            conn.commit()

        cursor = _load_cursor(conn, source)
        size = os.path.getsize(source)
        if cursor > size:                      # file rotated/truncated -> rescan
            print(f"cursor {cursor} > file size {size}; resetting to 0", flush=True)
            cursor = 0
            conn.execute("DELETE FROM cl_partial WHERE symbol=?", (CL_SYMBOL,))
        if cursor >= size:
            print("nothing new to ingest.", flush=True)
            return {"new_bytes": 0, "quotes": 0, "cl_sealed": 0, "windows": 0}

        minutes: dict = {}
        seed = _load_partial(conn)
        if seed:
            minutes[seed["time"]] = seed

        pm_win: dict = {}          # start_ts -> (market_id, slug, start_price)
        pm_res: dict = {}          # start_ts -> (market_id, end_price, up)
        quote_buf: list = []
        n_snap = n_out = n_quotes = 0

        t0 = time.time()
        next_progress = cursor + PROGRESS_GB * 1e9

        def flush_quotes():
            nonlocal quote_buf, n_quotes
            if quote_buf:
                conn.executemany(
                    "INSERT OR IGNORE INTO pm_quote (start_ts, time, yes, yes_bid, yes_ask) "
                    "VALUES (?,?,?,?,?)", quote_buf)
                n_quotes += len(quote_buf)
                quote_buf = []

        eff_db = db_path or db.db_path()
        print(f"Ingesting {source}\n  from byte {cursor:,} / {size:,} "
              f"({(size-cursor)/1e9:.2f} GB new) -> {eff_db}", flush=True)

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

                is_snap = b'"type":"snapshot"' in raw
                is_out = (not is_snap) and b'"type":"outcome"' in raw and b'"source":"chainlink"' in raw
                if not (is_snap or is_out):
                    continue
                try:
                    r = json.loads(raw)
                except Exception:                        # noqa: BLE001
                    continue

                if is_snap:
                    n_snap += 1
                    st = r.get("startTs")
                    w = (r.get("request") or {}).get("window") or {}
                    ts = r.get("ts")
                    if st is None or ts is None:
                        continue
                    tsec = int(ts) // 1000
                    cp = w.get("currentPrice")
                    if cp is not None:
                        _fold(minutes, tsec, float(cp))
                    if st not in pm_win:
                        sp = w.get("startPrice")
                        slug = r.get("slug") or f"btc-updown-5m-{st}"
                        pm_win[st] = (str(r.get("marketId")) if r.get("marketId") is not None else None,
                                      slug, float(sp) if sp is not None else None)
                    yes = r.get("yes")
                    if yes is not None:
                        quote_buf.append((st, tsec, float(yes),
                                          _f(r.get("yesBid")), _f(r.get("yesAsk"))))
                        if len(quote_buf) >= QUOTE_FLUSH:
                            flush_quotes()
                else:  # outcome (chainlink)
                    n_out += 1
                    st = r.get("startTs")
                    if st is None:
                        continue
                    ep = r.get("endPrice")
                    pm_res[st] = (str(r.get("marketId")) if r.get("marketId") is not None else None,
                                  float(ep) if ep is not None else None,
                                  1 if r.get("up") else 0)

                if start >= next_progress:
                    flush_quotes()
                    conn.commit()
                    pct = 100.0 * start / size
                    print(f"  ...{start/1e9:.2f}/{size/1e9:.2f} GB ({pct:.0f}%)  "
                          f"snap={n_snap:,} out={n_out:,} quotes={n_quotes:,} "
                          f"mins={len(minutes):,}", flush=True)
                    next_progress = start + PROGRESS_GB * 1e9

        flush_quotes()

        # ---- seal complete Chainlink minutes, hold back the last -----------
        sealed = 0
        if minutes:
            max_min = max(minutes)
            seal_rows = [(CL_SYMBOL, INTERVAL, c["time"], c["open"], c["high"],
                          c["low"], c["close"], 0.0)
                         for m, c in sorted(minutes.items()) if m < max_min]
            if seal_rows:
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO candles "
                    "(symbol, interval, time, open, high, low, close, volume) "
                    "VALUES (?,?,?,?,?,?,?,?)", seal_rows)
                sealed = conn.total_changes - before
            _save_partial(conn, minutes[max_min])       # carry the unsealed minute

        # ---- Polymarket windows + resolutions ------------------------------
        if pm_win:
            conn.executemany(
                "INSERT OR IGNORE INTO pm_window "
                "(start_ts, market_id, slug, end_ts, start_price, end_price, resolved_up) "
                "VALUES (?,?,?,?,?,?,NULL)",
                [(st, mid, slug, st + WINDOW_SEC, sp, None) for st, (mid, slug, sp) in pm_win.items()])
        for st, (mid, ep, up) in pm_res.items():
            conn.execute(
                "INSERT INTO pm_window (start_ts, market_id, end_ts, end_price, resolved_up, resolved_src) "
                "VALUES (?,?,?,?,?,'chainlink') ON CONFLICT(start_ts) DO UPDATE SET "
                "end_ts=excluded.end_ts, end_price=excluded.end_price, "
                "resolved_up=excluded.resolved_up, resolved_src='chainlink', "
                "market_id=COALESCE(pm_window.market_id, excluded.market_id)",
                (st, mid, st + WINDOW_SEC, ep, up))

        # Recover resolution for windows without a recorded outcome (the early
        # pre-outcome-logging period) from the NEXT window's Chainlink start
        # price — which IS Polymarket's boundary settlement reference. Never
        # overrides an authoritative 'chainlink' resolution.
        derived = conn.execute(
            "UPDATE pm_window AS w SET "
            "  end_price = (SELECT n.start_price FROM pm_window n WHERE n.start_ts = w.start_ts + 300), "
            "  resolved_up = (SELECT CASE WHEN n.start_price >= w.start_price THEN 1 ELSE 0 END "
            "                 FROM pm_window n WHERE n.start_ts = w.start_ts + 300), "
            "  resolved_src = 'boundary' "
            "WHERE w.resolved_up IS NULL AND w.start_price IS NOT NULL "
            "  AND EXISTS (SELECT 1 FROM pm_window n "
            "              WHERE n.start_ts = w.start_ts + 300 AND n.start_price IS NOT NULL)"
        ).rowcount

        _save_cursor(conn, source, cursor)
        conn.commit()
        if derived:
            print(f"  resolved {derived:,} extra window(s) from the boundary price.", flush=True)

        dt = time.time() - t0
        lo = min(minutes) if minutes else None
        hi = max(minutes) if minutes else None
        span = (f"{datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d %H:%M} .. "
                f"{datetime.fromtimestamp(hi, timezone.utc):%Y-%m-%d %H:%M}"
                if minutes else "—")
        print(f"\nDone in {dt:.1f}s. snapshots={n_snap:,} outcomes={n_out:,}\n"
              f"  BTCUSD_CL: {sealed:,} new sealed minutes ({span} UTC)\n"
              f"  pm_quote:  {n_quotes:,} rows offered\n"
              f"  pm_window: {len(pm_win):,} seen, {len(pm_res):,} resolutions\n"
              f"  cursor -> {cursor:,} bytes", flush=True)
        return {"new_bytes": cursor, "quotes": n_quotes, "cl_sealed": sealed,
                "windows": len(pm_win), "resolutions": len(pm_res)}
    finally:
        conn.close()


def _f(v):
    return float(v) if v is not None else None


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="Ingest pmqb stream.jsonl (Chainlink + Polymarket).")
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
