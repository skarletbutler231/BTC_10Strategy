"""CHoCH — Change of Character: the first structure break against the trend.

Idea
----
Smart-Money-Concepts vocabulary for one idea: a trend is a sequence of swing
points, and you can name the exact bar where that sequence stops behaving like a
trend. Two events come out of the same break, and which one it is depends
entirely on what the structure was doing beforehand:

    bias was BEARISH, price breaks the last swing HIGH  ->  CHoCH  (bullish)
    bias was BULLISH, price breaks the last swing HIGH  ->  BOS    (bullish)
    bias was BULLISH, price breaks the last swing LOW   ->  CHoCH  (bearish)
    bias was BEARISH, price breaks the last swing LOW   ->  BOS    (bearish)

**CHoCH** (Change of Character) is the *first* break against the prevailing
structure — the trend's first failure, and the signal SMC treats as a warning
that it may be over. **BOS** (Break of Structure) is a break in the same
direction as the existing bias — a continuation. Same code path, same levels;
only the prior state separates them.

That state is the whole point, and it is what this strategy adds over the
structure detector in reversal.py. That one compares the last two swing lows
against the latest swing high on each bar and calls the result a "BOS" — but by
the definitions above it only ever fires on a downtrend broken upward, which is
a **CHoCH**. It is a good detector with the wrong name and no memory of bias.
Here bias is carried explicitly, so CHoCH and BOS can be told apart, traded
separately, and compared.

How structure is tracked (causally)
------------------------------------
Swing points are fractal pivots, admitted only once the scan reaches bar
``j + pivot_right`` — nothing is read before it could be known. The two live
reference levels are simply the most recent confirmed pivot high and pivot low.
A level is **consumed** when it breaks, so one level fires once; it re-arms only
when a new pivot of that kind confirms. `bias` starts unset, and the first break
merely establishes it — a CHoCH is undefined without a prior character to change.

`break_mode` decides what counts as breaking a level: a **close** beyond it
(strict, later) or any **wick** through it (early, and prone to counting the
stop-run that immediately reverses). `break_buffer_atr` adds a dead-band so a
level grazed by a tick is not a break.

Two filters aimed at the classic failure — the liquidity sweep that looks like a
break and is not:

  * **Displacement** — the breaking bar's body must be at least
    ``min_displacement_atr`` ATRs. A real break tends to arrive with a decisive
    candle; a sweep is usually a long wick and a small body.
  * **Retest entry** — `entry_mode` = *On Retest* waits for price to come back
    to the broken level within ``max_retest_bars`` and enter there ("mitigation"
    in SMC terms), rather than chasing the break bar itself.

`use_htf_filter` runs a second, slower pivot scale and requires the higher-scale
bias to agree with the event (or deliberately to oppose it) — the SMC habit of
trading internal structure only in the direction of swing structure.

`predict_direction` then takes the event at face value (**With Structure**: a
bullish CHoCH -> LONG) or fades it (**Against Structure**).

Parameter groups
----------------
Structure      pivot_left, pivot_right, max_level_age
Event          signal_on (CHoCH | BOS | Both)
Break          break_mode, break_buffer_atr, min_displacement_atr
Entry          entry_mode, retest_tol_atr, max_retest_bars
Higher Scale   use_htf_filter, htf_logic, htf_pivot_left, htf_pivot_right
Decision       predict_direction
Volatility     vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter   (shared)
Window         (shared)

In Polymarket up/down mode the Exit/Backtest params are unused and each signal
is a bet on the next candle's direction.
"""

from __future__ import annotations

from typing import List, Optional

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_EVENTS = ["CHoCH only", "BOS only", "Both"]
_BREAK_MODES = ["Close Beyond", "Wick Beyond"]
_ENTRY_MODES = ["On Break", "On Retest"]
_DIRECTIONS = ["With Structure", "Against Structure"]
_HTF_LOGIC = ["Agree", "Oppose"]


