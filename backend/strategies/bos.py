"""Break of Structure — market-structure tracking with BOS and CHoCH separated.

Idea
----
Price is read as a sequence of swing highs and lows. A trend is *up* while it is
making higher highs, *down* while making lower lows, and the two events that
matter are the ones that confirm or contradict that:

  * **BOS** (Break of Structure) — price closes through the structural level in
    the SAME direction as the prevailing trend. An uptrend taking out its last
    structural high is a bullish BOS: continuation.
  * **CHoCH** (Change of Character) — price closes through the structural level
    AGAINST the prevailing trend. An uptrend that instead takes out its last
    structural low is a bearish CHoCH: the first mechanical sign the trend has
    turned.

Both are breaks of the same kind of level. What separates them is only the trend
at the time, and that is exactly why they are worth separating — a continuation
break and a reversal break are different events with different follow-through,
and any strategy that lumps them together is averaging over both.

WHAT THIS ADDS OVER reversal.py
--------------------------------
`reversal.py` already ships fitted BOS presets, but its detector is a single
condition: the last two lows descending, then a close above the most recent swing
high. It cannot express any of the following, all of which are parameters here:

  * **BOS vs CHoCH as separate switches** — reversal's rule fires on what is
    really a CHoCH (a downtrend broken upward) and calls it BOS.
  * **Displacement** — requiring the breaking candle to be impulsive
    (`min_displacement_atr`), not a drift through the level.
  * **Retest entry** — waiting for price to come back to the broken level
    instead of entering on the break bar (`entry_mode`).
  * **A liquidity sweep precondition** — requiring the level to have been wicked
    through and rejected first (`require_sweep`).

The overlap with reversal's presets is measured, not assumed; see the preset
block at the bottom of this file.

Structure state machine
-----------------------
One confirmed swing high and one confirmed swing low are live at any time. On a
close through either (by `break_buffer_atr`), the event is classified against the
prevailing trend, the trend is set to the break's direction, and that level is
retired so it cannot fire twice. A newer confirmed pivot replaces the level.

`predict_direction` takes the event at face value (**With Structure** — a bullish
BOS is a LONG) or fades it (**Against Structure**).

Look-ahead
----------
A fractal pivot at bar j is not knowable until bar ``j + pivot_right``, so pivots
are released through a confirmation cursor and a level is never used before it
existed. Retest entries fire on a bar strictly after the break, using only that
bar's own data. Verified by truncation: cutting the series at any signal bar
reproduces that signal exactly.

Parameter groups
----------------
Structure   pivot_left, pivot_right, max_level_age_bars
Event       use_bos, use_choch, break_buffer_atr, min_displacement_atr,
            require_sweep, sweep_lookback
Entry       entry_mode (Break Close | Retest), retest_max_bars, retest_tol_atr
Decision    predict_direction  (With Structure | Against Structure)
Volatility  vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter (shared)
Allowed Trading Window (shared)
"""

from __future__ import annotations

from typing import List, Optional

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_DIRECTIONS = ["With Structure", "Against Structure"]
_ENTRY_MODES = ["Break Close", "Retest"]


