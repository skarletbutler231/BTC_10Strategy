"""Gann Angles — a Gann fan drawn mechanically from a swing pivot, traded on break or bounce.

Idea
----
W. D. Gann projected a fan of straight lines forward from a significant pivot.
Each line advances a fixed amount of PRICE per bar of TIME, at the classic
ratios — ``1x1`` (one price unit per bar, Gann's "45 degree" line), the steeper
``2x1``/``3x1``/``4x1``/``8x1``, and the shallower ``1x2``/``1x3``/``1x4``/``1x8``.
Gann read the 1x1 as the balance line: while price holds above a rising fan it is
strong, and losing one line means it should travel to the next.

THE SCALE PROBLEM, AND WHAT IS DONE ABOUT IT
--------------------------------------------
"45 degrees" is not a property of price — it is a property of the chart's aspect
ratio. Rescale the y-axis and every Gann angle moves, which is the standard and
entirely fair criticism of the tool. Gann himself fixed a unit per market by
hand (a cent a day, a dollar a week), and on BTC there is no such convention to
inherit.

So the unit here is defined in volatility terms:

    one price unit per bar  =  unit_atr_mult x ATR(at the anchor bar)

That makes the fan invariant to price level and to the instrument, and — more to
the point — reproducible. Nothing about the geometry is chosen after the fact.
Note what this costs: these are not "45 degree lines" in any visual sense, and
``unit_atr_mult`` is a free parameter that has to be fitted like any other. What
survives of Gann is the *shape* of the construction — a fan of fixed-ratio rays
from one pivot — not his degrees.

Construction
------------
  * an **up-fan** is anchored on a confirmed pivot LOW and its rays rise,
    acting as support:      ``value(i) = p0 + ratio * u * (i - i0)``
  * a **down-fan** is anchored on a confirmed pivot HIGH and its rays fall,
    acting as resistance:   ``value(i) = p0 - ratio * u * (i - i0)``

There is exactly one live fan per side; a newer confirmed pivot re-anchors it, so
no discretion about which pivot "worked". Two events are detected per ray, the
same vocabulary trend_lines.py uses:

  * **Break** — the first bar to CLOSE through the ray by more than
    ``break_buffer_atr``. That ray is then retired, so it breaks once.
  * **Bounce** — the bar's extreme comes within ``bounce_tol_atr`` of the ray
    while the close stays onside: the ray held. The previous bar must not have
    been touching, so a drift along a ray emits one signal and not ten.

Breaking support is bearish and breaking resistance is bullish; bouncing off
support is bullish and off resistance is bearish. ``predict_direction`` takes
that at face value (**With Signal**) or fades it (**Against Signal**).

ARMING — why a ray cannot break before price has ever held it
-------------------------------------------------------------
A fan's steep rays (4x1, 8x1) climb away from the anchor far faster than price
does, so within a few bars price is mechanically "below" them without anything
having happened. Counting that as a break would manufacture a signal out of the
geometry alone. Each ray is therefore **armed** on the first bar its close is
onside, and only an armed ray can break or bounce. Rays that price never reaches
simply never fire.

Look-ahead
----------
A fractal pivot at bar j is not knowable until bar ``j + pivot_right``. Anchors
are released through a confirmation cursor that admits a pivot only once the scan
reaches ``j + pivot_right``, so a fan is never drawn from a pivot that had not yet
formed. ``u`` uses ATR at the anchor bar, which is known at that bar. Verified by
truncation: re-running on the series cut at any signal bar reproduces that signal.

Parameter groups
----------------
Anchor       pivot_left, pivot_right, use_up_fan, use_down_fan, max_anchor_age_bars
Fan          unit_atr_mult, use_1x8 .. use_8x1
Trigger      use_break, break_buffer_atr, use_bounce, bounce_tol_atr
Decision     predict_direction  (With Signal | Against Signal)
Volatility   vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter (shared)
Allowed Trading Window (shared)
"""

from __future__ import annotations

from typing import List, Optional

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_DIRECTIONS = ["With Signal", "Against Signal"]

