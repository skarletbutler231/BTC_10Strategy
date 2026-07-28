"""Backfill Polymarket price + L2 order book history from PMData into SQLite.

Downloads the PMData daily archives (see ``backend.data.pmdata``) and folds each
market's L2 event stream onto a **1-second grid**:

  * ``pm_l2_quote``  — best bid/ask/mid, size at the best, and cumulative depth
                       within 1c/5c/10c of the best, per (window, second).
  * ``pm_l2_book``   — the full 0.001-resolution ladder for that second, as a
                       zstd-compressed 2000-slot uint32 array (~330 bytes).
  * ``pm_l2_market`` — per-window metadata and the feed's own resolution.
  * ``pmdata_day``   — which archives have been folded in, so re-runs skip them.

Why a 1-second grid and not the raw events: BTC 5m alone is ~30M L2 events a
day, ~5 billion over the full history. The archives keep every event; SQLite
keeps the per-second state that backtests actually query, and can be rebuilt at
any other resolution from the archives without spending PMData quota again.

Book reconstruction: a ``book`` event is a full snapshot, ``price_change`` sets
or clears one level. The feed also reports its own best bid/ask on every
price_change — those are used verbatim for the top-of-book columns, so quoted
prices never depend on replay being perfect. Full snapshots arrive ~3.4x/second,
so the replayed depth resyncs continuously.

Usage:
    python3 -m backend.data.ingest_pmdata                   # full history, download + fold
    python3 -m backend.data.ingest_pmdata --from 2026-07-01 --to 2026-07-27
    python3 -m backend.data.ingest_pmdata --download-only   # just fill the archive
    python3 -m backend.data.ingest_pmdata --ingest-only     # fold what is already local
    python3 -m backend.data.ingest_pmdata --no-ladder       # skip pm_l2_book (~8 GB)
    python3 -m backend.data.ingest_pmdata --status          # coverage report, no work
"""

from __future__ import annotations

import argparse
import io
import multiprocessing as mp
import os
import sys
import time
import zipfile
from datetime import date, datetime, timezone

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests

from .. import db
from ..pm_store import SIZE_SCALE, SLOTS, TICK, decode_ladder, encode_ladder  # noqa: F401
from . import pmdata

SERIES = "btc-5m"
DATA_TYPE = "poly_l2"
WINDOW_SEC = 300


# ---- per-market fold --------------------------------------------------------

def _col_f64(t: pa.Table, name: str) -> np.ndarray:
    """Column as float64 with NULL -> NaN."""
    return t.column(name).cast(pa.float64()).to_numpy(zero_copy_only=False)


def _eq(col, value) -> np.ndarray:
    """``col == value`` as a plain bool array, with NULL treated as False.

    ``pc.equal`` propagates nulls, which would come back as an object array; the
    fill_null keeps it a real numpy bool mask.
    """
    return pc.fill_null(pc.equal(col, value), False).to_numpy(zero_copy_only=False).astype(bool)


