"""Recovery-sized martingale over the Polymarket 5-minute UP/DOWN markets
(and the 15-minute ones: ``window=900``, ``load_market(series="15m")``).

The idea
--------
A candle strategy (RSI + BB here, but any of them) fires a directional signal at
the close of a 5m bar. That bet is placed in the Polymarket window opening on
that boundary. If it **loses**, the same direction is re-bet in the *immediately
next* window, and again, up to ``max_depth`` rungs. The first win closes the
chain; a loss on the last rung abandons it and realises the accumulated cost.

Why the next window and not a bigger bet later: each 5m window is a *separate*
market that re-opens near 50/50, so a martingale here does not have to buy an
ever-worsening price the way a losing position in a single market would. The
adverse selection is subtler — see the per-rung table the CLI prints.

Recovery sizing (not doubling)
------------------------------
Doubling is blind: it ignores what a share actually pays. Here each rung solves
for the share count that makes a win repay everything already lost *plus* the
chain's profit target::

    breakeven(p) = p + fee * (1 - p)          # the hit rate a fill at p needs
    profit per share on a win = 1 - breakeven(p) = (1 - p) * (1 - fee)
    shares_k = (prior_loss + target) / (1 - breakeven(p_k))
    cost_k   = shares_k * p_k

``prior_loss`` is the cash actually sunk in rungs 1..k-1 (all losers, so all of
it is gone). By construction **every** chain that wins at any rung banks exactly
``target``, so depth only changes how often a chain closes green and how much a
red one costs. That is the whole trade-off this module measures.

Because rung 1 has ``prior_loss = 0``, ``shares_1 = target / (1 - breakeven(p))``
— i.e. the base stake floats with the price so the target is constant, rather
than the stake being constant and the profit floating.

Pricing and settlement
----------------------
Entries are the **real** book, not a flat 0.5: buy YES at the ask for an UP bet,
buy NO at ``1 - bid`` for a DOWN bet, read at a fixed ``entry_el`` seconds into
the window (no hindsight — the same offset every time). Outcomes are the market's
own resolution (Chainlink-settled), never the Binance candle's direction, which
is only an ~85% proxy at the strike.

Reads pm_l2_quote / pm_l2_market and pm_quote / pm_window from the market DB.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone

from . import db

WINDOW = 300  # Polymarket BTC up/down window length, seconds (5m series)

# series -> window length in seconds. The 15m market reads its own tables
# because every key is the window start_ts alone (see db.py / `load_market`).
SERIES = {"5m": 300, "15m": 900}
FEE_MODELS = ("winnings", "taker")


def breakeven(p: float, fee: float, model: str = "winnings") -> float:
    """Hit rate a fill at price ``p`` needs to break even, i.e. cash per share.

    ``winnings``: a flat take on a winning share's profit, so a win nets
    ``(1 - p) * (1 - fee)`` and the effective cost is ``p + fee * (1 - p)``.

    ``taker``: Polymarket's crypto-market taker fee, charged on the BUY as
    ``shares * fee * p * (1 - p)`` (documented rate 0.07; makers pay nothing and
    a hold-to-settle redemption is free). It peaks at 50c -- 1.75c a share --
    which on these markets is most of the edge. Cash per share is
    ``p + fee * p * (1 - p)``.

    Either way profit per share on a win is ``1 - breakeven(p)``.
    """
    if model == "taker":
        return p + fee * p * (1.0 - p)
    return p + fee * (1.0 - p)


# Entry predicates. A filter is ``(key, op, value)`` evaluated against the
# opening signal's feature dict, so a new filter is data rather than code — the
# page's PM_FILTERS and the CLI's --require both build these.
_OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
}


def match_features(feats: dict, require) -> bool:
    """True when every ``(key, op, value)`` predicate holds.

    A predicate whose key the signal does not carry FAILS rather than passing,
    so a filter can never silently do nothing because a strategy stopped
    emitting the field it names.
    """
    for key, op, val in require or ():
        if key not in feats:
            return False
        try:
            if not _OPS[op](feats[key], val):
                return False
        except TypeError:            # e.g. comparing a string to a number
            return False
    return True


@dataclass
class MartingaleConfig:
    max_depth: int = 1            # rungs per chain; 1 == no martingale at all
                                  # 1 is the fitted answer on 10 of the 11
                                  # strategies measured -- see the README
    target: float = 1.0           # $ a completed chain banks, at any depth
    fee: float = 0.0              # platform take on winnings (0.02 = 2%)
    entry_el: int = 5             # seconds into the window we lift the book
    price: str = "exec"           # 'exec' (ask / 1-bid) | 'mid'
    max_price: float = 0.95       # never buy above this; abandon the chain instead
    max_cost: float = 0.0         # 0 = uncapped, else abandon above this rung outlay
    use_book: bool = False        # size against the real ladder, not top of book
    hours: "tuple | None" = None  # only open chains in these UTC hours (None = all)
    skip_after_bust: bool = False # sit out the next signal after a chain busts
    require: tuple = ()           # extra entry predicates; see `match_features`
    window: int = WINDOW          # market length in seconds: 300 (5m) or 900 (15m)
    fee_model: str = "winnings"   # 'winnings' | 'taker' -- see `breakeven`

    def validate(self) -> "MartingaleConfig":
        if self.max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        if self.price not in ("exec", "mid"):
            raise ValueError("price must be 'exec' or 'mid'")
        if not 0.0 <= self.fee < 1.0:
            raise ValueError("fee must be in [0, 1)")
        if self.fee_model not in FEE_MODELS:
            raise ValueError(f"fee_model must be one of {FEE_MODELS}")
        if self.window <= 0:
            raise ValueError("window must be > 0 seconds")
        if self.target <= 0:
            raise ValueError("target must be > 0")
        if self.hours is not None:
            self.hours = tuple(sorted({int(h) for h in self.hours}))
            if not self.hours or not all(0 <= h < 24 for h in self.hours):
                raise ValueError("hours must be UTC hours in [0, 24)")
        self.require = tuple(tuple(r) for r in (self.require or ()))
        for r in self.require:
            if len(r) != 3 or r[1] not in _OPS:
                raise ValueError(f"bad predicate {r!r}; want (key, op, value) "
                                 f"with op in {sorted(_OPS)}")
        return self


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------

def load_market(start_ts: int, end_ts: int, entry_el: int = 5, *, conn=None,
                series: str = "5m") -> dict:
    """``{start_ts: {'up', 'bid', 'ask', 'src'}}`` for resolved, quoted windows.

    5m: two overlapping captures are merged. ``pm_l2_*`` (PMData, from
    2026-02-13) carries real book depth; ``pm_window``/``pm_quote`` (this
    machine's pmqb capture) runs later and to the present. Where both resolved a
    window the exchange feed's own outcome wins, then our Chainlink capture, then
    the L2 terminal-book derivation — most authoritative last, so it overwrites.

    15m: ``pm_l2_*_15m`` (PMData btc-15m, where loaded) and pmqb's standalone 15m
    capture, ``pm_window_15m``/``pm_quote_15m`` (2s book snapshots from
    2026-07-03, outcomes are Polymarket's own via Gamma, which wins). The capture
    samples every ~2s, so its quote must be no older than 15s at entry: a capture
    gap would otherwise hand a pre-window price to the fill.
    """
    if series not in SERIES:
        raise ValueError(f"unknown series {series!r}; use one of {sorted(SERIES)}")
    own = conn is None
    if own:
        conn = db.connect(readonly=True)
    try:
        out: dict = {}
        if series == "15m":
            outcomes = (
                ("SELECT start_ts, resolved_up FROM pm_l2_market_15m WHERE resolved_up IS NOT NULL "
                 "AND start_ts BETWEEN ? AND ?", "l2"),
                ("SELECT start_ts, resolved_up FROM pm_window_15m WHERE resolved_up IS NOT NULL "
                 "AND start_ts BETWEEN ? AND ?", "gamma"),
            )
            quotes = (
                ("SELECT start_ts, MAX(time) t, bid, ask, bid_sz, ask_sz FROM pm_l2_quote_15m "
                 "WHERE start_ts BETWEEN ? AND ? AND time - start_ts <= ? GROUP BY start_ts",
                 ("bid_sz", "ask_sz")),
                ("SELECT start_ts, MAX(time) t, yes_bid bid, yes_ask ask, bid_sz, ask_sz, "
                 "bid_depth, ask_depth FROM pm_quote_15m "
                 "WHERE start_ts BETWEEN ? AND ? AND time - start_ts <= ? "
                 "AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL GROUP BY start_ts",
                 ("bid_sz", "ask_sz", "bid_depth", "ask_depth")),
            )
        else:
            outcomes = (
                ("SELECT start_ts, resolved_up FROM pm_l2_market WHERE resolved_up IS NOT NULL "
                 "AND resolved_src='terminal' AND start_ts BETWEEN ? AND ?", "l2_terminal"),
                ("SELECT start_ts, resolved_up FROM pm_window WHERE resolved_up IS NOT NULL "
                 "AND start_ts BETWEEN ? AND ?", "chainlink"),
                ("SELECT start_ts, resolved_up FROM pm_l2_market WHERE resolved_up IS NOT NULL "
                 "AND resolved_src='feed' AND start_ts BETWEEN ? AND ?", "feed"),
            )
            quotes = (
                ("SELECT start_ts, MAX(time) t, bid, ask, bid_sz, ask_sz FROM pm_l2_quote "
                 "WHERE start_ts BETWEEN ? AND ? AND time - start_ts <= ? GROUP BY start_ts",
                 ("bid_sz", "ask_sz")),
                ("SELECT start_ts, MAX(time) t, yes_bid bid, yes_ask ask FROM pm_quote "
                 "WHERE start_ts BETWEEN ? AND ? AND time - start_ts <= ? "
                 "AND yes_bid IS NOT NULL GROUP BY start_ts", ()),
            )
        for sql, tag in outcomes:
            for r in conn.execute(sql, (start_ts, end_ts)):
                out[r["start_ts"]] = {"up": int(r["resolved_up"]), "src": tag}

        # Top of book at/just before entry_el seconds in. L2 first; pm_quote only
        # fills windows L2 never saw, so the depth-carrying source stays primary.
        # Only some sources carry resting SIZE, so `bid_sz`/`ask_sz` can be absent
        # -- see `run` for what that means.
        quoted: dict = {}
        for sql, extra in quotes:
            for r in conn.execute(sql, (start_ts, end_ts, entry_el)):
                if series == "15m" and r["t"] < r["start_ts"] + entry_el - 15:
                    continue                  # stale: the capture was down at entry
                q = {"bid": r["bid"], "ask": r["ask"]}
                for k in extra:
                    q[k] = r[k]
                quoted.setdefault(r["start_ts"], q)

        market = {}
        for ts, w in out.items():
            q = quoted.get(ts)
            if q is None:
                continue
            market[ts] = {**w, **q}
        return market
    finally:
        if own:
            conn.close()


def entry_cost(win: dict, side_up: bool, price: str = "exec") -> "float | None":
    """Executable cost per share of the chosen side, or None if unquotable.

    An UP bet buys YES at the ask; a DOWN bet buys NO, which on a binary market
    costs ``1 - yes_bid``.
    """
    bid, ask = win.get("bid"), win.get("ask")
    if price == "mid":
        if bid is None or ask is None:
            return None
        mid = (bid + ask) / 2.0
        c = mid if side_up else 1.0 - mid
    else:
        c = ask if side_up else (None if bid is None else 1.0 - bid)
    if c is None or not 0.0 < c < 1.0:
        return None
    return c


def book_lean(win: "dict | None", side_up: bool) -> "float | None":
    """Top-of-book size imbalance ORIENTED to the side being bought, in [-1, 1].

    ``bid_sz``/``ask_sz`` are resting size on YES, so the raw imbalance means the
    opposite thing depending on which side the bet is: for an UP bet the YES bid
    is the crowd on our side, for a DOWN bet (buying NO) it is the crowd against
    us. Multiplying by the side makes +1 "the book leans the way we are betting"
    for both, which is the form the filter was fitted on -- scanned raw, the same
    effect points opposite ways on two presets of one strategy and reads as noise.

    ``None`` when the window has no size (pm_quote-only windows, and everything
    after the pm_l2_quote capture ends), so a predicate on it filters those
    chains out rather than silently keeping them.
    """
    if not win:
        return None
    bs, as_ = win.get("bid_sz"), win.get("ask_sz")
    if bs is None or as_ is None:
        return None
    tot = bs + as_
    if tot <= 0:                 # quoted but nothing resting: no lean either way
        return 0.0
    return ((bs - as_) / tot) * (1.0 if side_up else -1.0)


def push_pct(candles, index: int, side_up: bool, bars: int = 3) -> "float | None":
    """The ``bars``-bar move INTO the trade, as a %, ORIENTED to the bet.

    Signed so that a POSITIVE value always means "price ran the way that created
    the signal": for a DOWN bet that is a rally, for an UP bet a sell-off. On the
    fade strategies here that is how violently price reached the extreme being
    faded, and it is the fitted second filter on Oscillators (``push3 >= 0.3``).

    The orientation is the whole point. Scanned RAW as a 3-bar return it is a
    side selector, not a feature -- on a banded strategy every UP bet follows a
    fall and every DOWN bet a rise, so ``ret3 >= 0.26`` keeps 100% DOWN bets
    (corr with side -0.74) and reads as an edge that is really just one side.
    Oriented, both sides agree, which is what makes it a filter.

    ``None`` when the bar has no ``bars``-bar history, so a predicate on it
    filters those signals out rather than silently keeping them.
    """
    if index < bars or index >= len(candles):
        return None
    prev = candles[index - bars]["close"]
    if not prev:
        return None
    ret = (candles[index]["close"] / prev - 1.0) * 100.0
    return -ret if side_up else ret


def load_books(start_tss, entry_el: int = 5, *, conn=None, series: str = "5m") -> dict:
    """``{start_ts: (up_prices, up_sizes, down_prices, down_sizes)}`` at ``entry_el``.

    The resting ladder is what a *growing* stake actually has to eat, and a
    martingale's whole problem is that the stake grows. ``up_*`` is the YES ask
    ladder (buy YES); ``down_*`` is the YES bid ladder mirrored to NO prices
    (buying NO at ``q`` is selling YES at ``1 - q``), both ordered cheapest first.

    Only ``pm_l2_book`` (``pm_l2_book_15m`` for the 15m series) carries depth, so
    windows outside its span come back missing and the caller falls back to
    top-of-book.
    """
    import numpy as np
    from . import pm_store

    own = conn is None
    if own:
        conn = db.connect(readonly=True)
    try:
        table = "pm_l2_book_15m" if series == "15m" else "pm_l2_book"
        sql = (f"SELECT ladder FROM {table} WHERE start_ts=? AND time<=? "
               "ORDER BY time DESC LIMIT 1")
        out = {}
        tick, scale = pm_store.TICK, pm_store.SIZE_SCALE
        for ts in sorted(set(int(t) for t in start_tss)):
            r = conn.execute(sql, (ts, ts + entry_el)).fetchone()
            if not r:
                continue
            lad = pm_store.decode_ladder(r["ladder"])
            ai = np.nonzero(lad[tick:])[0]                  # asks, cheapest first
            bi = np.nonzero(lad[:tick])[0][::-1]            # bids, best first
            out[ts] = (ai / tick, lad[tick:][ai] / scale,
                       1.0 - bi / tick, lad[:tick][bi] / scale)
        return out
    finally:
        if own:
            conn.close()


def book_fill(entry, side_up: bool, shares: float):
    """``(avg_price, filled)`` for a market order walking the resting ladder."""
    prices, sizes = (entry[0], entry[1]) if side_up else (entry[2], entry[3])
    want, cost, got = float(shares), 0.0, 0.0
    for px, sz in zip(prices, sizes):
        if want <= 0:
            break
        take = want if want < sz else sz
        cost += take * px
        got += take
        want -= take
    return (cost / got if got else None), got


def _size_rung(p_top: float, prior_loss: float, cfg: "MartingaleConfig",
               entry, side_up: bool):
    """Solve shares and the realised average fill price for one rung.

    Top of book is only the *first* share's price. Size and price are mutually
    dependent — a bigger stake eats deeper into the ladder, which raises the
    price, which demands more shares to recover the same loss — so this iterates
    to the fixed point instead of pretending the top of book is infinitely deep.
    Returns ``(shares, avg_price)``, or ``None`` if the book cannot fill it.
    """
    p = p_top
    for _ in range(12):
        edge = 1.0 - breakeven(p, cfg.fee, cfg.fee_model)
        if edge <= 0.0:
            return None
        shares = (prior_loss + cfg.target) / edge
        if entry is None:                    # no ladder for this window
            return shares, p
        avg, filled = book_fill(entry, side_up, shares)
        if avg is None or filled < shares * 0.9999:
            return None                      # not enough resting size
        if abs(avg - p) < 1e-6:
            # Re-solve at the realised price so the "a win banks exactly target"
            # identity stays exact rather than off by the convergence tolerance.
            # (A taker fee is charged per level; pricing it at the average fill
            # overstates it slightly, because p * (1 - p) is concave.)
            return ((prior_loss + cfg.target)
                    / (1.0 - breakeven(avg, cfg.fee, cfg.fee_model))), avg
        p = avg
    return None                              # did not converge: treat as unfillable


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------

@dataclass
class Chain:
    """One signal played to its conclusion: won at some rung, busted, or aborted."""
    start_ts: int
    side_up: bool
    rungs: list = field(default_factory=list)   # per-rung dicts
    outcome: str = "aborted"   # 'won' | 'busted' | 'aborted' (ran out of data)
    pnl: float = 0.0           # net $ over the whole chain
    staked: float = 0.0        # total cash put up across its rungs == capital needed
    window: int = WINDOW       # market length, so a chain knows when it settles

    @property
    def settle_ts(self) -> int:
        return (self.rungs[-1]["start_ts"] if self.rungs else self.start_ts) + self.window


def _prep_signals(signals, market: dict, cfg: "MartingaleConfig") -> dict:
    """``{window_ts: (side_up, features)}`` with the engine's own features added.

    ``hour``, ``weekday``, ``side_up``, ``top_imb_sd`` (see ``book_lean``) and
    ``fill`` (rung 1's executable cost) are filled in here so every strategy has
    them. The first signal a book fires on a window wins; later ones are dropped.
    """
    by_window: dict = {}
    for sig in signals:
        ts, up = int(sig[0]), bool(sig[1])
        feats = dict(sig[2]) if len(sig) > 2 and sig[2] else {}
        d = datetime.fromtimestamp(ts, timezone.utc)
        feats.setdefault("hour", d.hour)
        feats.setdefault("weekday", d.weekday())
        feats.setdefault("side_up", up)
        lean = book_lean(market.get(ts), up)
        if lean is not None:
            feats.setdefault("top_imb_sd", lean)
        # What rung 1 would actually pay. At depth 1 this IS the breakeven hit
        # rate, so a predicate on it asks whether the signal beats the price the
        # book is quoting it -- not merely whether it wins.
        cost = entry_cost(market.get(ts) or {}, up, cfg.price)
        if cost is not None:
            feats.setdefault("fill", cost)
        by_window.setdefault(ts, (up, feats))
    return by_window


def _play_chain(ts: int, side_up: bool, market: dict, cfg: "MartingaleConfig",
                books: dict) -> Chain:
    """Open a chain on window ``ts`` and play it rung by rung to its conclusion."""
    ch = Chain(start_ts=ts, side_up=side_up, window=cfg.window)
    prior_loss = 0.0
    w_ts = ts
    for depth in range(1, cfg.max_depth + 1):
        win = market.get(w_ts)
        if win is None:                      # unresolved / unquoted window
            break                            # -> 'aborted'
        p = entry_cost(win, ch.side_up, cfg.price)
        if p is None or p > cfg.max_price:
            break
        sized = _size_rung(p, prior_loss, cfg,
                           books.get(w_ts) if cfg.use_book else None, ch.side_up)
        if sized is None:                    # book too thin to fill this rung
            break
        shares, p = sized
        if p > cfg.max_price:                 # the ladder walked us too high
            break
        be = breakeven(p, cfg.fee, cfg.fee_model)
        edge = 1.0 - be                      # profit per share on a win
        # Cash that leaves the account: a taker fee is paid on the buy, so a
        # losing rung loses it too; a winnings fee is only ever taken on a win.
        cost = shares * (be if cfg.fee_model == "taker" else p)
        if cfg.max_cost and cost > cfg.max_cost:
            break
        won = ch.side_up == bool(win["up"])
        ch.rungs.append({
            "depth": depth, "start_ts": w_ts, "price": round(p, 6),
            "shares": round(shares, 4), "cost": round(cost, 4),
            "won": won, "resolved_up": win["up"], "src": win["src"],
        })
        ch.staked += cost
        if won:
            ch.outcome = "won"
            ch.pnl = shares * edge - prior_loss     # == cfg.target
            break
        prior_loss += cost
        w_ts += cfg.window
    else:
        ch.outcome = "busted"
    if ch.outcome != "won":
        ch.pnl = -prior_loss                 # busted or aborted: it is all gone
    return ch


def run_books(books: list, market: dict, one_per_window: bool = False) -> list:
    """Play several books over one market; one ``run``-shaped result per book.

    Each entry of ``books`` is ``(signals, cfg, ladder)`` -- what ``run`` takes.
    The books keep their own depth, target and filters either way; the switch
    is what happens when two of them want the same window.

    ``one_per_window=False`` (the default): the books are independent
    positions. Each has its own "one chain at a time" rule and nothing else,
    so two books that fire on the same window both trade it, and one book's
    rungs run straight through another's chain. That is a portfolio of
    separate accounts, and it stacks correlated signals.

    ``one_per_window=True``: the books share ONE account, which holds at most
    one position in any window. A signal that lands on a window some book is
    already in -- as a fresh chain or as a later rung -- is not traded. When
    several books fire on the same free window the first in ``books`` takes it,
    so the list order is a priority order; a book that disagrees on the side
    simply loses the window rather than cancelling it. Filters run first, so a
    signal a book filters out does not consume the window for the rest.

    Per-book stats separate the two reasons a signal was not traded:
    ``signals_skipped_in_chain`` is the book's OWN chain still running, and
    ``signals_yielded`` is another book holding the window.
    """
    preps = []
    for signals, cfg, ladder in books:
        cfg = (cfg or MartingaleConfig()).validate()
        preps.append((_prep_signals(signals, market, cfg), cfg, ladder or {}))
    n = len(preps)
    chains: list = [[] for _ in range(n)]
    skipped, filtered, yielded = [0] * n, [0] * n, [0] * n
    last_busted = [False] * n     # for skip_after_bust; path-dependent, hence in here
    # No new chain may open before this window. One clock per book, or -- with
    # one_per_window -- one clock for all of them, plus who set it.
    block_until = [-1] * n
    shared_until, holder = -1, -1

    windows = sorted(set().union(*(p[0] for p in preps)))
    for ts in windows:
        for i, (by_window, cfg, ladder) in enumerate(preps):
            if ts not in by_window:
                continue
            if one_per_window:
                if ts < shared_until:
                    if holder == i:
                        skipped[i] += 1
                    else:
                        yielded[i] += 1
                    continue
            elif ts < block_until[i]:
                skipped[i] += 1
                continue
            side_up, feats = by_window[ts]
            if cfg.hours is not None and feats["hour"] not in cfg.hours:
                filtered[i] += 1
                continue
            if cfg.require and not match_features(feats, cfg.require):
                filtered[i] += 1
                continue
            if cfg.skip_after_bust and last_busted[i]:
                filtered[i] += 1
                last_busted[i] = False    # sit out exactly one signal, then resume
                continue
            ch = _play_chain(ts, side_up, market, cfg, ladder)
            chains[i].append(ch)
            if ch.rungs:
                last_busted[i] = ch.outcome != "won"
            last = ch.rungs[-1]["start_ts"] if ch.rungs else ts
            if one_per_window:
                shared_until, holder = last + cfg.window, i
            else:
                block_until[i] = last + cfg.window

    return [{"config": asdict(cfg), "chains": chains[i],
             "stats": _summarize(chains[i], skipped[i], filtered[i], yielded[i]),
             "rungs": _rung_table(chains[i]),
             "equity": _equity(chains[i])}
            for i, (_, cfg, _) in enumerate(preps)]


def run(signals, market: dict, cfg: "MartingaleConfig | None" = None,
        books: "dict | None" = None) -> dict:
    """Play every signal as a recovery-sized chain. Returns chains/stats/equity.

    ``signals`` is an iterable of ``(window_start_ts, side_up)`` or
    ``(window_start_ts, side_up, features)``; the caller maps a strategy's
    bar-close signal to the window it bets on (``sig.time + 300``, because a
    signal at a bar's close bets on the *next* bar / window). ``features`` is a
    dict of entry-time values ``cfg.require`` predicates are tested against —
    the strategy's own signal meta, plus whatever the caller adds. ``hour``,
    ``weekday``, ``side_up``, ``top_imb_sd`` (see ``book_lean``) and ``fill``
    (rung 1's executable cost) are filled in here so every strategy has them.

    One chain at a time: a signal that fires while a chain is still running is
    skipped (counted in ``signals_skipped_in_chain``), because the rungs are
    sized against a single running loss and overlapping chains would not share
    that arithmetic.

    One book; ``run_books`` plays several over the same market.
    """
    return run_books([(signals, cfg, books)], market)[0]


def signals_for(strategy_id: str, preset: str, start_ts: int, end_ts: int,
                symbol: str = "BTCUSDT", interval: str = "5m", warmup: int = 400):
    """``[(window_start_ts, side_up)]`` the strategy would bet inside [start, end].

    A signal fires at a bar's CLOSE and bets on the next bar, so the window it
    trades opens one interval later. Candles are read from ``warmup`` bars before
    the range so the indicators are warm at the first tradeable bar.

    ``push3`` is added to every signal's features here, because it needs the
    candles and ``run`` does not have them. See ``push_pct``.

    Imported lazily: this module is a pure engine and the CLI paths that only
    replay stored chains should not pull in the whole strategy package.
    """
    from . import registry, store, strategies  # noqa: F401 (registers strategies)

    strat = registry.get(strategy_id)
    presets = strat.presets()
    if preset not in presets:
        raise ValueError(f"unknown preset {preset!r} for {strategy_id}; "
                         f"have: {list(presets)}")
    secs = store.INTERVAL_SECONDS[interval]
    candles = store.get_candles(symbol, interval,
                                (start_ts - warmup * secs) * 1000, end_ts * 1000)
    out = []
    for s in strat.generate_signals(candles, presets[preset]):
        w = s.time + secs
        if start_ts <= w <= end_ts:
            feats = dict(s.meta or {})
            p3 = push_pct(candles, s.index, s.side == "long", 3)
            if p3 is not None:
                feats.setdefault("push3", p3)
            out.append((w, s.side == "long", feats))
    return out, len(candles)


def merge_signals(per_strategy: dict) -> tuple:
    """Union several strategies' signals; drop windows where they disagree.

    ``per_strategy`` maps a label to its ``[(window, side_up)]`` list. A window
    both a long and a short vote lands on is discarded rather than arbitrated —
    the same rule the Combined strategy uses — and counted as a conflict.
    """
    votes: dict = {}
    for sigs in per_strategy.values():
        for sig in sigs:
            ts, up = int(sig[0]), bool(sig[1])
            feats = dict(sig[2]) if len(sig) > 2 and sig[2] else {}
            slot = votes.setdefault(ts, [set(), {}])
            slot[0].add(up)
            slot[1].update(feats)      # later strategies win on a key clash
    merged = [(ts, next(iter(v[0])), v[1])
              for ts, v in sorted(votes.items()) if len(v[0]) == 1]
    return merged, sum(1 for v in votes.values() if len(v[0]) > 1)


def combine(runs: list, one_per_window: bool = False) -> dict:
    """Aggregate several per-strategy runs into one portfolio.

    Each strategy is played on its OWN ladder depth and its OWN entry filters —
    the fitted answer differs per strategy, so forcing one setting on all of them
    would misrepresent every one but the strategy it was fitted to. That makes
    the runs genuinely separate books, so:

      * chains from different strategies MAY overlap in time (they are separate
        positions), which is why capital adds rather than shares;
      * ``peak_chain_exposure`` is the SUM of each book's own peak — the worst
        case where every book is deepest at once — not the max;
      * drawdown is recomputed on the merged, time-ordered equity curve, because
        two books' bad weeks need not coincide.

    ``one_per_window`` says the runs came from ``run_books(one_per_window=True)``:
    the books shared one account and their chains never overlap, so the capital
    is the deepest single chain, exactly as it is for one book.

    ``runs`` is ``[{"label", "chains", ...}]``; the extra keys are passed through.
    """
    played = [(r["label"], c) for r in runs for c in r["chains"] if c.rungs]
    if not played:
        return {"stats": {"chains": 0}, "equity": [], "periods": []}
    played.sort(key=lambda lc: lc[1].settle_ts)

    total = sum(c.pnl for _, c in played)
    staked = sum(c.staked for _, c in played)
    bets = sum(len(c.rungs) for _, c in played)
    wins = sum(1 for _, c in played for r in c.rungs if r["won"])
    cum = peak = mdd = 0.0
    # One point per settle SECOND, not per chain: separate books settle chains in
    # the same 5-minute window, and a charting library needs strictly ascending
    # unique timestamps — duplicates make it reject the whole series.
    curve: dict = {}
    for _, c in played:
        cum += c.pnl
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
        curve[c.settle_ts] = round(cum, 4)
    equity = [{"time": t, "value": v} for t, v in sorted(curve.items())]
    book_peaks = [max((c.staked for c in r["chains"] if c.rungs), default=0.0)
                  for r in runs]
    peak_cap = max(book_peaks) if one_per_window else sum(book_peaks)
    stats = {
        "chains": len(played),
        "chains_won": sum(1 for _, c in played if c.outcome == "won"),
        "chains_busted": sum(1 for _, c in played if c.outcome == "busted"),
        "chains_aborted": sum(1 for _, c in played if c.outcome == "aborted"),
        "chain_win_rate": round(100.0 * sum(1 for _, c in played
                                            if c.outcome == "won") / len(played), 2),
        "bets": bets, "bet_hit_rate": round(100.0 * wins / bets, 2) if bets else 0.0,
        "avg_rungs": round(bets / len(played), 3),
        "total_pnl": round(total, 2),
        "pnl_per_chain": round(total / len(played), 4),
        "total_staked": round(staked, 2),
        "roi_on_staked_pct": round(100.0 * total / staked, 3) if staked else 0.0,
        "peak_chain_exposure": round(peak_cap, 2),
        "roi_on_peak_exposure_pct": round(100.0 * total / peak_cap, 2) if peak_cap else 0.0,
        "worst_chain_pnl": round(min(c.pnl for _, c in played), 2),
        "max_drawdown": round(mdd, 2),
        "signals_skipped_in_chain": sum(r.get("skipped", 0) for r in runs),
        "signals_filtered_out": sum(r.get("filtered", 0) for r in runs),
        "signals_yielded": sum(r.get("yielded", 0) for r in runs),
    }
    return {"stats": stats, "equity": equity,
            "chains": [c for _, c in played],
            "labels": [lab for lab, _ in played]}


def period_table(chains: list, by: str = "week") -> list:
    """Per-week / per-month rows: chains, busts, P&L, and the running equity.

    A martingale's total hides its shape — most periods bank a steady trickle of
    ``target`` and one bust wipes out several of them — so ``busts`` belongs
    beside ``pnl``: a period with no bust cannot lose.
    """
    played = [c for c in chains if c.rungs]
    if not played:
        return []
    buckets: dict = {}
    for c in played:
        d = datetime.fromtimestamp(c.start_ts, timezone.utc)
        k = d.strftime("%Y-%m") if by == "month" else \
            (d.date() - timedelta(days=d.weekday())).isoformat()
        buckets.setdefault(k, []).append(c)
    rows, cum, peak = [], 0.0, 0.0
    for k in sorted(buckets):
        cs = buckets[k]
        bets = sum(len(c.rungs) for c in cs)
        pnl = sum(c.pnl for c in cs)
        cum += pnl
        peak = max(peak, cum)
        rows.append({
            "period": k, "start_ts": min(c.start_ts for c in cs),
            "chains": len(cs),
            "won": sum(1 for c in cs if c.outcome == "won"),
            "busts": sum(1 for c in cs if c.outcome != "won"),
            "bets": bets,
            "hit_rate": round(100.0 * sum(1 for c in cs for r in c.rungs
                                          if r["won"]) / bets, 2) if bets else 0.0,
            "staked": round(sum(c.staked for c in cs), 2),
            "pnl": round(pnl, 4), "cum": round(cum, 4),
            "drawdown": round(cum - peak, 4),
            "worst_chain": round(min(c.pnl for c in cs), 4),
        })
    return rows


def _summarize(chains: list, skipped: int, filtered: int = 0,
               yielded: int = 0) -> dict:
    """Headline numbers. Chains that ran out of data mid-ladder are counted as
    realised losses (the conservative reading) *and* reported separately, since
    a gap in the capture is a data outcome rather than a strategy outcome.

    ``yielded`` is only non-zero under ``run_books(one_per_window=True)``: the
    signals another book was already trading."""
    played = [c for c in chains if c.rungs]
    n = len(played)
    if n == 0:
        return {"chains": 0, "signals_skipped_in_chain": skipped,
                "signals_filtered_out": filtered, "signals_yielded": yielded}
    won = [c for c in played if c.outcome == "won"]
    bust = [c for c in played if c.outcome == "busted"]
    abort = [c for c in played if c.outcome == "aborted"]
    clean = [c for c in played if c.outcome != "aborted"]
    total = sum(c.pnl for c in played)
    staked = sum(c.staked for c in played)
    bets = sum(len(c.rungs) for c in played)
    bet_wins = sum(1 for c in played for r in c.rungs if r["won"])
    # The capital the scheme actually needs: rungs settle one at a time and a
    # loss is not returned, so a chain's cumulative outlay IS its peak exposure.
    peak_chain = max(c.staked for c in played)
    cum = peak = mdd = 0.0
    for c in played:
        cum += c.pnl
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {
        "chains": n,
        "chains_won": len(won), "chains_busted": len(bust), "chains_aborted": len(abort),
        "chain_win_rate": round(100.0 * len(won) / n, 2),
        "bets": bets, "bet_hit_rate": round(100.0 * bet_wins / bets, 2) if bets else 0.0,
        "avg_rungs": round(bets / n, 3),
        "total_pnl": round(total, 2),
        "pnl_per_chain": round(total / n, 4),
        "total_pnl_ex_aborted": round(sum(c.pnl for c in clean), 2),
        "chains_ex_aborted": len(clean),
        "total_staked": round(staked, 2),
        "roi_on_staked_pct": round(100.0 * total / staked, 3) if staked else 0.0,
        "peak_chain_exposure": round(peak_chain, 2),
        "roi_on_peak_exposure_pct": round(100.0 * total / peak_chain, 2) if peak_chain else 0.0,
        "worst_chain_pnl": round(min(c.pnl for c in played), 2),
        "max_drawdown": round(mdd, 2),
        "signals_skipped_in_chain": skipped,
        "signals_filtered_out": filtered,
        "signals_yielded": yielded,
    }


def _rung_table(chains: list) -> list:
    """Per-depth hit rate and mean price, conditional on every earlier rung losing."""
    by_depth: dict = {}
    for c in chains:
        for r in c.rungs:
            d = by_depth.setdefault(r["depth"], {"n": 0, "won": 0, "px": 0.0, "cost": 0.0})
            d["n"] += 1
            d["won"] += r["won"]
            d["px"] += r["price"]
            d["cost"] += r["cost"]
    return [{"depth": k, "bets": v["n"], "wins": v["won"],
             "hit_rate": round(100.0 * v["won"] / v["n"], 2),
             "avg_price": round(v["px"] / v["n"], 4),
             "avg_cost": round(v["cost"] / v["n"], 3)}
            for k, v in sorted(by_depth.items())]


def _equity(chains: list) -> list:
    curve, cum = [], 0.0
    for c in chains:
        if not c.rungs:
            continue
        cum += c.pnl
        curve.append({"time": c.settle_ts, "value": round(cum, 4)})
    return curve
