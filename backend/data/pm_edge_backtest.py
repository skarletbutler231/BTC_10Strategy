"""CLI for the PM Edge strategy backtest (backend.pm_edge).

    python3 -m backend.data.pm_edge_backtest                    # defaults, full history
    python3 -m backend.data.pm_edge_backtest --from 2026-07-07  # date range
    python3 -m backend.data.pm_edge_backtest --model chainlink --entry-from 180 --entry-to 210
    python3 -m backend.data.pm_edge_backtest --delta 0.10 --fee 0.04 --price mid
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .. import db, pm_edge


def _to_ts(s: str, *, end: bool = False) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def _coverage():
    conn = db.connect(readonly=True)
    try:
        r = conn.execute(
            "SELECT MIN(start_ts) lo, MAX(start_ts) hi FROM pm_window "
            "WHERE resolved_up IS NOT NULL").fetchone()
        return r["lo"], r["hi"]
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PM Edge backtest")
    ap.add_argument("--from", dest="frm", default=None, help="YYYY-MM-DD (default: earliest)")
    ap.add_argument("--to", dest="to", default=None, help="YYYY-MM-DD (default: latest)")
    ap.add_argument("--model", default="binance", choices=["binance", "chainlink"])
    ap.add_argument("--direction", default="follow", choices=["follow", "fade"])
    ap.add_argument("--entry-from", type=int, default=120)
    ap.add_argument("--entry-to", type=int, default=180)
    ap.add_argument("--delta", type=float, default=0.12)
    ap.add_argument("--fee", type=float, default=0.04)
    ap.add_argument("--price", default="exec", choices=["exec", "mid"])
    args = ap.parse_args(argv)

    lo, hi = _coverage()
    if lo is None:
        print("no resolved Polymarket windows in the DB — run ingest_stream first.")
        return 1
    start = _to_ts(args.frm) if args.frm else lo
    end = _to_ts(args.to, end=True) if args.to else hi

    cfg = pm_edge.PMEdgeConfig(
        model=args.model, direction=args.direction,
        entry_from=args.entry_from, entry_to=args.entry_to,
        delta=args.delta, fee=args.fee, price=args.price)
    res = pm_edge.run(start, end, cfg)
    s = res["stats"]

    fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
    print(f"PM Edge  {args.model}/{args.direction}  entry {args.entry_from}-{args.entry_to}s  "
          f"delta {args.delta}  fee {args.fee:.0%}  price {args.price}")
    print(f"range    {fmt(start)} .. {fmt(end)}\n")
    if s["bets"] == 0:
        print("no bets."); return 0
    print(f"  bets          {s['bets']:>8d}")
    print(f"  hit rate      {s['hit_rate']:>7.2f}%   (breakeven {s['breakeven']:.1f}%, avg odds {s['avg_odds']:.3f})")
    print(f"  UP / DOWN     {s['up_bets']:>8d} / {s['down_bets']}")
    print(f"  EV / bet      {s['ev_per_bet']:>+8.4f}   (per $1 stake)")
    print(f"  total P/L     {s['total_pnl']:>+8.2f}   (ROI {s['roi_pct']:+.2f}% on stake turned over)")
    print(f"  max drawdown  {s['max_drawdown']:>+8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
