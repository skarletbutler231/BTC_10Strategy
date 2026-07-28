"""Read helpers for the Polymarket 5-minute UP/DOWN data.

Two sources, same 5-minute ``start_ts`` grid:

  * ``pm_window`` / ``pm_quote`` — this machine's own pmqb capture, backfilled
    and kept live by ``backend.data.ingest_stream``. Carries the model's p_up
    alongside the quote, but only reaches back to the day the bot started.
  * ``pm_l2_market`` / ``pm_l2_quote`` / ``pm_l2_book`` — PMData's full history
    (from 2026-02-13), loaded by ``backend.data.ingest_pmdata``. No p_up, but it
    carries real order book **depth**, so a backtest can size an order against
    the book that was actually resting instead of assuming the top of book is
    infinitely deep.

Together they let a backtest price an entry from the *actual* YES(UP) book at a
given moment in a window instead of assuming a flat 0.5, and settle on the real
outcome. Read-only; mirrors the style of ``backend.store``.
"""

from __future__ import annotations

from . import db

# ---- ladder blob format (see the pm_l2_book comment in db.py) ---------------
TICK = 1000          # price resolution 0.001
SLOTS = 2 * TICK     # [0:1000) bids at p/1000, [1000:2000) asks at (p-1000)/1000
SIZE_SCALE = 100     # stored value is shares*100
LADDER_NBYTES = SLOTS * 4


def decode_ladder(blob: bytes):
    """Inflate a ``pm_l2_book.ladder`` blob to a 2000-slot numpy uint32 array.

    Slot p in [0,1000) is the size resting on the **bid** at price p/1000; slot
    1000+p the **ask** at p/1000. Divide by SIZE_SCALE for shares. pyarrow is
    imported lazily so the dashboard does not pay for it at startup.
    """
    import numpy as np
    import pyarrow as pa
    buf = pa.decompress(blob, decompressed_size=LADDER_NBYTES, codec="zstd")
    return np.frombuffer(memoryview(buf), dtype="<u4")


def encode_ladder(ladder) -> bytes:
    """Deflate a 2000-slot uint32 ladder for storage. Inverse of decode_ladder."""
    import pyarrow as pa
    return pa.compress(ladder.astype("<u4").tobytes(), codec="zstd", asbytes=True)


def coverage() -> dict:
    """{windows, resolved, quotes, first_ts, last_ts} for the Polymarket data."""
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001 - DB not built yet
        return {"windows": 0, "resolved": 0, "quotes": 0, "first_ts": None, "last_ts": None}
    try:
        w = conn.execute(
            "SELECT COUNT(*) n, SUM(resolved_up IS NOT NULL) r, "
            "MIN(start_ts) lo, MAX(start_ts) hi FROM pm_window").fetchone()
        q = conn.execute("SELECT COUNT(*) n FROM pm_quote").fetchone()
        return {"windows": w["n"], "resolved": w["r"] or 0, "quotes": q["n"],
                "first_ts": w["lo"], "last_ts": w["hi"]}
    finally:
        conn.close()


def windows(start_ts: int, end_ts: int, *, resolved_only: bool = False) -> "list[dict]":
    """pm_window rows with start_ts in [start_ts, end_ts] (unix seconds), ascending."""
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001
        return []
    try:
        sql = ("SELECT start_ts, market_id, slug, end_ts, start_price, end_price, "
               "resolved_up, resolved_src FROM pm_window "
               "WHERE start_ts BETWEEN ? AND ?")
        if resolved_only:
            sql += " AND resolved_up IS NOT NULL"
        sql += " ORDER BY start_ts"
        return [dict(r) for r in conn.execute(sql, (start_ts, end_ts))]
    finally:
        conn.close()


def quotes(window_start_ts: int) -> "list[dict]":
    """All YES(UP) quotes for one window, ascending; each has an `elapsed` (sec)."""
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001
        return []
    try:
        rows = conn.execute(
            "SELECT time, yes, yes_bid, yes_ask FROM pm_quote "
            "WHERE start_ts=? ORDER BY time", (window_start_ts,)).fetchall()
        return [{**dict(r), "elapsed": r["time"] - window_start_ts} for r in rows]
    finally:
        conn.close()


def quote_at(window_start_ts: int, elapsed: int) -> "dict | None":
    """The YES(UP) quote at/just before `elapsed` seconds into the window.

    This is the realistic entry price for a bet placed `elapsed` seconds in.
    """
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        r = conn.execute(
            "SELECT time, yes, yes_bid, yes_ask FROM pm_quote "
            "WHERE start_ts=? AND time<=? ORDER BY time DESC LIMIT 1",
            (window_start_ts, window_start_ts + int(elapsed))).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


# ============================================================================
# PMData L2 (full history + real book depth) — see backend.data.ingest_pmdata.
# ============================================================================

_L2_COLS = ("time, bid, ask, mid, bid_sz, ask_sz, "
            "bid_d1, ask_d1, bid_d5, ask_d5, bid_d10, ask_d10, n_events")


