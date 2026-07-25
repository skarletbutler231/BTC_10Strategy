"""Quantify the Binance -> Chainlink basis over the overlap window.

Binance is your deep-history backtest source, but Polymarket 5m/15m markets
settle on Chainlink. This measures how far apart they are — and, crucially, how
often a Binance candle's UP/DOWN direction *disagrees* with the Chainlink
candle's direction at the settlement horizon. That disagreement rate is the real
error bar on any Binance-based edge estimate.

    python3 -m backend.data.basis_report              # 5m horizon (default)
    python3 -m backend.data.basis_report --horizon 15m

Reads Chainlink ``BTCUSD_CL`` straight from the DB (ingest it first with
``ingest_chainlink``) and Binance ``BTCUSDT`` via the normal store path.
"""

from __future__ import annotations

import argparse
import statistics as st
from datetime import datetime, timezone

from .. import db, store

CL_SYMBOL = "BTCUSD_CL"
BN_SYMBOL = "BTCUSDT"


def _read_cl_1m(lo: int, hi: int) -> list:
    conn = db.connect(readonly=True)
    try:
        cur = conn.execute(
            "SELECT time, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND interval='1m' AND time BETWEEN ? AND ? ORDER BY time",
            (CL_SYMBOL, lo, hi))
        return [dict(r) for r in cur]
    finally:
        conn.close()


def _cl_coverage() -> "tuple[int, int, int]":
    conn = db.connect(readonly=True)
    try:
        r = conn.execute(
            "SELECT MIN(time) lo, MAX(time) hi, COUNT(*) n FROM candles "
            "WHERE symbol=? AND interval='1m'", (CL_SYMBOL,)).fetchone()
        return r["lo"], r["hi"], r["n"]
    finally:
        conn.close()


def _direction(c: dict) -> int:
    return 1 if c["close"] > c["open"] else -1 if c["close"] < c["open"] else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Binance vs Chainlink basis report.")
    ap.add_argument("--horizon", default="5m", help="settlement horizon (e.g. 5m, 15m)")
    args = ap.parse_args(argv)

    lo, hi, n = _cl_coverage()
    if not n:
        print(f"no {CL_SYMBOL} candles in the DB — run ingest_chainlink first.")
        return 1
    hsec = store.INTERVAL_SECONDS.get(args.horizon)
    if not hsec:
        print(f"unsupported horizon: {args.horizon}")
        return 1

    span = (f"{datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d %H:%M} .. "
            f"{datetime.fromtimestamp(hi, timezone.utc):%Y-%m-%d %H:%M} UTC")
    print(f"Overlap: {span}  ({n:,} Chainlink 1m candles)\n")

    cl_1m = _read_cl_1m(lo, hi)
    bn_1m = store.get_candles(BN_SYMBOL, "1m", lo * 1000, hi * 1000)

    # align on common minutes
    bn_by_t = {c["time"]: c for c in bn_1m}
    common = [(c, bn_by_t[c["time"]]) for c in cl_1m if c["time"] in bn_by_t]
    if not common:
        print("no overlapping minutes between Chainlink and Binance.")
        return 1

    # --- price basis on 1m closes (bps of Binance price) --------------------
    bps = [10_000.0 * (cl["close"] - bn["close"]) / bn["close"] for cl, bn in common]
    abps = [abs(x) for x in bps]
    print(f"[price basis]  {len(common):,} aligned 1m closes")
    print(f"    mean |Δ|   = {st.mean(abps):.2f} bps")
    print(f"    median |Δ| = {st.median(abps):.2f} bps")
    print(f"    p95 |Δ|    = {sorted(abps)[int(0.95 * (len(abps) - 1))]:.2f} bps")
    print(f"    max |Δ|    = {max(abps):.2f} bps")
    print(f"    signed mean= {st.mean(bps):+.2f} bps  (Chainlink − Binance)\n")

    # --- direction agreement at the settlement horizon ----------------------
    cl_h = store._resample(cl_1m, hsec, lo, hi)
    bn_common = [bn for _, bn in common]
    bn_h = store._resample(bn_common, hsec, lo, hi)
    cl_h_by_t = {c["time"]: c for c in cl_h}

    agree = flips = near_flip = decided = 0
    for bn in bn_h:
        cl = cl_h_by_t.get(bn["time"])
        if cl is None:
            continue
        db_dir, cb_dir = _direction(bn), _direction(cl)
        if db_dir == 0 or cb_dir == 0:
            continue                       # flat window — no bet either way
        decided += 1
        if db_dir == cb_dir:
            agree += 1
        else:
            flips += 1
            move_bps = abs(10_000.0 * (bn["close"] - bn["open"]) / bn["open"])
            if move_bps < 5.0:             # flip on a near-strike (<5 bps) move
                near_flip += 1

    print(f"[direction @ {args.horizon}]  {decided} decided windows "
          f"(of {len(bn_h)} total)")
    if decided:
        print(f"    agreement  = {100.0 * agree / decided:.1f}%  "
              f"({agree} agree / {flips} flip)")
        print(f"    -> a Binance-based signal would settle WRONG on ~"
              f"{100.0 * flips / decided:.1f}% of decided {args.horizon} bets")
        if flips:
            print(f"    of those flips, {near_flip} ({100.0*near_flip/flips:.0f}%) "
                  f"were near-strike (<5 bps move) — the manipulable zone")
    if decided < 50:
        print("\n    NOTE: small sample — rerun after a fuller backfill for a "
              "stable number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