def fold_market(raw: bytes, *, want_ladder: bool = True) -> "tuple[dict, list, list] | None":
    """Fold one market's Parquet into (meta, quote rows, book rows).

    Vectorised on purpose: the straightforward per-event Python loop runs at
    ~6.7us/event, which is ~12 CPU-hours over the full history. Here the ladder
    is a numpy array and each second is applied as one fancy-indexed assignment
    (numpy keeps the last value for repeated indices, which is exactly the
    last-write-wins the feed's semantics call for).
    """
    t = pq.read_table(io.BytesIO(raw))
    n = t.num_rows
    if n == 0:
        return None

    slug = t.column("market_slug")[0].as_py()
    try:
        start_ts = int(slug.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None

    # A few events carry a null exchange `timestamp`; fall back to PMData's own
    # receive time rather than dropping them, and drop only if both are missing
    # (nothing can place such an event on the time grid).
    ts_arr = pc.coalesce(t.column("timestamp").cast(pa.int64()),
                         t.column("local_timestamp").cast(pa.int64()))
    if ts_arr.null_count:
        keep = pc.is_valid(ts_arr)
        t = t.filter(keep)
        ts_arr = ts_arr.filter(keep)
        n = t.num_rows
        if n == 0:
            return None
    ts_us = ts_arr.to_numpy(zero_copy_only=False).astype(np.int64)

    order = np.argsort(ts_us, kind="stable")        # archives are already ordered; cheap insurance
    if not np.array_equal(order, np.arange(n)):
        t = t.take(pa.array(order))
        ts_us = ts_us[order]
    sec = ts_us // 1_000_000

    etype = t.column("event_type")
    is_book = _eq(etype, "book")
    # A change is only usable if it names a price; anything else is ignored.
    is_chg = _eq(etype, "price_change")
    pc_price = _col_f64(t, "pc_price")
    pc_size = _col_f64(t, "pc_size")
    is_chg &= ~np.isnan(pc_price)
    is_buy = _eq(t.column("pc_side"), "BUY")
    best_bid = _col_f64(t, "best_bid")
    best_ask = _col_f64(t, "best_ask")

    # Slot index per change event: side offset + price on the 0.001 grid.
    slot = np.where(np.isnan(pc_price), 0, np.nan_to_num(pc_price) * TICK)
    slot = np.rint(slot).astype(np.int32)
    np.clip(slot, 0, TICK - 1, out=slot)
    slot = slot + np.where(is_buy, 0, TICK).astype(np.int32)
    val = np.rint(np.nan_to_num(pc_size) * SIZE_SCALE).astype(np.uint32)

    # Only `book` rows carry the ladder arrays; materialise just those.
    book_pos = np.flatnonzero(is_book)
    if book_pos.size:
        take = pa.array(book_pos)
        bp = t.column("bid_prices").take(take).to_pylist()
        bs = t.column("bid_sizes").take(take).to_pylist()
        ap = t.column("ask_prices").take(take).to_pylist()
        asz = t.column("ask_sizes").take(take).to_pylist()
    else:
        bp = bs = ap = asz = []
    book_rank = {int(p): i for i, p in enumerate(book_pos)}

    outcome = None
    res_pos = _eq(etype, "market_resolved")
    if res_pos.any():
        wo = t.column("winning_outcome").take(pa.array(np.flatnonzero(res_pos))).to_pylist()
        outcome = next((v for v in reversed(wo) if v), None)

    ladder = np.zeros(SLOTS, dtype=np.uint32)
    bb = ba = float("nan")

    # Second boundaries: np.unique on a sorted array gives the first index of each run.
    uniq, first = np.unique(sec, return_index=True)
    bounds = np.append(first, n)

    quotes: list = []
    books: list = []

    for i in range(uniq.size):
        lo, hi = int(bounds[i]), int(bounds[i + 1])

        # A full snapshot supersedes everything before it in this second.
        b0, b1 = np.searchsorted(book_pos, (lo, hi))
        apply_from = lo
        if b1 > b0:
            last = int(book_pos[b1 - 1])
            k = book_rank[last]
            ladder[:] = 0
            for prices, sizes, off in ((bp[k], bs[k], 0), (ap[k], asz[k], TICK)):
                if prices:
                    idx = np.rint(np.asarray(prices, dtype=np.float64) * TICK).astype(np.int32)
                    np.clip(idx, 0, TICK - 1, out=idx)
                    ladder[idx + off] = np.rint(
                        np.asarray(sizes, dtype=np.float64) * SIZE_SCALE).astype(np.uint32)
            # A snapshot lists every resting level, so an empty side means the
            # side really is empty — carrying the previous best forward would
            # quote a price with nothing behind it (~1% of snapshots).
            bb = float(bp[k][0]) if bp[k] else float("nan")
            ba = float(ap[k][0]) if ap[k] else float("nan")
            apply_from = last + 1

        m = is_chg[apply_from:hi]
        if m.any():
            sl = slot[apply_from:hi][m]
            ladder[sl] = val[apply_from:hi][m]      # duplicate indices -> last wins
            # The feed's own best after the final change in this second is
            # authoritative; prefer it over anything the replay implies.
            j = apply_from + int(np.flatnonzero(m)[-1])
            if not np.isnan(best_bid[j]):
                bb = float(best_bid[j])
            if not np.isnan(best_ask[j]):
                ba = float(best_ask[j])

        tsec = int(uniq[i])
        # Slot 0 (price 0.000) is how the feed says "this side is empty" — it
        # reports best_bid=0 with no resting size where the ask side just goes
        # null. Both land as NULL here so a reader never sees a 0.0 quote and
        # mistakes it for a tradeable price.
        bslot = int(round(bb * TICK)) if bb == bb else -1
        aslot = int(round(ba * TICK)) if ba == ba else -1

        # Prices are stored snapped to the 0.001 grid rather than as the feed's
        # raw float. Polymarket only quotes grid prices, but ~8% of feed values
        # arrive with float noise (0.501 as 0.5009998095600838), which would
        # break exact comparisons and disagree with the ladder's own slotting.
        # The correction is ~2e-7, well below a tick.
        if 0 < bslot < TICK:
            bid_sz = ladder[bslot] / SIZE_SCALE
            b1 = ladder[max(0, bslot - 10):bslot + 1].sum() / SIZE_SCALE
            b5 = ladder[max(0, bslot - 50):bslot + 1].sum() / SIZE_SCALE
            b10 = ladder[max(0, bslot - 100):bslot + 1].sum() / SIZE_SCALE
            bidp = bslot / TICK
        else:
            bidp = bid_sz = b1 = b5 = b10 = None

        if 0 < aslot < TICK:
            o = TICK + aslot
            ask_sz = ladder[o] / SIZE_SCALE
            a1 = ladder[o:TICK + min(TICK - 1, aslot + 10) + 1].sum() / SIZE_SCALE
            a5 = ladder[o:TICK + min(TICK - 1, aslot + 50) + 1].sum() / SIZE_SCALE
            a10 = ladder[o:TICK + min(TICK - 1, aslot + 100) + 1].sum() / SIZE_SCALE
            askp = aslot / TICK
        else:
            askp = ask_sz = a1 = a5 = a10 = None

        mid = (bidp + askp) / 2 if bidp is not None and askp is not None else None
        quotes.append((start_ts, tsec, bidp, askp, mid, bid_sz, ask_sz,
                       b1, a1, b5, a5, b10, a10, hi - lo))
        if want_ladder:
            books.append((start_ts, tsec, encode_ladder(ladder)))

    meta = {
        "start_ts": start_ts, "slug": slug,
        "first_ts": int(sec[0]), "last_ts": int(sec[-1]),
        "n_events": n, "n_book": int(is_book.sum()), "n_change": int(is_chg.sum()),
        "outcome": outcome,
        "resolved_up": 1 if outcome == "yes" else (0 if outcome == "no" else None),
    }
    return meta, quotes, books


def fold_day(zip_path: str, day_str: str, want_ladder: bool = True) -> dict:
    """Fold every market in one day archive. Runs in a worker process."""
    metas: list = []
    quotes: list = []
    books: list = []
    failed: list = []
    events = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in sorted(z.namelist()):
            if not name.endswith(".parquet"):
                continue
            # One unreadable market must not abandon a 165-day backfill. The
            # failure is carried back and reported rather than swallowed, so a
            # gap is always visible instead of silently looking like coverage.
            try:
                got = fold_market(z.read(name), want_ladder=want_ladder)
            except Exception as e:  # noqa: BLE001
                failed.append(f"{name}: {type(e).__name__}: {e}")
                continue
            if got is None:
                continue
            meta, q, b = got
            meta["data_date"] = day_str
            metas.append(meta)
            quotes.extend(q)
            books.extend(b)
            events += meta["n_events"]
    return {"day": day_str, "metas": metas, "quotes": quotes, "books": books,
            "events": events, "failed": failed, "zip_bytes": os.path.getsize(zip_path)}


# ---- writer -----------------------------------------------------------------

_Q_SQL = ("INSERT OR REPLACE INTO pm_l2_quote (start_ts, time, bid, ask, mid, "
          "bid_sz, ask_sz, bid_d1, ask_d1, bid_d5, ask_d5, bid_d10, ask_d10, n_events) "
          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
_B_SQL = "INSERT OR REPLACE INTO pm_l2_book (start_ts, time, ladder) VALUES (?,?,?)"
_M_SQL = ("INSERT OR REPLACE INTO pm_l2_market (start_ts, slug, first_ts, last_ts, "
          "n_events, n_book, n_change, outcome, resolved_up, resolved_src, data_date) "
          "VALUES (?,?,?,?,?,?,?,?,?,?,?)")

# A 5m market's YES price converges to ~1.0 (UP) or ~0.0 (DOWN) as it settles,
# so the last two-sided quote implies the outcome. Measured against the feed's
# own market_resolved events: this decides 97.9% of markets at 99.82% accuracy.
# Only used where the feed gave no outcome at all — PMData did not record
# market_resolved before ~2026-03-28 — and always tagged 'terminal', never
# passed off as the authoritative 'feed' value. Same spirit as pm_window's
# 'boundary' provenance.
SETTLE_HI = 0.9
SETTLE_LO = 0.1


WRITE_CHUNK = 25_000     # rows per transaction


def _write_chunked(conn, sql: str, rows: list) -> None:
    """Insert in bounded transactions.

    market.db has a per-minute cron writer (``run_updaters.sh stream``). SQLite
    allows one writer at a time, so committing a whole day in one transaction
    would hold the lock long enough to push that job past its busy timeout.
    Short bursts let the two interleave.
    """
    for i in range(0, len(rows), WRITE_CHUNK):
        conn.executemany(sql, rows[i:i + WRITE_CHUNK])
        conn.commit()


def write_day(conn, res: dict) -> None:
    _write_chunked(conn, _M_SQL, [
        (m["start_ts"], m["slug"], m["first_ts"], m["last_ts"], m["n_events"],
         m["n_book"], m["n_change"], m["outcome"], m["resolved_up"],
         "feed" if m["resolved_up"] is not None else None, m["data_date"])
        for m in res["metas"]])
    _write_chunked(conn, _Q_SQL, res["quotes"])
    _write_chunked(conn, _B_SQL, res["books"])
    # Written last: it is the marker that says this day is complete, and every
    # insert above is INSERT OR REPLACE, so an interrupted day just re-folds.
    conn.execute(
        "INSERT OR REPLACE INTO pmdata_day (series, data_type, data_date, markets, "
        "events, sec_rows, zip_bytes, loaded_at) VALUES (?,?,?,?,?,?,?,?)",
        (SERIES, DATA_TYPE, res["day"], len(res["metas"]), res["events"],
         len(res["quotes"]), res["zip_bytes"], int(time.time())))
    conn.commit()


def derive_outcomes(conn) -> dict:
    """Fill in outcomes the feed never reported, from the settled book.

    Idempotent, and never touches a window the feed resolved: only rows with a
    NULL ``resolved_up`` are considered, and anything it sets is tagged
    ``resolved_src='terminal'`` so a caller can always exclude derived labels
    with ``WHERE resolved_src='feed'``.
    """
    # Any pre-existing resolution came from a market_resolved event.
    conn.execute("UPDATE pm_l2_market SET resolved_src='feed' "
                 "WHERE resolved_up IS NOT NULL AND resolved_src IS NULL")
    # Last two-sided quote per unresolved window.
    rows = conn.execute(
        "SELECT m.start_ts, ("
        "  SELECT q.mid FROM pm_l2_quote q "
        "  WHERE q.start_ts = m.start_ts AND q.bid IS NOT NULL AND q.ask IS NOT NULL "
        "  ORDER BY q.time DESC LIMIT 1) AS term_mid "
        "FROM pm_l2_market m WHERE m.resolved_up IS NULL").fetchall()
    upd = [(1 if r["term_mid"] >= SETTLE_HI else 0, r["start_ts"])
           for r in rows
           if r["term_mid"] is not None
           and (r["term_mid"] >= SETTLE_HI or r["term_mid"] <= SETTLE_LO)]
    if upd:
        conn.executemany("UPDATE pm_l2_market SET resolved_up=?, resolved_src='terminal' "
                         "WHERE start_ts=?", upd)
    conn.commit()
    return {"candidates": len(rows), "derived": len(upd),
            "undetermined": len(rows) - len(upd)}


def loaded_days(conn) -> "set[str]":
    return {r[0] for r in conn.execute(
        "SELECT data_date FROM pmdata_day WHERE series=? AND data_type=?",
        (SERIES, DATA_TYPE))}


# ---- orchestration ----------------------------------------------------------

def download_all(days: "list[date]", *, force: bool = False) -> "list[date]":
    """Fetch every missing archive, sequentially. Returns the days now on disk."""
    have = []
    s = requests.Session()
    t0 = time.time()
    got_bytes = 0
    for i, d in enumerate(days, 1):
        try:
            r = pmdata.download_day(SERIES, DATA_TYPE, d, force=force, session=s)
        except pmdata.PMDataError as e:
            print(f"  [{i}/{len(days)}] {d} FAILED: {e}", flush=True)
            continue
        if r["status"] == "missing":
            print(f"  [{i}/{len(days)}] {d} not published by PMData - skipped", flush=True)
            continue
        have.append(d)
        if r["status"] == "ok":
            got_bytes += r["bytes"]
            rate = got_bytes / max(time.time() - t0, 1e-9) / 1e6
            print(f"  [{i}/{len(days)}] {d} {r['bytes']/1e6:7.1f} MB "
                  f"in {r.get('secs', 0):5.1f}s  (avg {rate:.0f} MB/s)", flush=True)
    return have


def ingest_all(days: "list[date]", *, workers: int, want_ladder: bool,
               db_path=None, redo: bool = False) -> dict:
    conn = db.connect(db_path)
    try:
        done = set() if redo else loaded_days(conn)
        todo = [d for d in days
                if f"{d:%Y-%m-%d}" not in done and pmdata.day_file(SERIES, DATA_TYPE, d).exists()]
        if not todo:
            print("nothing to fold: every requested day is already in the DB.", flush=True)
            return {"days": 0, "quotes": 0, "books": 0, "events": 0}

        print(f"Folding {len(todo)} day(s) with {workers} worker(s) -> "
              f"{db_path or db.db_path()}", flush=True)
        jobs = [(str(pmdata.day_file(SERIES, DATA_TYPE, d)), f"{d:%Y-%m-%d}", want_ladder)
                for d in todo]

        n_q = n_b = n_e = 0
        failures: list = []
        t0 = time.time()
        # imap (ordered) keeps writes chronological, so both B-trees stay append-only.
        with mp.get_context("fork").Pool(workers) as pool:
            for i, res in enumerate(pool.imap(_fold_star, jobs), 1):
                write_day(conn, res)
                n_q += len(res["quotes"])
                n_b += len(res["books"])
                n_e += res["events"]
                bad = res.get("failed") or []
                failures += [f"{res['day']} {m}" for m in bad]
                el = time.time() - t0
                eta = el / i * (len(jobs) - i)
                print(f"  [{i}/{len(jobs)}] {res['day']}  markets={len(res['metas']):3d} "
                      f"events={res['events']:>10,}  sec_rows={len(res['quotes']):>7,}"
                      f"{f'  SKIPPED {len(bad)}' if bad else ''}  "
                      f"({el/60:.1f}m elapsed, ETA {eta/60:.0f}m)", flush=True)
        if failures:
            print(f"\n!! {len(failures)} market(s) could not be folded:", flush=True)
            for f in failures[:20]:
                print(f"   {f}", flush=True)
            if len(failures) > 20:
                print(f"   ... and {len(failures)-20} more", flush=True)
        return {"days": len(jobs), "quotes": n_q, "books": n_b, "events": n_e,
                "failures": failures, "secs": time.time() - t0}
    finally:
        conn.close()


def _fold_star(a):
    return fold_day(*a)


def status(db_path=None) -> None:
    files, nbytes = pmdata.archive_size()
    print(f"archive : {pmdata.archive_root()}")
    print(f"          {files} zip(s), {nbytes/1e9:.1f} GB")
    local = pmdata.local_days(SERIES, DATA_TYPE)
    if local:
        print(f"          {SERIES}/{DATA_TYPE}: {len(local)} day(s) {local[0]} .. {local[-1]}")
    conn = db.connect(db_path)
    try:
        d = conn.execute("SELECT COUNT(*) n, MIN(data_date) lo, MAX(data_date) hi, "
                         "SUM(events) e FROM pmdata_day WHERE series=? AND data_type=?",
                         (SERIES, DATA_TYPE)).fetchone()
        m = conn.execute("SELECT COUNT(*) n, SUM(resolved_up IS NOT NULL) r, "
                         "SUM(resolved_src='feed') rf, SUM(resolved_src='terminal') rt, "
                         "MIN(start_ts) lo, MAX(start_ts) hi FROM pm_l2_market").fetchone()
        q = conn.execute("SELECT COUNT(*) n FROM pm_l2_quote").fetchone()
        b = conn.execute("SELECT COUNT(*) n FROM pm_l2_book").fetchone()
        print(f"db      : {db_path or db.db_path()}")
        print(f"          pmdata_day   {d['n']} day(s) {d['lo']} .. {d['hi']}, "
              f"{(d['e'] or 0)/1e9:.2f}B events folded")
        if m["n"]:
            lo = datetime.fromtimestamp(m["lo"], timezone.utc)
            hi = datetime.fromtimestamp(m["hi"], timezone.utc)
            print(f"          pm_l2_market {m['n']:,} windows, {m['r'] or 0:,} resolved "
                  f"({m['rf'] or 0:,} feed / {m['rt'] or 0:,} terminal) "
                  f"({lo:%Y-%m-%d %H:%M} .. {hi:%Y-%m-%d %H:%M} UTC)")
        print(f"          pm_l2_quote  {q['n']:,} rows")
        print(f"          pm_l2_book   {b['n']:,} rows")
    finally:
        conn.close()


def _cli(argv=None):
    ap = argparse.ArgumentParser(
        description="Backfill Polymarket price + L2 order book history from PMData.")
    ap.add_argument("--from", dest="start", default=None, help="first day (YYYY-MM-DD)")
    ap.add_argument("--to", dest="end", default=None, help="last day (YYYY-MM-DD)")
    ap.add_argument("--workers", type=int, default=max(1, min(12, (os.cpu_count() or 4) - 2)))
    ap.add_argument("--download-only", action="store_true", help="fill the archive, do not fold")
    ap.add_argument("--ingest-only", action="store_true", help="fold local archives, do not download")
    ap.add_argument("--no-ladder", action="store_true", help="skip pm_l2_book (saves ~8 GB)")
    ap.add_argument("--redo", action="store_true", help="re-fold days already in pmdata_day")
    ap.add_argument("--no-derive", action="store_true",
                    help="skip deriving outcomes the feed never reported")
    ap.add_argument("--force-download", action="store_true", help="re-fetch archives already on disk")
    ap.add_argument("--status", action="store_true", help="print coverage and exit")
    ap.add_argument("--db", default=None, help="override DB path")
    args = ap.parse_args(argv)

    if args.status:
        status(args.db)
        return 0

    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None
    days = pmdata.day_range(SERIES, start, end)
    if not days:
        print("empty day range.", file=sys.stderr)
        return 1
    print(f"{SERIES}/{DATA_TYPE}: {len(days)} day(s) {days[0]} .. {days[-1]}", flush=True)

    if not args.ingest_only:
        print(f"\n== download -> {pmdata.archive_root()}", flush=True)
        download_all(days, force=args.force_download)
    if args.download_only:
        status(args.db)
        return 0

    print("\n== fold into SQLite", flush=True)
    r = ingest_all(days, workers=args.workers, want_ladder=not args.no_ladder,
                   db_path=args.db, redo=args.redo)
    if r["days"]:
        print(f"\nDone in {r['secs']/60:.1f}m: {r['days']} day(s), "
              f"{r['events']:,} events -> {r['quotes']:,} quote rows, "
              f"{r['books']:,} ladder rows.", flush=True)

    if not args.no_derive:
        conn = db.connect(args.db)
        try:
            d = derive_outcomes(conn)
        finally:
            conn.close()
        if d["candidates"]:
            print(f"\noutcomes: {d['derived']:,} of {d['candidates']:,} unresolved window(s) "
                  f"derived from the settled book (resolved_src='terminal'); "
                  f"{d['undetermined']:,} left undetermined.", flush=True)
    print()
    status(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
