"""Ingest pmqb's 15-minute BTC UP/DOWN capture (``pm15m_l2.jsonl``) into SQLite.

pmqb runs a standalone recorder for the 15m market next to its trading platform
(``research/capture/capturePM15m.ts``, pm2 app ``pmqb-pm15m-capture``). It
appends two record types to one file:

  * ``pm15m_l2``       — a YES/NO top-20 book snapshot, every ~2s, since 2026-07-03.
  * ``pm15m_outcome``  — one per window once Polymarket has resolved it, with the
                         strike (``priceToBeat``) and settle (``finalPrice``) read
                         back from Gamma. Recorded from 2026-09-13 onwards.

This folds them into:

  * ``pm_window_15m`` — one row per 15m market: strike, settle, outcome.
  * ``pm_quote_15m``  — per (window, second): YES mid/bid/ask, size at the best,
                        depth over the captured levels, and the Binance BTC mid.

A **resumable tail**, like ``ingest_stream``: a byte cursor for the file is kept
in ``stream_cursor``, the first run backfills the whole file (~3.7 GB, ~1.6M
lines), and each later run (schedule it per minute) reads only what was
appended. Only complete lines are consumed, so a line the capture is still
writing is picked up on the next run.

Windows captured before the outcome records existed have no outcome in the
file. ``--backfill-gamma`` asks Gamma for every window that is closed but still
unresolved and records Polymarket's own answer — run it once after the first
ingest, and again after any capture outage.

Usage:
    python3 -m backend.data.ingest_pm15m                    # backfill / append
    python3 -m backend.data.ingest_pm15m --backfill-gamma   # + resolve missing outcomes
    python3 -m backend.data.ingest_pm15m --reset            # rescan from offset 0
    python3 -m backend.data.ingest_pm15m --status
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import orjson
import requests

from .. import db

WINDOW_SEC = 900
DEFAULT_FILE = "/work/david/PolyMarket/01_EarlyEntry/pmqb/data/pm15m_l2.jsonl"
GAMMA = "https://gamma-api.polymarket.com"
QUOTE_FLUSH = 100_000
READ_CHUNK = 64 << 20          # bytes per read; lines are ~2.3 KB
RESOLVE_AFTER_SEC = 600        # only ask Gamma about windows closed at least this long


def capture_path() -> str:
    return os.environ.get("PM15M_FILE", DEFAULT_FILE)


def _load_cursor(conn, source: str) -> int:
    row = conn.execute("SELECT offset FROM stream_cursor WHERE source=?", (source,)).fetchone()
    return int(row["offset"]) if row else 0


def _save_cursor(conn, source: str, offset: int) -> None:
    conn.execute(
        "INSERT INTO stream_cursor (source, offset, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(source) DO UPDATE SET offset=excluded.offset, updated_at=excluded.updated_at",
        (source, offset, int(time.time())))


def _side(book, i: int):
    """(best price, size at best, total size) for one side of a captured book."""
    levels = (book or {}).get("bids" if i == 0 else "asks") or []
    if not levels:
        return None, None, None
    return levels[0][0], levels[0][1], sum(lv[1] for lv in levels)


_Q_SQL = ("INSERT OR REPLACE INTO pm_quote_15m (start_ts, time, yes, yes_bid, yes_ask, "
          "bid_sz, ask_sz, bid_depth, ask_depth, btc) VALUES (?,?,?,?,?,?,?,?,?,?)")
_W_SQL = ("INSERT INTO pm_window_15m (start_ts, market_id, slug, end_ts) VALUES (?,?,?,?) "
          "ON CONFLICT(start_ts) DO UPDATE SET "
          "market_id=COALESCE(pm_window_15m.market_id, excluded.market_id), "
          "slug=COALESCE(pm_window_15m.slug, excluded.slug)")
_O_SQL = ("INSERT INTO pm_window_15m (start_ts, market_id, slug, end_ts, start_price, end_price, "
          "resolved_up, resolved_src) VALUES (?,?,?,?,?,?,?,'gamma') "
          "ON CONFLICT(start_ts) DO UPDATE SET "
          "market_id=COALESCE(excluded.market_id, pm_window_15m.market_id), "
          "slug=COALESCE(excluded.slug, pm_window_15m.slug), "
          "start_price=COALESCE(excluded.start_price, pm_window_15m.start_price), "
          "end_price=COALESCE(excluded.end_price, pm_window_15m.end_price), "
          "resolved_up=excluded.resolved_up, resolved_src='gamma'")


def run(*, source: "str | None" = None, reset: bool = False, db_path=None) -> dict:
    src = os.path.abspath(source or capture_path())
    size = os.path.getsize(src)
    conn = db.connect(db_path)
    try:
        start = 0 if reset else _load_cursor(conn, src)
        if start > size:                      # file was truncated/rotated: start over
            start = 0
        n_snap = n_out = n_bad = 0
        quotes: list = []
        windows: dict = {}
        t0 = time.time()

        def flush():
            if quotes:
                conn.executemany(_Q_SQL, quotes)
                quotes.clear()
            if windows:
                conn.executemany(_W_SQL, [(st, mid, slug, st + WINDOW_SEC)
                                          for st, (mid, slug) in windows.items()])
                windows.clear()

        pos = start
        with open(src, "rb") as f:
            f.seek(start)
            carry = b""
            while True:
                chunk = f.read(READ_CHUNK)
                if not chunk:
                    break
                buf = carry + chunk
                cut = buf.rfind(b"\n")
                if cut < 0:                   # no complete line yet
                    carry = buf
                    continue
                carry = buf[cut + 1:]
                for line in buf[:cut].split(b"\n"):
                    if not line:
                        continue
                    try:
                        r = orjson.loads(line)
                    except orjson.JSONDecodeError:
                        n_bad += 1
                        continue
                    typ = r.get("type")
                    st = r.get("startTs")
                    if st is None:
                        continue
                    if typ == "pm15m_l2":
                        n_snap += 1
                        yes = r.get("yes")
                        bid, bid_sz, bid_depth = _side(yes, 0)
                        ask, ask_sz, ask_depth = _side(yes, 1)
                        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
                        quotes.append((st, int(r["ts"]) // 1000, mid, bid, ask, bid_sz, ask_sz,
                                       bid_depth, ask_depth, r.get("btc")))
                        if st not in windows:
                            windows[st] = (r.get("marketId"), r.get("slug"))
                    elif typ == "pm15m_outcome":
                        n_out += 1
                        flush()               # the window row must exist before its outcome
                        conn.execute(_O_SQL, (st, r.get("marketId") or None, r.get("slug"),
                                              st + WINDOW_SEC, r.get("priceToBeat"),
                                              r.get("finalPrice"), 1 if r.get("up") else 0))
                    if len(quotes) >= QUOTE_FLUSH:
                        flush()
                        conn.commit()
                # Everything up to the last newline is consumed; the carry is re-read next run.
                consumed = f.tell() - len(carry)
                flush()
                _save_cursor(conn, src, consumed)
                conn.commit()
                pos = consumed
            flush()
            _save_cursor(conn, src, pos)
            conn.commit()
        return {"from": start, "to": pos, "bytes": pos - start, "snapshots": n_snap,
                "outcomes": n_out, "bad_lines": n_bad, "secs": time.time() - t0}
    finally:
        conn.close()


# ---- outcome backfill from Gamma --------------------------------------------

def _gamma_outcome(slug: str, session: requests.Session) -> "dict | None":
    """Polymarket's own resolution for one closed market, or None if not settled."""
    for attempt in range(3):
        try:
            r = session.get(f"{GAMMA}/events", params={"slug": slug}, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 429:
                time.sleep(2 + 2 * attempt)
                continue
            if not r.ok:
                return None
            ev = r.json()
        except (requests.RequestException, ValueError):
            time.sleep(1 + attempt)
            continue
        e = ev[0] if isinstance(ev, list) and ev else None
        m = (e.get("markets") or [None])[0] if e else None
        if not m or not m.get("closed"):
            return None
        op = m.get("outcomePrices")
        op = orjson.loads(op) if isinstance(op, str) else op
        if not isinstance(op, list) or len(op) != 2:
            return None
        if float(op[0]) == 1:
            up = 1
        elif float(op[1]) == 1:
            up = 0
        else:
            return None
        meta = e.get("eventMetadata") or {}
        return {"market_id": str(m.get("id")) if m.get("id") is not None else None, "up": up,
                "start_price": meta.get("priceToBeat"), "end_price": meta.get("finalPrice")}
    return None


def backfill_gamma(db_path=None, *, workers: int = 4, limit: "int | None" = None) -> dict:
    conn = db.connect(db_path)
    try:
        cutoff = int(time.time()) - RESOLVE_AFTER_SEC
        rows = conn.execute(
            "SELECT start_ts, slug FROM pm_window_15m "
            "WHERE (resolved_up IS NULL OR end_price IS NULL) AND end_ts <= ? ORDER BY start_ts",
            (cutoff,)).fetchall()
        todo = [(r["start_ts"], r["slug"] or f"btc-updown-15m-{r['start_ts']}") for r in rows]
        if limit:
            todo = todo[:limit]
        if not todo:
            return {"asked": 0, "resolved": 0, "unresolved": 0}
        print(f"asking Gamma about {len(todo):,} window(s) with {workers} worker(s)", flush=True)
        session = requests.Session()
        done = 0
        got = 0
        t0 = time.time()
        # Answers are buffered and written in short bursts, never one execute per answer:
        # an execute opens a write transaction, and holding it open across the network wait
        # for the next answer locks market.db against the per-minute ingest crons.
        buf: list = []

        def write():
            if buf:
                conn.executemany(_O_SQL, buf)
                conn.commit()
                buf.clear()

        with ThreadPoolExecutor(workers) as ex:
            for (st, slug), res in zip(todo, ex.map(lambda w: _gamma_outcome(w[1], session), todo)):
                done += 1
                if res is not None:
                    got += 1
                    buf.append((st, res["market_id"], slug, st + WINDOW_SEC,
                                res["start_price"], res["end_price"], res["up"]))
                if len(buf) >= 50:
                    write()
                if done % 500 == 0 or done == len(todo):
                    write()
                    print(f"  {done:,}/{len(todo):,}  resolved {got:,}  "
                          f"({time.time() - t0:.0f}s)", flush=True)
        write()
        return {"asked": len(todo), "resolved": got, "unresolved": len(todo) - got}
    finally:
        conn.close()


def status(db_path=None) -> None:
    conn = db.connect(db_path)
    try:
        w = conn.execute(
            "SELECT COUNT(*) n, SUM(resolved_up IS NOT NULL) r, SUM(start_price IS NOT NULL) sp, "
            "MIN(start_ts) lo, MAX(start_ts) hi FROM pm_window_15m").fetchone()
        q = conn.execute("SELECT COUNT(*) n FROM pm_quote_15m").fetchone()
        c = conn.execute("SELECT offset FROM stream_cursor WHERE source=?",
                         (os.path.abspath(capture_path()),)).fetchone()
        fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M") if t else "-"
        print(f"db      : {db_path or db.db_path()}")
        print(f"          pm_window_15m {w['n']:,} windows ({fmt(w['lo'])} .. {fmt(w['hi'])} UTC), "
              f"{w['r'] or 0:,} resolved, {w['sp'] or 0:,} with strike")
        print(f"          pm_quote_15m  {q['n']:,} rows")
        size = os.path.getsize(capture_path()) if os.path.exists(capture_path()) else 0
        print(f"capture : {capture_path()} — cursor {c['offset'] if c else 0:,} of {size:,} bytes")
    finally:
        conn.close()


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingest pmqb's 15m BTC UP/DOWN capture into SQLite.")
    ap.add_argument("--file", default=None, help="capture file (default PM15M_FILE or pmqb/data)")
    ap.add_argument("--reset", action="store_true", help="rescan from byte 0")
    ap.add_argument("--backfill-gamma", action="store_true",
                    help="after ingesting, resolve closed windows that have no outcome via Gamma")
    ap.add_argument("--gamma-workers", type=int, default=4)
    ap.add_argument("--gamma-limit", type=int, default=None, help="ask about at most N windows")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--db", default=None, help="override DB path")
    args = ap.parse_args(argv)

    if args.status:
        status(args.db)
        return 0
    r = run(source=args.file, reset=args.reset, db_path=args.db)
    bad = f", {r['bad_lines']:,} unparseable lines" if r["bad_lines"] else ""
    print(f"pm15m: {r['bytes']:,} bytes -> {r['snapshots']:,} snapshots, "
          f"{r['outcomes']:,} outcomes{bad} in {r['secs']:.1f}s", flush=True)
    if args.backfill_gamma:
        g = backfill_gamma(args.db, workers=args.gamma_workers, limit=args.gamma_limit)
        print(f"gamma : asked {g['asked']:,}, resolved {g['resolved']:,}, "
              f"still unresolved {g['unresolved']:,}", flush=True)
    status(args.db)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