class BreakOfStructure(Strategy):
    id = "bos"
    name = "Break of Structure"
    description = ("Tracks swing structure and trades its two break events — BOS "
                   "(continuation) and CHoCH (change of character) — as separate, "
                   "switchable signals, with optional displacement, liquidity-sweep "
                   "and retest gates. Taken at face value or faded.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Structure", [
                Param("pivot_left", "Pivot Left Bars", 20, "int", 1, 500, 1,
                      "Bars before a swing that it must dominate. Larger means only "
                      "significant swings define structure."),
                Param("pivot_right", "Pivot Right Bars", 1, "int", 1, 100, 1,
                      "Bars after a swing that it must dominate. Also the delay "
                      "before it may be used, so larger is more reliable but later."),
                Param("max_level_age_bars", "Max Level Age (bars)", 300, "int", 1, 5000, 1,
                      "Ignore a structural level older than this. A level price has "
                      "not revisited in hundreds of bars is not structure any more."),
            ]),
            ParamGroup("Event", [
                Param("use_bos", "Trade BOS (continuation)", True, "bool",
                      help="Fire when price breaks the structural level in the "
                           "direction of the prevailing trend."),
                Param("use_choch", "Trade CHoCH (change of character)", True, "bool",
                      help="Fire when price breaks the structural level against the "
                           "prevailing trend — the first sign of a turn."),
                Param("break_buffer_atr", "Break Buffer (xATR)", 0.10, "float", 0.0, 5.0, 0.05,
                      "How far past the level the close must be to count as a break. "
                      "Filters closes that merely graze it."),
                Param("min_displacement_atr", "Min Displacement (xATR)", 0.0, "float", 0.0, 10.0, 0.05,
                      "Require the breaking candle's BODY to be at least this many "
                      "ATRs — an impulsive break rather than a drift. 0 disables."),
                Param("require_sweep", "Require Liquidity Sweep First", False, "bool",
                      help="Only count the break if a recent bar first wicked past "
                           "the level and closed back inside it."),
                Param("sweep_lookback", "Sweep Lookback (bars)", 10, "int", 1, 200, 1,
                      "How recently the sweep must have happened."),
            ]),
            ParamGroup("Entry", [
                Param("entry_mode", "Entry Mode", "Break Close", "enum",
                      options=_ENTRY_MODES,
                      help="Break Close: enter on the bar that breaks the level. "
                           "Retest: wait for price to return to the broken level and "
                           "enter there instead."),
                Param("retest_max_bars", "Retest Max Wait (bars)", 20, "int", 1, 500, 1,
                      "Retest mode only. Give up if price has not returned within "
                      "this many bars."),
                Param("retest_tol_atr", "Retest Tolerance (xATR)", 0.25, "float", 0.0, 5.0, 0.05,
                      "Retest mode only. How close price must come back to the "
                      "broken level to count as a retest."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "With Structure", "enum",
                      options=_DIRECTIONS,
                      help="With Structure: a bullish break is a LONG. Against "
                           "Structure fades every event."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback. Sizes TP/SL and every xATR tolerance here."),
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

        piv_left, piv_right = p["pivot_left"], p["pivot_right"]
        max_age = p["max_level_age_bars"]

        use_bos, use_choch = bool(p["use_bos"]), bool(p["use_choch"])
        buf = p["break_buffer_atr"]
        min_disp = p["min_displacement_atr"]
        req_sweep, sweep_look = bool(p["require_sweep"]), p["sweep_lookback"]

        retest_mode = p["entry_mode"] == "Retest"
        retest_max, retest_tol = p["retest_max_bars"], p["retest_tol_atr"]

        if not (use_bos or use_choch):
            return []

        with_structure = p["predict_direction"] == "With Structure"

        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_logic"] == "With Trend"
        use_window = bool(p["use_trading_window"])
        allowed_days = common.allowed_days(p)
        win_start, win_end = common.window_minutes(p)

        n = len(candles)
        atr = ind.atr(candles, vol_len)
        piv_hi, piv_lo = ind.pivots(candles, piv_left, piv_right)
        trend_ma = (common.moving_average(common.source_values(candles, p["source"]),
                                          p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)

        # live structural levels: [index, price, broken]
        hi_lvl: Optional[list] = None
        lo_lvl: Optional[list] = None
        trend = 0                       # +1 up, -1 down, 0 unknown
        cursor = 0
        pending: Optional[dict] = None  # retest state
        signals: List[Signal] = []

        def swept(i: int, level: float, bull: bool) -> bool:
            """Did a bar in the lookback wick past the level and close back inside?"""
            for k in range(max(0, i - sweep_look), i):
                b = candles[k]
                if bull:
                    if b["high"] > level and b["close"] <= level:
                        return True
                elif b["low"] < level and b["close"] >= level:
                    return True
            return False

        def emit(i: int, c: dict, a: float, bull: bool, label: str,
                 level: float, extra: str) -> None:
            """Apply the shared gates and append a signal."""
            if use_window and not common.in_window(c["time"], allowed_days,
                                                   win_start, win_end):
                return
            cl = c["close"]
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                return
            side = ("long" if bull else "short") if with_structure else \
                   ("short" if bull else "long")
            if use_trend:
                m = trend_ma[i]
                if m is None or not common.trend_ok(side, cl, m, trend_with):
                    return
            mode = "With" if with_structure else "Against"
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl,
                reason=(f"{label} -> {mode} Structure {side.upper()} "
                        f"(level {level:.1f}{extra}, ATR% {atr_pct:.2f})"),
                atr=a,
                meta={"event": label, "level": round(level, 2),
                      "trend": trend, "atr_pct": round(atr_pct, 3)},
            ))

        for i, c in enumerate(candles):
            # --- release newly confirmed pivots --------------------------------
            # Scan state: must run on every bar, before any `continue`.
            while cursor <= i - piv_right and cursor < n:
                j = cursor
                if piv_hi[j] is not None:
                    hi_lvl = [j, piv_hi[j], False]
                if piv_lo[j] is not None:
                    lo_lvl = [j, piv_lo[j], False]
                cursor += 1

            a = atr[i]
            if a is None or a <= 0:
                continue
            cl, op, hi, lo = c["close"], c["open"], c["high"], c["low"]

            # --- a pending retest resolves before any new break ---------------
            if pending is not None:
                if i - pending["break_i"] > retest_max:
                    pending = None
                else:
                    lvl = pending["level"]
                    back = (lo <= lvl + retest_tol * a) if pending["bull"] else \
                           (hi >= lvl - retest_tol * a)
                    if back:
                        emit(i, c, a, pending["bull"], pending["label"], lvl,
                             f", retest +{i - pending['break_i']}b")
                        pending = None

            # --- structural breaks --------------------------------------------
            # Evaluate the upside level first, then the downside; a bar cannot
            # break both because the trend flips on the first.
            for lvl_ref, bull in ((hi_lvl, True), (lo_lvl, False)):
                if lvl_ref is None or lvl_ref[2]:
                    continue
                idx, price, _ = lvl_ref
                if i - idx <= 0 or i - idx > max_age:
                    continue
                pierced = (cl > price + buf * a) if bull else (cl < price - buf * a)
                if not pierced:
                    continue

                # classify against the prevailing trend BEFORE updating it
                is_bos = (trend >= 0) if bull else (trend <= 0)
                label = ("bullish " if bull else "bearish ") + ("BOS" if is_bos else "CHoCH")
                lvl_ref[2] = True            # retired: a level breaks once
                trend = 1 if bull else -1

                if not (use_bos if is_bos else use_choch):
                    break
                if min_disp > 0 and abs(cl - op) < min_disp * a:
                    break
                if req_sweep and not swept(i, price, bull):
                    break

                if retest_mode:
                    pending = {"break_i": i, "level": price, "bull": bull,
                               "label": label}
                else:
                    disp = abs(cl - op) / a
                    emit(i, c, a, bull, label, price, f", displacement {disp:.2f}xATR")
                break

        return signals


# ---------------------------------------------------------------------------
# Presets — FADE the structural break. Read every hit rate against the window's
# own FLAT CEILING, not 50%: 0.24% of 5m candles close exactly at their open and
# lose whichever side you take, so the best a 50/50 bettor can do over the last
# two years is 49.88%.
#
# METHOD, the same protocol as trend_lines.py / gann.py:
#   TRAIN    2024-07-30 -> 2025-10-01   selection here and ONLY here
#   HOLDOUT  2025-10-01 -> 2026-07-30   scored once, after the picks were frozen
#   UNSWEPT  2018-01-01 -> 2024-07-30   never consulted during selection
# Every config ran over the whole 2017-2026 series with signals bucketed by date;
# a standalone two-year run reproduces these to within 0.02pp.
#
# Stage A (12 configs) settled the family, and did so emphatically: every FADED
# config beat every face-value one. Best fade +5.11pp, best face-value -2.73pp.
# That is the fourth independent time this repo has found the break is a stop-run
# that reverts (Reversal, Trend Lines, Support & Resistance, and now this).
#
#   BOS   / Against / Break Close   54.97%   <- family winner
#   BOS   / With    / Break Close   44.87%   <- the mirror; taking the break loses
#
# **Retest entry costs ~2pp** (Break Close 54.97% vs Retest 52.73%). Waiting for
# price to come back to the broken level is standard SMC advice and it is worth
# negative money here. The parameter stays, but no preset uses it.
#
# Stage B (5,040 + 144 configs) tuned the rest off MARGINALS rather than the
# argmax. Grids whose optimum hit a boundary were extended: break_buffer_atr and
# min_displacement_atr both did, and both then turned out to have their apparent
# peaks on noise — buffer 2.0 looks like +1.13pp and displacement 2.0 like
# +1.47pp, but those sit on 745 and 514 mean bets (SE ~1.8-2.2pp).
#
# **Displacement earns nothing.** Its marginal is flat from 0.0 to 1.5 (56.33 ->
# 56.73%, inside noise) before the thin-volume spike. Requiring the breaking
# candle to be impulsive — the whole "displacement" idea — does not separate the
# breaks that revert from the ones that run. It is off in every preset.
# `max_level_age_bars` is likewise flat to 0.13pp and is kept at 300 only because
# a level has to expire somewhere.
#
# SELECTION RULE, fixed before the holdout was read: >= 3,000 TRAIN bets; both
# TRAIN halves above their own ceiling; parameters off the grid edges; then
# maximise TRAIN hit. Winner: pivot_left=20, pivot_right=1, buffer=0.8. Tiers vary
# ONLY break_buffer_atr.
#
#   preset      2yr bets   2yr hit    edge    train    HOLDOUT   unswept     z
#   Volume         7,076    55.64%  +5.76pp   55.68%    55.58%    59.00%   +9.7
#   Balanced       5,323    56.38%  +6.50pp   56.70%    55.89%    58.94%   +9.5
#   Selective      3,640    56.29%  +6.41pp   56.05%    56.66%    58.95%   +7.7
#   Sweep          1,393    57.07%  +7.19pp   58.52%    54.72%    57.20%   +5.4
#
# ===========================================================================
# HOW MUCH OF THIS IS ALREADY IN THE REPO — MEASURED, NOT ASSUMED
# ===========================================================================
# reversal.py already ships fitted BOS presets, and trend_lines, gann and
# support_resistance all end up fading the break of a mechanically located level.
# Comparing (bar, side) signal sets over the two years:
#
#   preset       shared with >=1 existing   unique   unique hit    shared hit
#   Volume              86.8%               13.2%     52.19%        56.16%
#   Balanced            71.3%               28.7%     55.74%        56.62%
#   Selective           56.5%               43.5%     55.56%        56.84%
#   Sweep               82.2%               17.8%     55.24%        57.47%
#
# The single largest overlap is with **gann:PM 5m Volume at 60.5% Jaccard** on the
# Volume tier — unsurprising once you know gann's fitted optimum collapsed to a
# horizontal pivot level, which is very nearly what a structural level is.
# Overlap with reversal's own BOS presets is much lower (10-23%), because
# reversal's detector requires two descending lows and fires on what is really a
# CHoCH.
#
# READ THE TABLE THIS WAY. Selectivity buys independence: as the buffer rises the
# unique share goes 13% -> 29% -> 44% while the unique bets keep scoring 55.5-55.7%.
# **PM 5m Volume is 87% duplicate and its 13% of unique bets are weak (52.19%)** —
# it is the tier least worth running alongside the others. *Balanced* and
# *Selective* carry genuinely new signal at full strength. If you use these as
# Combined voters together with gann or support_resistance, prefer *Selective*, and
# understand that *Volume* would mostly be double-counting.
#
# CAVEATS
# -------
# 1. **2017 breaks every tier** — 44.50-46.37% against that year's 46.50% ceiling.
#    2017 was a parabolic run in which broken structure kept going: the standard
#    failure mode of a fade, and the same year that breaks Reversal and Harmonic.
#    Every full year 2018-2026 clears comfortably.
# 2. **The edge decays.** All tiers score ~3pp higher across the never-swept
#    2018-2024 than over the last two years (Volume 59.00% vs 55.64%). The
#    two-year number is the live estimate; the unswept column is evidence the
#    mechanism is real, not a forecast.
# 3. **Sweep is thin and its holdout is the weakest of the four** — 1,393 bets over
#    two years on a 530-bet holdout, and 58.52% train -> 54.72% holdout is the only
#    material post-selection decay here. Its geometry was also picked from a
#    different grid slice than the other three. Treat it as the most speculative.
# 4. A bet pays only when the hit rate beats the price paid. Balanced's 56.4% needs
#    a fill below ~0.564, and a real Polymarket book will not quote a directional
#    5m market at 0.50 — the hit rate is the finding, the EV is an upper bound.
# 5. Days and times are UTC; a bar is stamped by its open time.
_BOS_COMMON = {
    "pivot_left": 20, "pivot_right": 1,      # R=1: least confirmation lag
    "max_level_age_bars": 300,               # not selected -- see the note above
    "use_bos": True, "use_choch": True,
    "min_displacement_atr": 0.0,             # measured worthless -- see above
    "require_sweep": False, "sweep_lookback": 10,
    "entry_mode": "Break Close",             # retest costs ~2pp
    "retest_max_bars": 20, "retest_tol_atr": 0.25,
    "predict_direction": "Against Structure",  # FADE the break
    "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
    "use_trend_filter": False, "trend_logic": "With Trend",
    "ma_type": "EMA", "ma_length": 200, "source": "close",
    "use_trading_window": False,
}

PRESETS: dict = {
    # 7,076 bets over the last two years (~1 per 2.5 hours), 55.64% vs a 49.88%
    # ceiling; holdout 55.58%, never-swept 2018-2024 59.00%. WIDEST NET AND THE
    # MOST REDUNDANT: 87% of these bets are already emitted by gann /
    # support_resistance / trend_lines / reversal, and the 13% that are not score
    # only 52.19%. Prefer Balanced or Selective if you run this alongside those.
    "PM 5m Volume": {**_BOS_COMMON, "break_buffer_atr": 0.5},
    # 5,323 bets (~1 per 3.3 hours), 56.38%; holdout 55.89%, unswept 58.94%. The
    # selection rule's winner. 29% of its bets are unique, at full strength.
    "PM 5m Balanced": {**_BOS_COMMON, "break_buffer_atr": 0.8},
    # 3,640 bets (~1 per 4.8 hours), 56.29%; holdout 56.66%, unswept 58.95%. The
    # most independent tier — 44% of its bets appear in no other strategy here,
    # and they score 55.56%. The one to pick as a Combined voter.
    "PM 5m Selective": {**_BOS_COMMON, "break_buffer_atr": 1.2},
    # 1,393 bets (~1 per 12.6 hours), 57.07%; holdout 54.72%, unswept 57.20%.
    # Requires the level to be wicked and rejected before the break counts — the
    # one variant reversal.py's detector cannot express. Highest rate of the four
    # and the weakest holdout; see caveat 3.
    "PM 5m Sweep": {
        **_BOS_COMMON,
        "require_sweep": True,
        "pivot_left": 8, "pivot_right": 2,
        "use_choch": False,                  # BOS only won this slice
        "break_buffer_atr": 0.5,
        "min_displacement_atr": 0.25,
        "max_level_age_bars": 100,
    },
}