def _bias_track(candles: List[dict], piv_hi: List, piv_lo: List, right: int,
                buffer_px: List[float], use_close: bool):
    """Per-bar structure bias from a pivot set -> list of 'bull'/'bear'/None.

    The same state machine the main scan uses, run standalone so a second,
    slower pivot scale can supply a higher-timeframe bias. Causal: a pivot at
    bar j is admitted only at bar j + right.
    """
    n = len(candles)
    out: List[Optional[str]] = [None] * n
    hi_lv = lo_lv = None          # (price, used)
    bias: Optional[str] = None
    cursor = 0
    for i in range(n):
        while cursor <= i - right and cursor < n:
            if piv_hi[cursor] is not None:
                hi_lv = [piv_hi[cursor], False]
            if piv_lo[cursor] is not None:
                lo_lv = [piv_lo[cursor], False]
            cursor += 1
        c = candles[i]
        ref = c["close"] if use_close else c["high"]
        buf = buffer_px[i] or 0.0
        if hi_lv and not hi_lv[1] and ref > hi_lv[0] + buf:
            hi_lv[1] = True
            bias = "bull"
        ref = c["close"] if use_close else c["low"]
        if lo_lv and not lo_lv[1] and ref < lo_lv[0] - buf:
            lo_lv[1] = True
            bias = "bear"
        out[i] = bias
    return out