# Gann's fan, in his own notation: "AxB" advances A price units per B bars, so
# the slope in price-units-per-bar is A/B. Ordered shallow -> steep.
_RATIOS: List[tuple] = [
    ("1x8", 1.0 / 8.0),
    ("1x4", 1.0 / 4.0),
    ("1x3", 1.0 / 3.0),
    ("1x2", 1.0 / 2.0),
    ("1x1", 1.0),
    ("2x1", 2.0),
    ("3x1", 3.0),
    ("4x1", 4.0),
    ("8x1", 8.0),
]


class Gann(Strategy):
    id = "gann"
    name = "Gann Angles"
    description = ("A Gann fan projected from the latest confirmed swing pivot, with "
                   "its price-per-bar unit scaled to ATR so the angles are "
                   "reproducible. Trades the break through a ray or the bounce off "
                   "it — at face value or faded.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Anchor", [
                Param("pivot_left", "Pivot Left Bars", 30, "int", 1, 500, 1,
                      "Bars before a pivot that it must dominate. Larger means only "
                      "significant swings anchor a fan."),
                Param("pivot_right", "Pivot Right Bars", 1, "int", 1, 100, 1,
                      "Bars after a pivot that it must dominate. Also the delay "
                      "before the pivot may be used as an anchor, so larger is more "
                      "reliable but later."),
                Param("use_up_fan", "Use Up Fan (from pivot lows)", True, "bool",
                      help="Rising rays anchored on a pivot low; they act as support."),
                Param("use_down_fan", "Use Down Fan (from pivot highs)", True, "bool",
                      help="Falling rays anchored on a pivot high; they act as "
                           "resistance."),
                Param("max_anchor_age_bars", "Max Anchor Age (bars)", 300, "int", 1, 5000, 1,
                      "Stop using a fan this many bars after its anchor. Projected "
                      "far enough, every ray is arbitrarily far from price."),
            ]),
            ParamGroup("Fan", [
                Param("unit_atr_mult", "Price Unit (xATR per bar)", 0.5, "float", 0.01, 10.0, 0.01,
                      "One Gann price unit per bar, as a multiple of ATR at the "
                      "anchor bar. This is what replaces Gann's hand-chosen chart "
                      "scale — the 1x1 ray rises this fast."),
                Param("use_1x8", "Use 1x8 ray", False, "bool",
                      help="Shallowest classic ray: 1 price unit per 8 bars."),
                Param("use_1x4", "Use 1x4 ray", False, "bool",
                      help="1 price unit per 4 bars."),
                Param("use_1x3", "Use 1x3 ray", False, "bool",
                      help="1 price unit per 3 bars."),
                Param("use_1x2", "Use 1x2 ray", True, "bool",
                      help="1 price unit per 2 bars."),
                Param("use_1x1", "Use 1x1 ray", True, "bool",
                      help="Gann's balance line: 1 price unit per bar."),
                Param("use_2x1", "Use 2x1 ray", True, "bool",
                      help="2 price units per bar."),
                Param("use_3x1", "Use 3x1 ray", False, "bool",
                      help="3 price units per bar."),
                Param("use_4x1", "Use 4x1 ray", False, "bool",
                      help="4 price units per bar."),
                Param("use_8x1", "Use 8x1 ray", False, "bool",
                      help="Steepest classic ray: 8 price units per bar. Price "
                           "rarely reaches it, so it rarely arms."),
            ]),
            ParamGroup("Trigger", [
                Param("use_break", "Trade Ray Breaks", True, "bool",
                      help="Fire when a close pierces an armed ray. The ray is then "
                           "retired, so it breaks at most once."),
                Param("break_buffer_atr", "Break Buffer (xATR)", 0.10, "float", 0.0, 5.0, 0.05,
                      "How far past the ray the close must be to count as a break. "
                      "Filters closes that merely graze it."),
                Param("use_bounce", "Trade Ray Bounces", False, "bool",
                      help="Fire when the bar reaches an armed ray but closes back "
                           "on its side — the ray held."),
                Param("bounce_tol_atr", "Bounce Tolerance (xATR)", 0.25, "float", 0.0, 5.0, 0.05,
                      "How close the bar's extreme must come to the ray to count as "
                      "a touch."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "With Signal", "enum",
                      options=_DIRECTIONS,
                      help="With Signal: support break -> SHORT, resistance break -> "
                           "LONG, support bounce -> LONG, resistance bounce -> SHORT. "
                           "Against Signal inverts all four."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback. Sizes TP/SL, the Gann price unit, and every "
                      "xATR tolerance here."),
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
        use_up, use_down = bool(p["use_up_fan"]), bool(p["use_down_fan"])
        max_age = p["max_anchor_age_bars"]

        unit_mult = p["unit_atr_mult"]
        ratios = [(nm, r) for nm, r in _RATIOS if bool(p[f"use_{nm}"])]

        use_break, use_bounce = bool(p["use_break"]), bool(p["use_bounce"])
        break_buf, bounce_tol = p["break_buffer_atr"], p["bounce_tol_atr"]

        if not (use_up or use_down) or not ratios or not (use_break or use_bounce):
            return []
        if unit_mult <= 0:
            return []

        with_signal = p["predict_direction"] == "With Signal"

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

        def build(j: int, price: float, up: bool) -> Optional[dict]:
            """A fan anchored at bar j, or None if ATR there gives no usable unit."""
            a0 = atr[j]
            if not a0 or a0 <= 0:
                return None
            return {"i0": j, "p0": price, "u": unit_mult * a0, "up": up,
                    # per-ray state: armed once price is onside, then it may fire
                    "st": {nm: [False, False, False] for nm, _ in ratios}}
            #             armed ---^      ^-- broken   ^-- touching

        up_fan: Optional[dict] = None
        down_fan: Optional[dict] = None
        cursor = 0
        signals: List[Signal] = []

        for i, c in enumerate(candles):
            # --- release newly confirmed pivots, re-anchoring the fans ---------
            # Scan state: must run on every bar, before any `continue`.
            while cursor <= i - piv_right and cursor < n:
                j = cursor
                if use_up and piv_lo[j] is not None:
                    f = build(j, piv_lo[j], True)
                    if f is not None:
                        up_fan = f
                if use_down and piv_hi[j] is not None:
                    f = build(j, piv_hi[j], False)
                    if f is not None:
                        down_fan = f
                cursor += 1

            a = atr[i]
            if a is None or a <= 0:
                continue
            cl, hi, lo = c["close"], c["high"], c["low"]

            # Evaluate every armed ray of both fans. Ray state (armed/touching) is
            # updated even when a filter below rejects the bar, so "first touch
            # only" and "arm before you can break" stay honest.
            events = []          # (label, bias, ray value, distance in ATRs)
            for fan in (up_fan, down_fan):
                if fan is None:
                    continue
                age = i - fan["i0"]
                if age <= 0 or age > max_age:
                    continue
                is_sup = fan["up"]
                p0, u = fan["p0"], fan["u"]
                st_all = fan["st"]
                for nm, r in ratios:
                    st = st_all[nm]
                    if st[1]:                     # broken -> retired
                        continue
                    v = p0 + r * u * age if is_sup else p0 - r * u * age
                    onside = (cl > v) if is_sup else (cl < v)
                    if not st[0]:                 # not armed yet
                        if onside:
                            st[0] = True
                        continue                  # never fires on the arming bar
                    pierced = (cl < v - break_buf * a) if is_sup else (cl > v + break_buf * a)
                    near = (lo <= v + bounce_tol * a) if is_sup else (hi >= v - bounce_tol * a)
                    was_touching = st[2]
                    st[2] = bool(near and onside)

                    kind = "support" if is_sup else "resistance"
                    if use_break and pierced:
                        events.append((f"{nm} {kind} break",
                                       "bear" if is_sup else "bull", v, abs(cl - v) / a))
                        st[1] = True              # a broken ray cannot break twice
                    elif use_bounce and near and onside and not was_touching:
                        events.append((f"{nm} {kind} bounce",
                                       "bull" if is_sup else "bear", v, abs(cl - v) / a))

            if not events:
                continue
            # Rays firing both ways on one bar is a genuine disagreement (price
            # pinched inside the fan) -- take neither.
            biases = {e[1] for e in events}
            if len(biases) != 1:
                continue
            bias = biases.pop()

            if use_window and not common.in_window(c["time"], allowed_days,
                                                  win_start, win_end):
                continue
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            side = ("long" if bias == "bull" else "short") if with_signal else \
                   ("short" if bias == "bull" else "long")

            if use_trend:
                m = trend_ma[i]
                if m is None or not common.trend_ok(side, cl, m, trend_with):
                    continue

            labels = ", ".join(e[0] for e in events)
            mode = "With" if with_signal else "Against"
            reason = (f"{labels} -> {mode} Signal {side.upper()} "
                      f"(ray {events[0][2]:.1f}, {events[0][3]:.2f}xATR away, "
                      f"ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"events": [e[0] for e in events], "bias": bias,
                      "ray_value": round(events[0][2], 2),
                      "dist_atr": round(events[0][3], 3),
                      "atr_pct": round(atr_pct, 3)},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets — FADE the break of a ray. Read every hit rate against the window's own
# FLAT CEILING, not 50%: 0.24% of 5m candles close exactly at their open and lose
# whichever side you take, so the best a 50/50 bettor can do is (1 - flat) / 2 =
# 49.88% over the last two years.
#
# METHOD, the same protocol as trend_lines.py and harmonic.py:
#   TRAIN    2024-07-30 -> 2025-10-01   selection here and ONLY here
#   HOLDOUT  2025-10-01 -> 2026-07-30   scored once, after the picks were frozen
#   UNSWEPT  2018-01-01 -> 2024-07-30   never consulted during selection
# Every config was run over the whole 2017-2026 series and its signals bucketed by
# date, so warmup state is identical across windows, and every number below comes
# from that run. Re-running each shipped preset on a standalone two-year window —
# what the dashboard actually does — reproduces the hit rates to within 0.02pp and
# the bet counts to within 0.03% (a standalone window has less warmup, so it loses
# a handful of early signals: 8,653 vs 8,655 on Volume, 46,333 vs 46,345 on Angled
# Fan). renko.py measured a 1.4pp gap from this effect; here it is negligible.
#
# Stage A (24 configs) settled the family: **break only, Against Signal**. Fading
# the break beat taking it by 1.9pp, which is the third time this repo has landed
# on that finding (Reversal and Trend Lines both did). A structure break on BTC 5m
# is usually a stop-run that reverts.
#
# ===========================================================================
# THE HEADLINE FINDING: GANN'S ANGLES DO NOT EARN. FLATTENING THE FAN DOES.
# ===========================================================================
# `unit_atr_mult` sets how fast the 1x1 ray climbs, and it is the only parameter
# in this file that matters much. Its marginal — mean TRAIN hit over every config
# sharing that value — is monotone, and it points at zero:
#
#   unit    mean hit    unit    mean hit    unit    mean hit
#   0.0005   55.17%     0.035    53.11%     0.35     50.23%
#   0.001    55.17%     0.05     52.29%     0.50     50.32%
#   0.002    55.09%     0.075    52.04%     0.75     50.02%
#   0.005    54.91%     0.10     51.58%     1.00     50.09%
#   0.010    54.77%     0.15     51.08%     1.50     49.88%
#   0.020    54.51%     0.20     50.89%     2.50     49.79%
#            (columns come from three grids; they overlap and agree at 0.1-0.2)
#
# A steep fan is worth nothing at all — by unit=1.5 the edge is gone entirely.
# Flatten it and the edge appears, rising all the way down to a plateau at
# unit <= 0.002 (0.0005 and 0.001 differ by 0.01pp).
#
# As unit -> 0 every ray flattens toward a HORIZONTAL line through the anchor, so
# what the fitted optimum actually trades is the break of the last confirmed swing
# pivot LEVEL. Two independent checks confirm the fan has genuinely collapsed
# rather than merely flattened:
#
#   1. **The ray sets converge.** At unit=0.0005 the 1x1 ray alone and the
#      three-ray core score 55.23% and 55.11% on 2,588 and 2,617 bets — the same
#      rate on the same trades. Adding six more rays multiplies the bet count by
#      only 2.2x at unit=0.005, against 4.5x at unit=0.2, because near-flat rays
#      sit on top of one another and fire on the same bars.
#   2. **The shipped geometry is flat by inspection.** At unit=0.002 the 1x1 ray
#      drifts 0.60 ATR across its entire 300-bar life — less than the 0.8 ATR
#      break buffer the Balanced preset requires. There is no meaningful angle
#      left.
#
# So these presets keep the name Gann Angles and the fan machinery, but the fan
# they ship is switched off. That is the measurement, not a modelling choice. It
# is the same shape of result harmonic.py reached about the Fibonacci ratios and
# trend_lines.py reached about line slope (`require_direction=False` won there
# too): on BTC 5m the mechanically located LEVEL carries the edge, and the
# geometry drawn through it does not.
#
# `PM 5m Angled Fan` is shipped so that claim can be checked rather than argued
# about — a real, visible fan at unit=0.5, chosen as the best such config on
# TRAIN. It is not a straw man: it clears its ceiling by +1.67pp on 46,345 bets
# (z +7.2), so a genuine Gann fan does carry a small real edge. It is simply worth
# ~5pp per bet LESS than turning the angles off.
#
# Stage B (5,184 + 864 + 392 + 84 configs) tuned the rest. With ~3-20k bets per
# config the standard error is 0.3-0.9pp, so the maximum over thousands of draws
# is inflated by roughly 3 SE by chance — picking the single best row would have
# been mostly noise-fitting. Every parameter was therefore read off its MARGINAL,
# and grids whose optimum landed on a boundary were extended rather than trusted
# (unit twice, buffer once, pivot_left once).
#
# SELECTION RULE, fixed before the holdout was read: >= 3,000 TRAIN bets; both
# TRAIN halves above their own ceiling; unit at the plateau knee and not on a grid
# edge; then maximise TRAIN hit. Winner: pivot_left=20, buffer=0.8. The tiers then
# vary ONLY break_buffer_atr, so they differ in selectivity and not in a
# separately-fitted shape.
#
#   preset       2yr bets   2yr hit    edge    train    HOLDOUT   unswept      z
#   Volume          8,655    55.64%  +5.76pp   55.60%    55.71%    58.65%  +10.7
#   Balanced        5,412    56.54%  +6.66pp   56.86%    56.05%    58.88%   +9.8
#   Selective       2,604    55.65%  +5.76pp   55.17%    56.37%    59.49%   +5.9
#   Angled Fan     46,345    51.55%  +1.67pp   50.90%    52.50%    53.49%   +7.2
#
# WHY THESE ARE PROBABLY REAL. The holdout matches train on every tier (Volume
# +0.11pp, Selective +1.20pp, Balanced -0.81pp). The 2018-2024 columns are
# 6,520-22,727 bets from years the sweep never touched and they score HIGHER than
# the fitted window. It is not directional beta: bets run 49.5-50.7% long over the
# two years against a 49.88% ceiling. And the two-year halves are close on every
# tier (Volume 55.48/55.83, Balanced 57.03/56.01, Selective 55.28/56.04).
#
# CAVEATS, in order of how much they should worry you
# ---------------------------------------------------
# 1. **This strategy is largely redundant with trend_lines.py.** Both end up
#    fading the break of a mechanically located pivot level — trend_lines got
#    there by finding flat lines beat sloping ones, this file by finding flat rays
#    beat angled ones. Trend Lines' Volume tier is 21,468 bets at +6.12pp against
#    this file's 8,655 at +5.76pp, i.e. a similar edge with 2.5x the volume. If you
#    run both as Combined voters, understand that you are mostly double-counting
#    one signal, not combining two.
# 2. **The edge decays.** Every tier scores ~3pp higher across 2018-2024 than over
#    the last two years (Volume 58.65% vs 55.64%). The two-year number is the live
#    estimate; the unswept column is evidence the mechanism is real, not a
#    forecast.
# 3. **2017 breaks Volume.** 44.68% against that year's 46.50% ceiling on 1,316
#    bets. 2017 was a parabolic run in which broken levels kept going — the
#    standard failure mode of a fade, and the same year that breaks Reversal and
#    Harmonic. Balanced merely matches its 2017 ceiling (46.53 vs 46.50);
#    Selective clears it. Every full year 2018-2026 clears comfortably.
# 4. **`break_buffer_atr` is noisy, not a clean optimum.** Its curve is not
#    monotone: 0.8 is a real +1.20pp step at ~3,400 bets, but the apparent 58.56%
#    at buffer=2.0 sits on 1,127 bets (SE 1.5pp) with halves of 55.09/61.64 — that
#    is noise, and the >=3,000-bet gate is what kept it out. Treat the buffer as a
#    volume dial, not a tuned constant.
# 5. **`max_anchor_age_bars` does nothing measurable.** Its marginal is flat to
#    0.01pp across 100/300/600. It is kept at 300 because a fan has to expire
#    somewhere, not because 300 was selected.
# 6. **Selective is thin** — 2,604 bets over two years, ~1 per 6.7 hours, and a
#    1,036-bet holdout. That is not enough to separate 55% from 57%.
# 7. A bet pays only when the hit rate beats the price paid. Balanced's 56.5% needs
#    a fill below ~0.565, and a real Polymarket book will not quote a directional
#    5m market at 0.50 — the hit rate is the finding, the EV is an upper bound.
# 8. Days and times are UTC; a bar is stamped by its open time.
_GANN_COMMON = {
    # Breaks only, faded. Bounces lost in Stage A.
    "use_break": True, "use_bounce": False, "bounce_tol_atr": 0.25,
    "use_up_fan": True, "use_down_fan": True,
    "pivot_left": 20, "pivot_right": 1,      # R=1: least confirmation lag
    "max_anchor_age_bars": 300,              # not selected -- see caveat 5
    # The fan, switched off: 0.002 xATR/bar drifts 0.60 ATR over 300 bars.
    "unit_atr_mult": 0.002,
    "use_1x8": False, "use_1x4": False, "use_1x3": False, "use_1x2": False,
    "use_1x1": True,                         # one ray; the others add nothing
    "use_2x1": False, "use_3x1": False, "use_4x1": False, "use_8x1": False,
    "predict_direction": "Against Signal",   # FADE the break
    "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
    "use_trend_filter": False, "trend_logic": "With Trend",
    "ma_type": "EMA", "ma_length": 200, "source": "close",
    "use_trading_window": False,
}

PRESETS: dict = {
    # 8,655 bets over the last two years (~1 per 2.0 hours), 55.64% vs a 49.88%
    # ceiling; holdout 55.71%, never-swept 2018-2024 58.65%. The widest net that
    # still passes the >=3,000-train-bet gate. Fails 2017 -- see caveat 3.
    "PM 5m Volume": {**_GANN_COMMON, "break_buffer_atr": 0.3},
    # 5,412 bets (~1 per 3.2 hours), 56.54%; holdout 56.05%, unswept 58.88%.
    # The selection rule's winner: the close must clear the level by 0.8 ATR.
    "PM 5m Balanced": {**_GANN_COMMON, "break_buffer_atr": 0.8},
    # 2,604 bets (~1 per 6.7 hours), 55.65%; holdout 56.37%, unswept 59.49%. The
    # only tier that clears 2017. Thin -- see caveat 6.
    "PM 5m Selective": {**_GANN_COMMON, "break_buffer_atr": 1.6},
    # THE CONTROL, not a recommendation: a real, visible Gann fan (unit=0.5, all
    # nine rays), the best such config on TRAIN. 46,345 bets at 51.55% vs the
    # 49.88% ceiling -- a genuine +1.67pp edge (z +7.2) on 5x the volume, and ~5pp
    # per bet worse than switching the angles off. Ship-and-compare, so the
    # headline finding above can be checked rather than taken on trust.
    "PM 5m Angled Fan": {
        **_GANN_COMMON,
        "pivot_left": 30, "pivot_right": 2,
        "unit_atr_mult": 0.5,
        "use_1x8": True, "use_1x4": True, "use_1x3": True, "use_1x2": True,
        "use_1x1": True,
        "use_2x1": True, "use_3x1": True, "use_4x1": True, "use_8x1": True,
        "break_buffer_atr": 0.1,
    },
}
