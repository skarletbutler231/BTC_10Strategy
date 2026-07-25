"""Read helpers for the Polymarket 5-minute UP/DOWN data (pm_window / pm_quote).

Backfilled + kept live by ``backend.data.ingest_stream``. These let a backtest
price a Polymarket entry from the *actual* YES(UP) quote at a given moment in a
window, instead of assuming a flat 0.5 — and settle on the real Chainlink
outcome. Read-only; mirrors the style of ``backend.store``.
"""

from __future__ import annotations

from . import db


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