class Choch(Strategy):
    id = "choch"
    name = "CHoCH (Change of Character)"
    description = ("Tracks market-structure bias from confirmed swing pivots and "
                   "fires on the bar that breaks a swing level — labelling it a "
                   "CHoCH when it breaks against the prevailing bias and a BOS "
                   "when it breaks with it, so the two can be traded separately.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Structure", [
                Param("pivot_left", "Pivot Left Bars", 8, "int", 1, 200, 1,
                      "Bars before a swing point that it must dominate. Larger "
                      "means only genuinely significant swings define structure."),
                Param("pivot_right", "Pivot Right Bars", 2, "int", 1, 50, 1,
                      "Bars after a swing point that it must dominate — and so "
                      "the delay before that level may be used. Larger is more "
                      "reliable but later."),
                Param("max_level_age", "Max Level Age (bars)", 500, "int", 2, 5000, 5,
                      "A level older than this is stale and no longer tradeable."),
            ]),
            ParamGroup("Event", [
                Param("signal_on", "Signal On", "CHoCH only", "enum", options=_EVENTS,
                      help="CHoCH: only breaks AGAINST the prevailing bias (the "
                           "trend's first failure). BOS: only breaks WITH it "
                           "(continuation). Both: every structure break."),
            ]),
            ParamGroup("Break", [
                Param("break_mode", "Break Confirmation", "Close Beyond", "enum",
                      options=_BREAK_MODES,
                      help="Close Beyond needs the bar to close through the level. "
                           "Wick Beyond accepts any touch through it — earlier, "
                           "but it also counts the stop-run that reverses."),
                Param("break_buffer_atr", "Break Buffer (xATR)", 0.0, "float", 0.0, 3.0, 0.05,
                      "Dead-band around the level, in ATRs, so a level grazed by "
                      "a tick does not count as broken."),
                Param("min_displacement_atr", "Min Break Body (xATR)", 0.0, "float",
                      0.0, 5.0, 0.05,
                      "The breaking bar's body must be at least this many ATRs. "
                      "A genuine break tends to be decisive; a liquidity sweep is "
                      "usually a long wick on a small body. 0 disables."),
            ]),
            ParamGroup("Entry", [
                Param("entry_mode", "Entry", "On Break", "enum", options=_ENTRY_MODES,
                      help="On Break enters on the bar that breaks the level. On "
                           "Retest waits for price to return to the broken level "
                           "and enters there (SMC 'mitigation')."),
                Param("retest_tol_atr", "Retest Tolerance (xATR)", 0.25, "float",
                      0.0, 5.0, 0.05,
                      "How close price must come back to the broken level to "
                      "count as a retest. On Retest only."),
                Param("max_retest_bars", "Max Bars To Retest", 20, "int", 1, 500, 1,
                      "Give up on the retest after this many bars. On Retest only."),
            ]),
            ParamGroup("Higher Scale", [
                Param("use_htf_filter", "Use Higher-Scale Bias?", False, "bool",
                      help="Run a second, slower pivot scale and require its "
                           "structure bias to agree with the event."),
                Param("htf_logic", "Higher-Scale Logic", "Agree", "enum",
                      options=_HTF_LOGIC,
                      help="Agree: only take events pointing the same way as the "
                           "slower structure. Oppose: only take the ones fighting it."),
                Param("htf_pivot_left", "Higher-Scale Left Bars", 30, "int", 2, 500, 1,
                      "Pivot left bars for the slower scale."),
                Param("htf_pivot_right", "Higher-Scale Right Bars", 3, "int", 1, 100, 1,
                      "Pivot right bars for the slower scale."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "With Structure", "enum",
                      options=_DIRECTIONS,
                      help="With Structure takes the break at face value (a "
                           "bullish CHoCH -> LONG). Against Structure fades it."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback. Sizes every xATR tolerance here, and TP/SL "
                      "in TP/SL mode."),
                Param("atr_pct_min", "ATR % Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR % Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (too violent)."),
            ]),
            common.trend_filter_group(),
            common.trading_window_group(),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        piv_left, piv_right = p["pivot_left"], p["pivot_right"]
        want = p["signal_on"]
        use_close = p["break_mode"] == "Close Beyond"
        buf_mult = p["break_buffer_atr"]
        disp_mult = p["min_displacement_atr"]
        on_retest = p["entry_mode"] == "On Retest"
        retest_tol = p["retest_tol_atr"]
        max_retest = p["max_retest_bars"]
        max_age = p["max_level_age"]
        with_structure = p["predict_direction"] == "With Structure"
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        atr = ind.atr(candles, p["vol_atr_length"])
        piv_hi, piv_lo = ind.pivots(candles, piv_left, piv_right)
        buffer_px = [None if a is None else buf_mult * a for a in atr]

        use_htf = bool(p["use_htf_filter"])
        if use_htf:
            h_hi, h_lo = ind.pivots(candles, p["htf_pivot_left"], p["htf_pivot_right"])
            htf_bias = _bias_track(candles, h_hi, h_lo, p["htf_pivot_right"],
                                   [0.0] * n, use_close)
        else:
            htf_bias = [None] * n
        htf_agree = p["htf_logic"] == "Agree"

        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_logic"] == "With Trend"
        trend_ma = (common.moving_average(common.source_values(candles, p["source"]),
                                          p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)
        use_window = bool(p["use_trading_window"])
        allowed = common.allowed_days(p)
        win_start, win_end = common.window_minutes(p)

        # Live reference levels: the most recent confirmed pivot of each kind.
        # 'used' is what makes one level fire once — it re-arms only when a new
        # pivot of that kind confirms.
        hi_lv: Optional[dict] = None
        lo_lv: Optional[dict] = None
        bias: Optional[str] = None
        pending: Optional[dict] = None      # a break awaiting its retest
        cursor = 0

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            # --- advance the pivot cursor FIRST -----------------------------
            # Scan state, not a filter: it must run on every bar, before any
            # `continue`, or the structure history goes wrong.
            while cursor <= i - piv_right and cursor < n:
                j = cursor
                if piv_hi[j] is not None:
                    hi_lv = {"price": piv_hi[j], "i": j, "used": False}
                if piv_lo[j] is not None:
                    lo_lv = {"price": piv_lo[j], "i": j, "used": False}
                cursor += 1

            a = atr[i]
            if a is None or a <= 0:
                continue
            buf = buf_mult * a
            cl, hi, lo = c["close"], c["high"], c["low"]

            # --- did a level break on this bar? ------------------------------
            event = None            # ('CHoCH'|'BOS', 'bull'|'bear', level price)
            probe_up = cl if use_close else hi
            if (hi_lv and not hi_lv["used"] and probe_up > hi_lv["price"] + buf
                    and i - hi_lv["i"] <= max_age):
                hi_lv["used"] = True
                kind = "BOS" if bias == "bull" else ("CHoCH" if bias == "bear" else None)
                bias = "bull"
                if kind:
                    event = (kind, "bull", hi_lv["price"])
            probe_dn = cl if use_close else lo
            if (lo_lv and not lo_lv["used"] and probe_dn < lo_lv["price"] - buf
                    and i - lo_lv["i"] <= max_age):
                lo_lv["used"] = True
                kind = "BOS" if bias == "bear" else ("CHoCH" if bias == "bull" else None)
                prev_bias, bias = bias, "bear"
                # A bar that breaks BOTH levels is a whipsaw, not structure.
                if kind and event is None:
                    event = (kind, "bear", lo_lv["price"])
                elif event is not None:
                    event = None

            if event is not None:
                kind, side_bias, level = event
                if (want == "CHoCH only" and kind != "CHoCH") or \
                   (want == "BOS only" and kind != "BOS"):
                    event = None
                elif disp_mult > 0 and abs(cl - c["open"]) < disp_mult * a:
                    event = None    # no displacement: likely a sweep, not a break

            # --- emit, or arm the retest -------------------------------------
            if event is not None:
                kind, side_bias, level = event
                if on_retest:
                    pending = {"kind": kind, "bias": side_bias, "level": level,
                               "i": i, "atr": a}
                    continue
                self._emit(signals, candles, i, kind, side_bias, level, a, 0,
                           with_structure, ap_min, ap_max, use_window, allowed,
                           win_start, win_end, use_trend, trend_ma, trend_with,
                           use_htf, htf_bias, htf_agree)
                continue

            # A retest fires when price returns to the broken level. Bullish
            # break -> the level is now support, so we want the bar's LOW back
            # down at it.
            if pending is not None:
                if i - pending["i"] > max_retest:
                    pending = None
                else:
                    tol = retest_tol * pending["atr"]
                    reached = (lo <= pending["level"] + tol) if pending["bias"] == "bull" \
                        else (hi >= pending["level"] - tol)
                    if reached:
                        bars = i - pending["i"]
                        kind, side_bias, level = (pending["kind"], pending["bias"],
                                                  pending["level"])
                        pending = None
                        self._emit(signals, candles, i, kind, side_bias, level, a,
                                   bars, with_structure, ap_min, ap_max, use_window,
                                   allowed, win_start, win_end, use_trend, trend_ma,
                                   trend_with, use_htf, htf_bias, htf_agree)
        return signals

    @staticmethod
    def _emit(signals, candles, i, kind, side_bias, level, a, retest_bars,
              with_structure, ap_min, ap_max, use_window, allowed, win_start,
              win_end, use_trend, trend_ma, trend_with, use_htf, htf_bias,
              htf_agree):
        """Apply the tradeable filters and append a signal if they all pass."""
        c = candles[i]
        cl = c["close"]
        atr_pct = a / cl * 100.0
        if atr_pct < ap_min or atr_pct > ap_max:
            return
        if use_window and not common.in_window(c["time"], allowed, win_start, win_end):
            return
        if use_htf:
            hb = htf_bias[i]
            if hb is None:
                return
            agrees = (hb == side_bias)
            if htf_agree and not agrees:
                return
            if not htf_agree and agrees:
                return

        side = ("long" if side_bias == "bull" else "short") if with_structure \
            else ("short" if side_bias == "bull" else "long")
        if use_trend and not common.trend_ok(side, cl, trend_ma[i], trend_with):
            return

        mode = "With" if with_structure else "Against"
        reason = (f"{side_bias.capitalize()} {kind} through {level:.1f} "
                  + (f"(retest +{retest_bars} bars) " if retest_bars else "")
                  + f"-> {mode} Structure {side.upper()} (ATR% {atr_pct:.2f})")
        signals.append(Signal(
            index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
            meta={"event": kind, "structure_bias": side_bias,
                  "level": round(level, 2), "retest_bars": retest_bars,
                  "atr_pct": round(atr_pct, 3),
                  "break_body_atr": round((cl - c["open"]) / a, 2)},
        ))


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m, BTCUSDT). Exit / Backtest params are unused in that mode.
#
# METHOD — fitted to the last two years, with a real holdout inside them
# ----------------------------------------------------------------------
#   TRAIN    2024-07-29 -> 2025-11-01   selection happened here and only here
#   HOLDOUT  2025-11-01 -> 2026-07-29   scored once, after the picks were frozen
#   UNSWEPT  2017-08-17 -> 2024-07-29   never loaded by the sweep at all
#
# 2,025 configurations in three stages (972 structural, 720 fine, 333 entry and
# filter), each scored by calling this module's own `generate_signals`.
# Selection was mechanical: train bets >= the tier floor, both halves of train
# >= 52%, `pivot_left` off the boundary of the union grid, then highest train
# hit rate.
#
# RESULTS — flat $1 per bet, next-candle direction, whole record
# --------------------------------------------------------------
#   preset       bets     hit      z | unswept 17-24 |  2yr    train  HOLDOUT
#   Volume     25,730  57.79%  +25.0 | 18,943 58.41% | 56.05%  55.93%  56.27%
#   (worst full calendar year: Volume 56.4% (2025), Balanced 55.2% (2021),
#    Selective 58.7% (2018) — every full year from 2018 on clears 55%.)
#   Balanced    8,421  56.77%  +12.4 |  6,244 56.97% | 56.22%  57.51%  53.92%
#   Selective   5,219  59.55%  +13.8 |  4,130 59.95% | 58.03%  60.60%  53.94%
#
# **Volume is the pick**, which is unusual for this repo — it is the only tier
# whose holdout matches its train (55.93% -> 56.27%, holdout slightly ahead).
# Balanced and Selective post better headlines and both shrink 3.6pp and 6.7pp
# into the holdout, on 779 and 419 bets respectively.
#
# EVERY BREAK IS TRADED BACKWARDS
# --------------------------------
# Pooled over stage 1, taking the break at face value scores 46.72% train /
# 46.04% holdout; fading it scores 53.15% / 53.86%. There is no ambiguity and no
# configuration where following structure wins. A structure break on 5m BTC is
# an exhaustion signal, not a continuation one — the same verdict every other
# strategy in this repo reaches by its own route.
#
# THE SMC CLAIM, TESTED PROPERLY
# -------------------------------
# Smart-Money-Concepts teaches that CHoCH marks reversal and BOS marks
# continuation. The cleanest test here is **inside the Volume preset**, which
# trades both and therefore compares them on identical settings, identical bars
# and identical filters — the only difference being whether the break went
# against the prevailing bias:
#
#     over the whole record:   CHoCH  10,378 bets  59.05%
#                              BOS    15,352 bets  56.94%
#
# CHoCH is worth **+2.1pp** over BOS, on a matched comparison. Stage 2 agrees
# pooled (CHoCH 55.89% train / 57.10% holdout against BOS 55.25% / 54.35%). So
# the distinction is real and worth drawing — but note what it is *not*: both
# are profitable, and both are profitable **faded**. CHoCH is not the reversal
# signal and BOS the continuation signal; CHoCH is the *better* reversal signal
# and BOS the worse one.
#
# WHAT REVERSAL.PY WAS ALREADY DOING
# -----------------------------------
# reversal.py ships two "Break of Structure" presets at 56.60% and 56.84%. By
# the definitions at the top of this file its detector only ever fires on a
# downtrend broken upward — which is a **CHoCH**, not a BOS — and its presets
# trade it as "Continuation", i.e. they fade it. So that strategy was already
# trading a faded CHoCH under the wrong name, on different machinery, and landed
# within a point of what this one measures. Two independent implementations
# agreeing is the strongest evidence here that the effect is not a fitting
# artefact.
#
# OTHER FINDINGS
# --------------
#   * **The retest entry destroys the edge.** SMC's signature move is to wait
#     for price to return to the broken level and enter on the "mitigation".
#     At all three anchors it costs 5-6pp: 50.57% vs 56.01%, 50.87% vs 56.51%,
#     52.15% vs 57.93%. Whatever the break is telling you has decayed by the
#     time price comes back. Every preset enters On Break.
#   * **Close Beyond beats Wick Beyond** (53.78% train / 54.54% holdout against
#     52.64% / 53.32%). The stop-run through a level that closes back inside is
#     a real phenomenon and it is not a structure break.
#   * **`max_level_age` is inert** — 100, 500 and 2000 give byte-identical
#     results. A level is consumed the moment it breaks and re-arms only when a
#     new pivot confirms, so it never survives long enough to go stale. Kept as
#     a parameter for pathological pivot settings; it does nothing at sane ones.
#   * **Higher-scale "Oppose" helps** (56.98% train / 58.25% holdout at 60-bar
#     pivots, against 55.99% / 56.36% for no filter and 55.23% / 54.89% for
#     "Agree"). Fading a break that fights the *bigger* structure beats fading
#     one aligned with it — coherent with the exhaustion reading, and the
#     opposite of the SMC habit of trading internal structure with swing
#     structure. Only Selective uses it.
#
# CAVEATS, in order of how much they should worry you
# ----------------------------------------------------
# 1. **2017 breaks it**, as it breaks everything mean-reverting here: the
#    partial year (Aug-Dec) scores 47.8 / 43.8 / 50.6%. Fading breaks loses when
#    breaks keep running, which is what a parabolic year is.
# 2. **Balanced and Selective do not hold up.** 57.51% -> 53.92% and 60.60% ->
#    53.94% into the holdout. Their full-record numbers look better than
#    Volume's only because the unswept years were kind to them. Prefer Volume.
# 3. **The edge decays.** 2018-2023 runs 56-64%; 2024-2026 runs 52-62%, and
#    Selective's 2026 is its weakest cell at 52.6%.
# 4. Bets are near-balanced (Volume: 12,533 long at 58.15%, 13,197 short at
#    57.44%) against a 49.6-50.5% up-candle base rate, so this is not beta.
# 5. A bet pays only when hit rate > the price paid. Volume's 56% over the
#    fitted window needs a fill below ~0.56, which a real Polymarket book will
#    not offer on a directional 5m market.
# 6. Days and times are UTC; a bar is stamped by its open time.
_CHOCH_COMMON = {
    "break_mode": "Close Beyond",       # not Wick — see findings
    "entry_mode": "On Break",           # not On Retest — costs 5-6pp
    "retest_tol_atr": 0.25, "max_retest_bars": 20,   # unused at On Break
    "max_level_age": 500,               # inert at sane pivot settings
    "predict_direction": "Against Structure",        # fade the break
    "use_trend_filter": False, "trend_logic": "With Trend",
    "ma_type": "EMA", "ma_length": 200, "source": "close",
    "use_trading_window": False,
}

