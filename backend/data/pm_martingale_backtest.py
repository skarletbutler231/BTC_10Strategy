"""CLI for the recovery-sized martingale over Polymarket 5m / 15m windows.

    # sweep the ladder depth over the last 6 months (the headline question)
    python3 -m backend.data.pm_martingale_backtest --from 2026-03-04 --to 2026-09-04

    # one depth, in detail
    python3 -m backend.data.pm_martingale_backtest --depth 3 --fee 0.02

    # another strategy / preset, later entry, mid pricing
    python3 -m backend.data.pm_martingale_backtest --strategy jump_exhaustion \\
        --preset "PM 5m Volume" --entry-el 15 --price mid

    # the 15-minute market (real pmqb 15m quotes from 2026-07-03), taker fee,
    # with entry predicates on the signal's features
    python3 -m backend.data.pm_martingale_backtest --interval 15m \\
        --preset "PM 15m Volume" --from 2026-07-03 --to 2026-09-13 \\
        --fee 0.07 --fee-model taker --require "fill>=0.5"
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import re

from .. import pm_martingale as pmm


def _ts(s: str, *, end: bool = False) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) + (86399 if end else 0)


def signals_for(strategy_id: str, preset: str, lo: int, hi: int,
                symbol: str = "BTCUSDT", interval: str = "5m", warmup: int = 400):
    """Thin wrapper so the CLI's error path is a clean exit, not a traceback."""
    try:
        return pmm.signals_for(strategy_id, preset, lo, hi, symbol, interval, warmup)
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))


_PRED = re.compile(r"^\s*(\w+)\s*(<=|>=|==|!=|<|>)\s*(.+?)\s*$")


def parse_require(texts) -> tuple:
    """``["fill>=0.5", "side_up==True"]`` -> ``(("fill", ">=", 0.5), ...)``.

    Values parse as bool (True/False), then number, else stay a string.
    """
    out = []
    for t in texts or ():
        m = _PRED.match(t)
        if not m:
            raise SystemExit(f"bad --require {t!r}; want e.g. 'fill>=0.5'")
        key, op, raw = m.groups()
        if raw in ("True", "False"):
            val = raw == "True"
        else:
            try:
                val = float(raw)
            except ValueError:
                val = raw
        out.append((key, op, val))
    return tuple(out)


def _print_rungs(rows: list) -> None:
    print(f"  {'rung':>4} {'bets':>6} {'hit':>8} {'avg px':>8} {'breakeven':>10} {'avg cost':>10}")
    for r in rows:
        print(f"  {r['depth']:>4} {r['bets']:>6} {r['hit_rate']:>7.2f}% "
              f"{r['avg_price']:>8.4f} {100 * r['avg_price']:>9.2f}% {r['avg_cost']:>10.3f}")


def _print_periods(runs: dict, by: str, target: float) -> None:
    """Per-period P&L for one or more ladder depths, side by side.

    Chain counts differ between depths on purpose: a deeper chain occupies the
    windows behind it, so it swallows signals a flat bet would have taken.
    """
    tables = {d: pmm.period_table(cs, by) for d, cs in sorted(runs.items())}
    depths = [d for d, t in tables.items() if t]
    if not depths:
        print("no chains."); return
    rows = {d: {r["period"]: r for r in tables[d]} for d in depths}
    periods = sorted({k for d in depths for k in rows[d]})

    label = "week of" if by == "week" else "month"
    head, rule = f"{label:>11}", f"{'':>11}"
    for d in depths:
        head += f" | {('D=' + str(d)):>4} {'chains':>6} {'bust':>4} {'PnL':>8} {'cum':>9}"
        rule += " | " + "-" * 34
    if len(depths) == 2:
        head += f" | {'cum diff':>9}"
        rule += " | " + "-" * 9
    print(head); print(rule)
    for k in periods:
        line = f"{k:>11}"
        for d in depths:
            r = rows[d].get(k)
            line += (f" | {'':>4} {r['chains'] if r else 0:>6} "
                     f"{r['busts'] if r else 0:>4} {(r['pnl'] if r else 0):>+8.2f} "
                     f"{(r['cum'] if r else 0):>+9.2f}")
        if len(depths) == 2:
            a = rows[depths[0]].get(k, {}).get("cum", 0.0)
            b = rows[depths[1]].get(k, {}).get("cum", 0.0)
            line += f" | {b - a:>+9.2f}"
        print(line)

    print()
    for d in depths:
        t = tables[d]
        green = sum(1 for r in t if r["pnl"] > 0)
        busts = [c.pnl for c in runs[d] if c.rungs and c.outcome != "won"]
        print(f"  D={d}: {green}/{len(t)} green {by}s ({green / len(t):.0%}), "
              f"{len(busts)} busts, worst chain {min(busts, default=0.0):+.2f} "
              f"(~{-min(busts, default=0.0) / target:.1f}x the ${target:g} target)")