def l2_coverage() -> dict:
    """Coverage counts for the L2 data.

    ``resolved_feed`` are outcomes the exchange feed reported; ``resolved_terminal``
    were derived from the settled book because PMData did not record
    ``market_resolved`` before ~2026-03-28. Keep them apart when the distinction
    matters — see ``backend.data.ingest_pmdata.derive_outcomes``.
    """
    empty = {"windows": 0, "resolved": 0, "resolved_feed": 0, "resolved_terminal": 0,
             "quotes": 0, "books": 0, "first_ts": None, "last_ts": None, "days": 0}
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001
        return empty
    try:
        m = conn.execute(
            "SELECT COUNT(*) n, SUM(resolved_up IS NOT NULL) r, "
            "SUM(resolved_src='feed') rf, SUM(resolved_src='terminal') rt, "
            "MIN(start_ts) lo, MAX(start_ts) hi FROM pm_l2_market").fetchone()
        q = conn.execute("SELECT COUNT(*) n FROM pm_l2_quote").fetchone()
        b = conn.execute("SELECT COUNT(*) n FROM pm_l2_book").fetchone()
        d = conn.execute("SELECT COUNT(*) n FROM pmdata_day").fetchone()
        return {"windows": m["n"], "resolved": m["r"] or 0,
                "resolved_feed": m["rf"] or 0, "resolved_terminal": m["rt"] or 0,
                "quotes": q["n"], "books": b["n"],
                "first_ts": m["lo"], "last_ts": m["hi"], "days": d["n"]}
    except Exception:  # noqa: BLE001 - tables not created yet
        return empty
    finally:
        conn.close()


def l2_quotes(window_start_ts: int) -> "list[dict]":
    """Every per-second book state for one window, ascending, with `elapsed`."""
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001
        return []
    try:
        rows = conn.execute(
            f"SELECT {_L2_COLS} FROM pm_l2_quote WHERE start_ts=? ORDER BY time",
            (window_start_ts,)).fetchall()
        return [{**dict(r), "elapsed": r["time"] - window_start_ts} for r in rows]
    finally:
        conn.close()


def l2_quote_at(window_start_ts: int, elapsed: int) -> "dict | None":
    """Book state at/just before `elapsed` seconds into the window.

    Rows exist only for seconds in which the book actually changed, so this
    takes the most recent state at or before the requested moment.
    """
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        r = conn.execute(
            f"SELECT {_L2_COLS} FROM pm_l2_quote "
            "WHERE start_ts=? AND time<=? ORDER BY time DESC LIMIT 1",
            (window_start_ts, window_start_ts + int(elapsed))).fetchone()
        return {**dict(r), "elapsed": r["time"] - window_start_ts} if r else None
    finally:
        conn.close()


def l2_book_at(window_start_ts: int, elapsed: int) -> "dict | None":
    """Full ladder at/just before `elapsed` seconds in.

    Returns ``{time, elapsed, bids, asks}`` where each side is a list of
    ``(price, shares)`` ordered best-first.
    """
    try:
        conn = db.connect(readonly=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        r = conn.execute(
            "SELECT time, ladder FROM pm_l2_book "
            "WHERE start_ts=? AND time<=? ORDER BY time DESC LIMIT 1",
            (window_start_ts, window_start_ts + int(elapsed))).fetchone()
        if not r:
            return None
        lad = decode_ladder(r["ladder"])
        bids = [(p / TICK, int(lad[p]) / SIZE_SCALE)
                for p in range(TICK - 1, 0, -1) if lad[p]]
        asks = [(p / TICK, int(lad[TICK + p]) / SIZE_SCALE)
                for p in range(1, TICK) if lad[TICK + p]]
        return {"time": r["time"], "elapsed": r["time"] - window_start_ts,
                "bids": bids, "asks": asks}
    finally:
        conn.close()


def l2_fill(window_start_ts: int, elapsed: int, shares: float,
            side: str = "buy") -> "dict | None":
    """Walk the resting book to price a market order of `shares`.

    ``side='buy'`` lifts the ask ladder, ``'sell'`` hits the bid ladder. This is
    the point of storing full depth: a large order does not fill at the top of
    book, and on Polymarket's 5m markets the book past the best level is often
    thin. Returns ``{avg_price, filled, unfilled, worst_price, levels, top}``;
    ``avg_price`` is None if nothing could be filled at all.
    """
    book = l2_book_at(window_start_ts, elapsed)
    if not book:
        return None
    levels = book["asks"] if side == "buy" else book["bids"]
    want = float(shares)
    cost = filled = 0.0
    worst = None
    used = 0
    for price, avail in levels:
        if want <= 0:
            break
        take = min(want, avail)
        cost += take * price
        filled += take
        want -= take
        worst = price
        used += 1
    return {"avg_price": (cost / filled) if filled else None,
            "filled": filled, "unfilled": max(want, 0.0),
            "worst_price": worst, "levels": used,
            "top": levels[0][0] if levels else None,
            "time": book["time"], "elapsed": book["elapsed"]}