PRESETS: dict = {
    # *** THE PICK. *** 25,730 bets, 57.79% hit (z +25.0); train 55.93% against
    # 56.27% on the holdout — the only tier here that does not shrink — and
    # 58.41% across the never-swept 2017-2024. Trades both event types; its
    # CHoCH bets run 59.05% and its BOS bets 56.94%.
    "PM 5m Volume": {
        **_CHOCH_COMMON,
        "pivot_left": 12, "pivot_right": 1, "signal_on": "Both",
        "break_buffer_atr": 0.75, "min_displacement_atr": 0.0,
        "use_htf_filter": False, "htf_logic": "Agree",
        "htf_pivot_left": 30, "htf_pivot_right": 3,
        "vol_atr_length": 50, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
    },
    # 8,421 bets, 56.77% hit (z +12.4) — BOS only, with a wide buffer and a
    # full-ATR displacement floor. 57.51% train against 53.92% holdout: a 3.6pp
    # shrinkage on 779 bets. Shipped for the frontier, not as a pick.
    "PM 5m Balanced": {
        **_CHOCH_COMMON,
        "pivot_left": 12, "pivot_right": 1, "signal_on": "BOS only",
        "break_buffer_atr": 1.0, "min_displacement_atr": 1.0,
        "use_htf_filter": False, "htf_logic": "Agree",
        "htf_pivot_left": 30, "htf_pivot_right": 3,
        "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
    },
    # 5,219 bets, 59.55% hit (z +13.8) — CHoCH only, gated on a 60-bar structure
    # that disagrees with the break. The best headline and the worst shrinkage:
    # 60.60% train against 53.94% on 419 holdout bets. NOT RECOMMENDED.
    "PM 5m Selective": {
        **_CHOCH_COMMON,
        "pivot_left": 12, "pivot_right": 3, "signal_on": "CHoCH only",
        "break_buffer_atr": 0.25, "min_displacement_atr": 0.75,
        "use_htf_filter": True, "htf_logic": "Oppose",
        "htf_pivot_left": 60, "htf_pivot_right": 3,
        "vol_atr_length": 50, "atr_pct_min": 0.10, "atr_pct_max": 2.0,
    },
}