def selftest() -> int:
    """Hand-checked arithmetic for the recovery ladder, on a synthetic book.

    There is no test suite in this repo, and the sizing formula is the one thing
    here that is easy to get subtly wrong, so the invariants live behind
    ``--selftest`` rather than nowhere.
    """
    import numpy as np
    W = pmm.WINDOW
    cfg = pmm.MartingaleConfig

    def mkt(*specs):        # (resolved_up, bid, ask) for consecutive windows
        return {i * W: {"up": u, "bid": b, "ask": a, "src": "synthetic"}
                for i, (u, b, a) in enumerate(specs)}

    assert abs(pmm.breakeven(0.60, 0.0) - 0.60) < 1e-12
    assert abs((1 - pmm.breakeven(0.6, 0.05)) - (1 - 0.6) * (1 - 0.05)) < 1e-12

    # win on rung 1 -> banks exactly the target on 2 shares of a 50c fill
    c = pmm.run([(0, True)], mkt((1, 0.49, 0.50)), cfg(max_depth=3))["chains"][0]
    assert c.outcome == "won" and abs(c.pnl - 1.0) < 1e-9, c
    assert abs(c.rungs[0]["shares"] - 2.0) < 1e-9 and abs(c.staked - 1.0) < 1e-9, c

    # lose rung 1, win rung 2 -> still exactly the target; rung 2 repays $1 + $1
    c = pmm.run([(0, True)], mkt((0, 0.49, 0.50), (1, 0.49, 0.50)),
                cfg(max_depth=3))["chains"][0]
    assert c.outcome == "won" and abs(c.pnl - 1.0) < 1e-9, c
    assert abs(c.rungs[1]["shares"] - 4.0) < 1e-9 and abs(c.staked - 3.0) < 1e-9, c

    # bust at max_depth -> loses exactly what it staked
    c = pmm.run([(0, True)], mkt((0, 0.49, 0.50), (0, 0.49, 0.50)),
                cfg(max_depth=2))["chains"][0]
    assert c.outcome == "busted" and abs(c.pnl + 3.0) < 1e-9, c

    # a 10% take on winnings raises the effective cost, so the rung buys more
    c = pmm.run([(0, True)], mkt((1, 0.49, 0.50)),
                cfg(max_depth=1, fee=0.10))["chains"][0]
    assert abs(c.rungs[0]["shares"] - 1 / (1 - 0.55)) < 1e-4, c   # breakeven(.5,.1)=.55
    assert abs(c.pnl - 1.0) < 1e-9, c

    # a DOWN bet buys NO at 1 - bid
    c = pmm.run([(0, False)], mkt((0, 0.60, 0.62)), cfg(max_depth=1))["chains"][0]
    assert abs(c.rungs[0]["price"] - 0.40) < 1e-9, c

    # one chain at a time: a signal inside a live chain is skipped
    r = pmm.run([(0, True), (W, True)], mkt((0, 0.49, 0.50), (1, 0.49, 0.50)),
                cfg(max_depth=2))
    assert len(r["chains"]) == 1 and r["stats"]["signals_skipped_in_chain"] == 1, r["stats"]

    # a gap in the capture aborts rather than inventing a rung
    c = pmm.run([(0, True)], mkt((0, 0.49, 0.50)), cfg(max_depth=3))["chains"][0]
    assert c.outcome == "aborted" and abs(c.pnl + 1.0) < 1e-9, c

    # ladder sizing: a thin book raises the fill price and the share count with it
    book = {0: (np.array([0.50, 0.55]), np.array([1.0, 100.0]),
                np.array([0.50]), np.array([100.0]))}
    c = pmm.run([(0, True)], mkt((1, 0.49, 0.50)),
                cfg(max_depth=1, use_book=True), book)["chains"][0]
    assert c.rungs[0]["price"] > 0.50 and c.rungs[0]["shares"] > 2.0, c
    assert abs(c.pnl - 1.0) < 1e-9, c        # still the target, it just costs more

    # a book too thin to fill at all is an abort, not a free fill
    book = {0: (np.array([0.50]), np.array([0.1]), np.array([0.50]), np.array([0.1]))}
    c = pmm.run([(0, True)], mkt((1, 0.49, 0.50)),
                cfg(max_depth=1, use_book=True), book)["chains"][0]
    assert c.outcome == "aborted" and not c.rungs, c

    # book lean is ORIENTED to the side: the same book reads +1 for an UP bet
    # and -1 for a DOWN bet, which is the whole point of the feature.
    assert pmm.book_lean({"bid_sz": 300.0, "ask_sz": 100.0}, True) == 0.5
    assert pmm.book_lean({"bid_sz": 300.0, "ask_sz": 100.0}, False) == -0.5
    assert pmm.book_lean({"bid_sz": 0.0, "ask_sz": 0.0}, True) == 0.0   # no lean
    assert pmm.book_lean({"bid": 0.49, "ask": 0.50}, True) is None      # no sizes
    assert pmm.book_lean(None, True) is None

    # ...and a window with no sizes FAILS a top_imb_sd predicate rather than
    # passing it, so an unquotable stretch is filtered out, not silently kept.
    sized = {0: {"up": 1, "bid": 0.49, "ask": 0.50, "src": "s",
                 "bid_sz": 300.0, "ask_sz": 100.0},
             W: {"up": 1, "bid": 0.49, "ask": 0.50, "src": "s"}}
    req = (("top_imb_sd", ">=", 0.0),)
    r = pmm.run([(0, True), (W, True)], sized, cfg(max_depth=1, require=req))
    assert r["stats"]["chains"] == 1 and r["stats"]["signals_filtered_out"] == 1, r["stats"]
    # the DOWN bet on the same book leans against us and is filtered out too
    r = pmm.run([(0, False)], sized, cfg(max_depth=1, require=req))
    assert r["stats"]["chains"] == 0, r["stats"]

    # Polymarket's taker fee is paid on the BUY (shares * rate * p * (1 - p)), so
    # a losing rung loses it too, and a win still banks exactly the target.
    tk = dict(fee=0.07, fee_model="taker")
    be = 0.5 + 0.07 * 0.25
    c = pmm.run([(0, True)], mkt((1, 0.49, 0.50)), cfg(max_depth=1, **tk))["chains"][0]
    assert abs(c.rungs[0]["shares"] - 1 / (1 - be)) < 1e-4 and abs(c.pnl - 1.0) < 1e-9, c
    assert abs(c.staked - be / (1 - be)) < 1e-4, c
    c = pmm.run([(0, True)], mkt((0, 0.49, 0.50), (0, 0.49, 0.50)),
                cfg(max_depth=2, **tk))["chains"][0]
    r1 = be / (1 - be)
    assert c.outcome == "busted" and abs(c.pnl + r1 + (r1 + 1) * be / (1 - be)) < 1e-4, c

    # 15m: the next rung is the window 900s later, and a chain blocks 900s.
    W15 = pmm.SERIES["15m"]
    m15 = {0: {"up": 0, "bid": 0.49, "ask": 0.50, "src": "s"},
           W15: {"up": 1, "bid": 0.49, "ask": 0.50, "src": "s"},
           2 * W15: {"up": 1, "bid": 0.49, "ask": 0.50, "src": "s"}}
    r = pmm.run([(0, True), (W15, True), (2 * W15, True)], m15, cfg(max_depth=2, window=W15))
    assert len(r["chains"]) == 2 and r["chains"][0].outcome == "won", r["stats"]
    assert r["chains"][0].rungs[1]["start_ts"] == W15 and r["chains"][0].settle_ts == 2 * W15
    assert r["stats"]["signals_skipped_in_chain"] == 1, r["stats"]
    # ...and on the 5m grid the same market has no rung-2 window, so it aborts
    c = pmm.run([(0, True)], m15, cfg(max_depth=2))["chains"][0]
    assert c.outcome == "aborted", c

    # Two books on one market. Independent (the default): both trade the
    # contested window, and A's rung 2 runs straight through B's chain.
    m2 = mkt((0, 0.49, 0.50), (1, 0.49, 0.50), (1, 0.49, 0.50))
    A = ([(0, True), (2 * W, True)], cfg(max_depth=2), None)
    B = ([(0, False), (W, True)], cfg(max_depth=1), None)
    ra, rb = pmm.run_books([A, B], m2)
    assert [c.start_ts for c in ra["chains"]] == [0, 2 * W], ra["stats"]
    assert ra["chains"][0].rungs[1]["start_ts"] == W and ra["chains"][0].outcome == "won"
    assert len(rb["chains"]) == 2 and rb["stats"]["signals_yielded"] == 0, rb["stats"]
    # One trade per window: the first book takes the contested window, its rung
    # 2 blocks B at W, and B's yielded signals are counted apart from its own
    # in-chain skips. Capital is the deepest chain, not the sum of book peaks.
    ra, rb = pmm.run_books([A, B], m2, one_per_window=True)
    assert [c.start_ts for c in ra["chains"]] == [0, 2 * W], ra["stats"]
    assert not rb["chains"] and rb["stats"]["signals_yielded"] == 2, rb["stats"]
    assert ra["stats"]["signals_skipped_in_chain"] == 0, ra["stats"]
    A2 = ([(0, True), (W, True)], cfg(max_depth=2), None)
    ra, rb = pmm.run_books([A2, B], m2, one_per_window=True)
    assert ra["stats"]["signals_skipped_in_chain"] == 1 and rb["stats"]["signals_yielded"] == 2
    # ...list order is priority: B first, and B's flat DOWN bet takes window 0
    rb, ra = pmm.run_books([B, A], m2, one_per_window=True)
    assert rb["chains"][0].side_up is False and rb["chains"][0].outcome == "won", rb["stats"]
    assert ra["chains"][0].start_ts == 2 * W and ra["stats"]["signals_yielded"] == 1, ra["stats"]
    # ...and a filtered-out signal does not consume the window for the rest
    A3 = ([(0, True)], cfg(max_depth=1, hours=(12,)), None)
    ra, rb = pmm.run_books([A3, B], m2, one_per_window=True)
    assert ra["stats"]["signals_filtered_out"] == 1 and rb["chains"][0].start_ts == 0
    runs = [{"label": "a", "chains": ra["chains"]}, {"label": "b", "chains": rb["chains"]}]
    assert pmm.combine(runs)["stats"]["peak_chain_exposure"] == \
        pmm.combine(runs, one_per_window=True)["stats"]["peak_chain_exposure"]  # no overlap here
    ra, rb = pmm.run_books([A, B], m2)
    runs = [{"label": "a", "chains": ra["chains"]}, {"label": "b", "chains": rb["chains"]}]
    pa, pb = (max(c.staked for c in r["chains"]) for r in runs)         # 3.0, 1.04 (NO at 51c)
    assert abs(pmm.combine(runs)["stats"]["peak_chain_exposure"] - round(pa + pb, 2)) < 1e-9
    assert abs(pmm.combine(runs, one_per_window=True)["stats"]["peak_chain_exposure"] - pa) < 1e-9
    # run() is the one-book case of run_books()
    assert pmm.run(*A[:1], m2, A[1])["stats"] == pmm.run_books([A], m2)[0]["stats"]

    assert parse_require(["fill>=0.5", "side_up==True", "hour != 15"]) == (
        ("fill", ">=", 0.5), ("side_up", "==", True), ("hour", "!=", 15.0))

    print("all self-checks pass")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Recovery-sized martingale backtest")
    ap.add_argument("--strategy", default="rsi_bb")
    ap.add_argument("--preset", default="PM 5m Volume")
    ap.add_argument("--from", dest="frm", default="2026-03-04")
    ap.add_argument("--to", dest="to", default="2026-09-04")
    ap.add_argument("--depth", type=int, default=None,
                    help="single ladder depth; omit to sweep 1..--max-depth")
    ap.add_argument("--max-depth", type=int, default=8, help="deepest rung in the sweep")
    ap.add_argument("--target", type=float, default=1.0, help="$ a won chain banks")
    ap.add_argument("--fee", type=float, default=0.0,
                    help="fee rate: winnings take (0.02 = 2%%) or, with --fee-model "
                         "taker, Polymarket's crypto taker rate (0.07)")
    ap.add_argument("--fee-model", choices=pmm.FEE_MODELS, default="winnings",
                    help="winnings: fee*(1-p) off a win; taker: fee*p*(1-p) on every buy")
    ap.add_argument("--entry-el", type=int, default=5, help="seconds into the window")
    ap.add_argument("--price", default="exec", choices=["exec", "mid"])
    ap.add_argument("--max-price", type=float, default=0.95)
    ap.add_argument("--max-cost", type=float, default=0.0, help="0 = uncapped rung outlay")
    ap.add_argument("--use-book", action="store_true",
                    help="size each rung against the real resting ladder (pm_l2_book, "
                         "2026-02-13..2026-07-27) instead of assuming top of book is deep")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="5m", choices=sorted(pmm.SERIES),
                    help="candle interval AND Polymarket market length (5m or 15m)")
    ap.add_argument("--hours", default=None,
                    help="only open chains in these UTC hours, e.g. 12-23 or 16-19,20-23")
    ap.add_argument("--require", action="append", default=[],
                    help="entry predicate on the signal's features, repeatable, "
                         "e.g. 'fill>=0.5' or 'hour!=15'")
    ap.add_argument("--skip-after-bust", action="store_true",
                    help="sit out the next signal after a chain busts")
    ap.add_argument("--by", choices=["week", "month"], default=None,
                    help="also break the run down by period, one block per swept depth")
    ap.add_argument("--selftest", action="store_true",
                    help="check the ladder arithmetic on a synthetic book and exit")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()

    hours = None
    if args.hours:
        hours = []
        for part in args.hours.split(","):
            a, _, b = part.partition("-")
            a, b = int(a), int(b or a)
            # inclusive, and wraps midnight: "16-01" means 16,17,…,23,0,1
            hours += [(a + i) % 24 for i in range((b - a) % 24 + 1)]

    window = pmm.SERIES[args.interval]
    require = parse_require(args.require)
    lo, hi = _ts(args.frm), _ts(args.to, end=True)
    sigs, n_candles = signals_for(args.strategy, args.preset, lo, hi,
                                  args.symbol, args.interval)
    market = pmm.load_market(lo, hi, args.entry_el, series=args.interval)

    books = {}
    if args.use_book:
        depth = args.depth or args.max_depth
        cand = {w + k * window for w, *_ in sigs for k in range(depth)}
        books = pmm.load_books(cand, args.entry_el, series=args.interval)
        print(f"ladder loaded for {len(books)}/{len(cand)} candidate windows "
              f"({len(books) / max(len(cand), 1):.0%}); the rest fall back to top of book")

    print(f"{args.strategy} / {args.preset}   {args.frm} .. {args.to}   "
          f"{args.interval} {args.symbol}")
    print(f"{n_candles} candles -> {len(sigs)} signals in range; "
          f"{len(market)} resolved+quoted PM windows; entry at +{args.entry_el}s "
          f"({args.price}), fee {args.fee:.1%} ({args.fee_model}), target ${args.target:g}"
          + (f", require {require}" if require else "") + "\n")
    if not sigs:
        print("no signals."); return 0

    depths = [args.depth] if args.depth else list(range(1, args.max_depth + 1))
    hdr = (f"{'D':>2} {'chains':>7} {'won':>6} {'bust':>5} {'abort':>6} {'chainwin':>9} "
           f"{'bets':>6} {'hit':>7} {'PnL':>9} {'/chain':>8} {'ROI/stake':>10} "
           f"{'peak exp':>9} {'ROI/peak':>9} {'maxDD':>9}")
    print(hdr); print("-" * len(hdr))
    detail = None
    runs = {}
    for d in depths:
        cfg = pmm.MartingaleConfig(
            max_depth=d, target=args.target, fee=args.fee, entry_el=args.entry_el,
            price=args.price, max_price=args.max_price, max_cost=args.max_cost,
            use_book=args.use_book, hours=hours,
            skip_after_bust=args.skip_after_bust, require=require,
            window=window, fee_model=args.fee_model)
        res = pmm.run(sigs, market, cfg, books)
        s = res["stats"]
        if not s.get("chains"):
            print(f"{d:>2}  no chains"); continue
        print(f"{d:>2} {s['chains']:>7} {s['chains_won']:>6} {s['chains_busted']:>5} "
              f"{s['chains_aborted']:>6} {s['chain_win_rate']:>8.2f}% {s['bets']:>6} "
              f"{s['bet_hit_rate']:>6.2f}% {s['total_pnl']:>+9.2f} "
              f"{s['pnl_per_chain']:>+8.4f} {s['roi_on_staked_pct']:>+9.2f}% "
              f"{s['peak_chain_exposure']:>9.2f} {s['roi_on_peak_exposure_pct']:>+8.1f}% "
              f"{s['max_drawdown']:>+9.2f}")
        detail = res
        runs[d] = res["chains"]
    if detail is not None:
        print(f"\nper-rung, at depth {detail['config']['max_depth']} "
              f"(each row conditions on every earlier rung losing):")
        _print_rungs(detail["rungs"])
    if args.by and runs:
        print(f"\nby {args.by}"
              + (f", depth {next(iter(runs))}" if len(runs) == 1 else
                 f", depths {min(runs)}-{max(runs)} side by side") + ":")
        _print_periods(runs, args.by, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
