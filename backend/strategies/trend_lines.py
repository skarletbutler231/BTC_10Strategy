"""Trend Lines — mechanically drawn support/resistance lines, traded on break or bounce.

Idea
----
A trend line is two swing points joined and extended forward. Discretion is the
usual problem with the tool — pick a different pivot and the whole line moves —
so here the anchors are chosen mechanically, the same way Fib Retracement defines
its swing leg:

  * a **support** line joins the two most recent confirmed pivot LOWS,
  * a **resistance** line joins the two most recent confirmed pivot HIGHS,

and is extended forward as ``value(i) = p2 + slope * (i - i2)``. When a newer
pivot is confirmed the line re-anchors to it, so there is exactly one live
support line and one live resistance line at any bar. No manual anchoring, and
no choosing after the fact which pivots "worked".

Two events are detected, and each can be traded or ignored:

  * **Break** — the first bar to CLOSE through the line by more than
    ``break_buffer_atr``. A broken line is retired, so one line breaks once.
  * **Bounce** — the bar's extreme comes within ``bounce_tol_atr`` of the line
    but the close stays on the trend's side: the line held. Requires the
    previous bar not to have been touching, so a slow drift along the line
    emits one signal rather than ten.

Directionally: breaking support is bearish and breaking resistance is bullish;
bouncing off support is bullish and off resistance is bearish. `predict_direction`
takes that at face value (**With Signal**) or fades it (**Against Signal**) —
worth having, because this repo has repeatedly found the fade to be the better
side of a structure event on BTC 5m.

Look-ahead
----------
A fractal pivot at bar j is not knowable until bar ``j + pivot_right``. Pivots
are therefore admitted through a confirmation cursor that releases a pivot only
once the scan reaches ``j + pivot_right``, so a line is never drawn from a pivot
that had not yet formed. The truncation test in the test suite verifies this:
re-running on a series cut at any signal bar reproduces that signal exactly.

Parameter groups
----------------
Pivots        pivot_left, pivot_right, min_pivot_gap, max_pivot_gap
Line          use_support, use_resistance, require_direction, max_line_age_bars,
              max_slope_atr
Trigger       use_break, break_buffer_atr, use_bounce, bounce_tol_atr
Decision      predict_direction  (With Signal | Against Signal)
Volatility    vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter  (shared)
Allowed Trading Window (shared)
"""

from __future__ import annotations

from typing import List, Optional

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_DIRECTIONS = ["With Signal", "Against Signal"]

# Only the newest two pivots of each kind anchor a line, so the confirmed lists
# are trimmed to this length to stay flat in memory over millions of bars.
_PIVOT_KEEP = 2


class TrendLines(Strategy):
    id = "trend_lines"
    name = "Trend Lines"
    description = ("Support/resistance lines drawn mechanically from the two most "
                   "recent confirmed swing pivots and extended forward. Trades the "
                   "break through a line or the bounce off it — at face value or "
                   "faded.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Pivots", [
                Param("pivot_left", "Pivot Left Bars", 12, "int", 1, 500, 1,
                      "Bars before a pivot that it must dominate. Larger means "
                      "only significant swings anchor a line."),
                Param("pivot_right", "Pivot Right Bars", 2, "int", 1, 100, 1,
                      "Bars after a pivot that it must dominate. Also the delay "
                      "before the pivot may be used, so larger is more reliable "
                      "but later."),
                Param("min_pivot_gap", "Min Pivot Gap (bars)", 5, "int", 1, 500, 1,
                      "Reject a line whose two anchors are closer than this — a "
                      "line off two adjacent swings has no reach."),
                Param("max_pivot_gap", "Max Pivot Gap (bars)", 200, "int", 2, 2000, 1,
                      "Reject a line whose anchors are further apart than this."),
            ]),
            ParamGroup("Line", [
                Param("use_support", "Use Support Lines", True, "bool",
                      help="Lines joining two pivot lows (drawn under price)."),
                Param("use_resistance", "Use Resistance Lines", True, "bool",
                      help="Lines joining two pivot highs (drawn over price)."),
                Param("require_direction", "Require Trend Direction", True, "bool",
                      help="Support must rise (second low above the first) and "
                           "resistance must fall. Off also admits flat and "
                           "counter-sloping lines, i.e. plain horizontal S/R."),
                Param("max_line_age_bars", "Max Line Age (bars)", 200, "int", 1, 5000, 1,
                      "Stop using a line this many bars after its second anchor. "
                      "An extended line drifts far from price and stops meaning "
                      "anything."),
                Param("max_slope_atr", "Max Slope (xATR per bar)", 0.50, "float", 0.0, 10.0, 0.05,
                      "Reject a line steeper than this, measured in ATRs of price "
                      "change per bar. 0 disables the check."),
            ]),
            ParamGroup("Trigger", [
                Param("use_break", "Trade Line Breaks", True, "bool",
                      help="Fire when a close pierces the line. The line is then "
                           "retired, so it breaks at most once."),
                Param("break_buffer_atr", "Break Buffer (xATR)", 0.10, "float", 0.0, 5.0, 0.05,
                      "How far past the line the close must be to count as a "
                      "break. Filters ticks that merely graze it."),
                Param("use_bounce", "Trade Line Bounces", False, "bool",
                      help="Fire when the bar reaches the line but closes back on "
                           "the trend's side — the line held."),
                Param("bounce_tol_atr", "Bounce Tolerance (xATR)", 0.25, "float", 0.0, 5.0, 0.05,
                      "How close the bar's extreme must come to the line to count "
                      "as a touch."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "With Signal", "enum",
                      options=_DIRECTIONS,
                      help="With Signal: support break -> SHORT, resistance break "
                           "-> LONG, support bounce -> LONG, resistance bounce -> "
                           "SHORT. Against Signal inverts all four."),
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
        min_gap, max_gap = p["min_pivot_gap"], p["max_pivot_gap"]
        if min_gap > max_gap:
            return []

        use_sup, use_res = bool(p["use_support"]), bool(p["use_resistance"])
        require_dir = bool(p["require_direction"])
        max_age = p["max_line_age_bars"]
        max_slope = p["max_slope_atr"]

        use_break, use_bounce = bool(p["use_break"]), bool(p["use_bounce"])
        break_buf, bounce_tol = p["break_buffer_atr"], p["bounce_tol_atr"]

        if not (use_sup or use_res) or not (use_break or use_bounce):
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

        # Confirmed pivots, released only at j + pivot_right (see Look-ahead).
        conf_lo: List[tuple] = []          # (index, price)
        conf_hi: List[tuple] = []
        cursor = 0
        # The single live line per side: {i2, p2, slope, touching}
        sup: Optional[dict] = None
        res: Optional[dict] = None

        def build(prev: tuple, cur: tuple, rising: bool, a: float) -> Optional[dict]:
            """A line from two anchors, or None if it fails the validity gates."""
            (i1, p1), (i2, p2) = prev, cur
            span = i2 - i1
            if span < min_gap or span > max_gap:
                return None
            if require_dir and ((p2 <= p1) if rising else (p2 >= p1)):
                return None
            slope = (p2 - p1) / span
            if max_slope > 0 and a and abs(slope) > max_slope * a:
                return None
            return {"i2": i2, "p2": p2, "slope": slope, "touching": False}

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            # --- release newly confirmed pivots, re-anchoring the lines --------
            # Scan state: must run on every bar, before any `continue`.
            while cursor <= i - piv_right and cursor < n:
                j = cursor
                a_j = atr[j] or 0.0
                if piv_lo[j] is not None:
                    cand = (j, piv_lo[j])
                    if use_sup and conf_lo:
                        sup = build(conf_lo[-1], cand, True, a_j)
                    conf_lo.append(cand)
                    del conf_lo[:-_PIVOT_KEEP]
                if piv_hi[j] is not None:
                    cand = (j, piv_hi[j])
                    if use_res and conf_hi:
                        res = build(conf_hi[-1], cand, False, a_j)
                    conf_hi.append(cand)
                    del conf_hi[:-_PIVOT_KEEP]
                cursor += 1

            a = atr[i]
            if a is None or a <= 0:
                continue
            cl, hi, lo = c["close"], c["high"], c["low"]

            # Evaluate both lines. `touching` is line state and is updated even
            # when a filter below rejects the bar, so the "first touch only" rule
            # stays honest.
            events = []          # (kind, side_bias, label, line_value, dist_atr)
            for kind, line in (("support", sup), ("resistance", res)):
                if line is None:
                    continue
                age = i - line["i2"]
                if age <= 0 or age > max_age:
                    continue
                v = line["p2"] + line["slope"] * age
                is_sup = kind == "support"
                # break: close pierces the line by the buffer
                pierced = (cl < v - break_buf * a) if is_sup else (cl > v + break_buf * a)
                # touch: the bar's extreme reaches the line, close stays onside
                near = (lo <= v + bounce_tol * a) if is_sup else (hi >= v - bounce_tol * a)
                onside = (cl > v) if is_sup else (cl < v)
                was_touching = line["touching"]
                line["touching"] = bool(near and onside)

                if use_break and pierced:
                    events.append((kind, "bear" if is_sup else "bull",
                                   f"{kind} break", v, abs(cl - v) / a))
                    # a broken line is retired -- it cannot break twice
                    if is_sup:
                        sup = None
                    else:
                        res = None
                elif use_bounce and near and onside and not was_touching:
                    events.append((kind, "bull" if is_sup else "bear",
                                   f"{kind} bounce", v, abs(cl - v) / a))

            if not events:
                continue
            # Both lines firing opposite ways on one bar is a genuine
            # disagreement (price pinched between them) -- take neither.
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

            labels = ", ".join(e[2] for e in events)
            mode = "With" if with_signal else "Against"
            reason = (f"{labels} -> {mode} Signal {side.upper()} "
                      f"(line {events[0][3]:.1f}, {events[0][4]:.2f}xATR away, "
                      f"ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"events": [e[2] for e in events], "bias": bias,
                      "line_value": round(events[0][3], 2),
                      "dist_atr": round(events[0][4], 3),
                      "atr_pct": round(atr_pct, 3)},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets — FADE the line break. Read the hit rates against 49.76%, not 50%:
# 0.48% of 5m candles close exactly at their open and lose whichever side you
# take, so the ceiling for any 50/50 bettor is (1 - flat) / 2.
#
# METHOD, same protocol as the other presets here:
#   TRAIN    2024-01-01 -> 2025-10-01   selection here and only here
#   HOLDOUT  2025-10-01 -> 2026-07-28   scored once, after the pick was frozen
#   UNSWEPT  2018-01-01 -> 2024-01-01   never loaded by the sweep at all
#
# Stage A (12 configs) settled the family before any tuning. Fading the break
# beat taking it on every variant, and by a wide margin:
#
#   break only / Against Signal   52.45% train   <- winner
#   break only / With Signal      47.42% train   <- the mirror; taking the break loses
#
# That is the same finding the Reversal presets landed on — a structure break on
# BTC 5m tends to be a stop-run that reverts, not the start of a move. It also
# means this strategy, like Reversal, ships presets that trade AGAINST its own
# nominal signal. That is not a bug.
#
# `require_direction=False` also won, which is worth saying plainly: the edge
# does NOT need the line to be sloping the "right" way. Flat and counter-sloping
# lines carry it too, so what these presets really trade is a mechanically drawn
# support/resistance LEVEL being pierced, not a trend line in the textbook sense.
#
# Stage B (486 configs) then tuned pivots, gap, age and buffer. Selection rule,
# fixed in advance: >= 3,000 train bets, both train halves > 50%, pivot_left off
# the grid boundary, then maximise train hit.
#
#   preset            bets     hit    edge    unswept   train   HOLDOUT      z
#   Line Break Volume  21,468  55.88%  +6.12pp  56.93%  54.46%  55.41%   +17.9
#   Line Break Balanced 17,298 56.72%  +6.97pp  58.15%  54.80%  54.66%   +18.3
#
# A TIE, AND HOW IT WAS BROKEN. The rule left pivot_right=1 and pivot_right=3
# separated by 0.03pp on train (54.80 vs 54.83) at n~3,700, where the standard
# error is ~0.8pp — statistically identical. The tie was broken on a
# non-statistical ground: pivot_right IS the confirmation delay, so R=1 makes a
# line usable one bar after its anchor prints instead of three. Less lag is
# strictly better live. R=1 also turned out better out of sample, but that was
# not the reason for choosing it.
#
# WHY THESE ARE PROBABLY REAL. The 2018-2023 columns are ~11-13k bets from years
# the sweep never touched and they score HIGHER than the optimised window; the
# holdout also beats train; and it is not directional beta, with bets running
# ~49% long / ~51% short against a 49.76% ceiling.
#
# WHERE IT FAILS. 2017 (partial year, Aug-Dec, thin early Binance liquidity)
# scores 48.3% — below the ceiling. Every full year 2018-2026 clears it, worst
# 53.80%. Expect the usual failure mode of a fade: a sustained one-way trend, in
# which broken levels keep going.
#
# The volume/accuracy trade is monotone in break_buffer_atr — 0.0 gives 21,468
# bets at 55.88%, 0.1 gives 19,899 at 56.05%, 0.3 gives 17,298 at 56.72%, and
# 0.5 gives 14,890 at 57.04%. Only 0.0 and 0.3 are shipped because only those
# were inside the swept grid; dial the parameter up yourself for fewer, better
# signals.
_LINE_COMMON = {
    # Breaks only. Bounces were swept and lost (see Stage A above).
    "use_break": True, "use_bounce": False, "bounce_tol_atr": 0.25,
    "use_support": True, "use_resistance": True,
    "require_direction": False,               # flat / counter-sloping lines count too
    "pivot_left": 30, "pivot_right": 1,       # R=1: least confirmation lag
    "min_pivot_gap": 5, "max_pivot_gap": 200,
    "max_line_age_bars": 200,                 # not binding above this given the gap cap
    "max_slope_atr": 0.50,
    "predict_direction": "Against Signal",    # FADE the break
    "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
    "use_trend_filter": False, "trend_logic": "With Trend",
    "ma_type": "EMA", "ma_length": 200, "source": "close",
    "use_trading_window": False,
}

PRESETS: dict = {
    # 21,468 bets, 55.88% vs a 49.76% ceiling; unswept 56.93%, holdout 55.41%.
    # Every pierce counts, so this is the widest net.
    "PM 5m Line Break Volume": {**_LINE_COMMON, "break_buffer_atr": 0.0},
    # 17,298 bets, 56.72% vs a 49.76% ceiling; unswept 58.15%, holdout 54.66%.
    # Requires the close to clear the line by 0.3 ATR, which drops the grazes.
    "PM 5m Line Break Balanced": {**_LINE_COMMON, "break_buffer_atr": 0.3},
}


# ---------------------------------------------------------------------------
# 1-MINUTE presets. Swept separately, because the parameters do NOT carry over:
# pivot_left counts BARS, so the 5m winner's 30 is 150 *minutes*. The whole 1m
# grid was therefore expressed in wall-clock minutes, and pivot_left=150 (one bar
# per minute) is the direct analogue of the 5m pick.
#
# READ THESE AGAINST 48.44%, NOT 50%. On 1m tape 3.12% of candles close exactly at
# their open and lose whichever side you take, so the ceiling for any 50/50 bettor
# is (1 - flat)/2. The flat rate also swings hard by year — 33.58% in 2017, 0.12%
# in 2021 — so per-year hit rates are only meaningful against that year's OWN
# ceiling, which is how the table below reads them.
#
# Stage A (12 configs, pivots pre-scaled to 150 min so the family was not judged
# at meaningless 1m bar counts) reproduced the 5m finding exactly: break-only,
# Against Signal — fade the break. Taking the break scored 47.53%.
#
#   preset            bets     hit    edge    unswept   train   HOLDOUT      z
#   1m Line Volume    29,164  52.88%  +4.44pp  54.23%  51.88%  54.85%   +15.2
#   1m Line Balanced  25,274  53.43%  +4.99pp  54.78%  52.36%  55.70%   +15.9
#
# THE BUFFER IS A DIAL, NOT AN OPTIMUM. Stage B's rule landed on
# break_buffer_atr=0.5, which was the TOP of that grid — so the grid was extended
# rather than trusted, and the relationship turned out monotone all the way out:
#
#   buf   bets     hit     unswept   worst yr (2018)
#   0.0  36,352  51.85%    53.36%    45.80%   <- a clearly losing year
#   0.1  33,540  52.12%    53.39%    46.20%   <- still losing
#   0.3  29,164  52.88%    54.23%    47.71%   <- marginally below its ceiling
#   0.5  25,274  53.43%    54.78%    49.58%   <- first tier where EVERY year clears
#   0.8  20,493  53.36%    54.69%    50.67%
#   1.2  15,624  53.54%    55.12%    50.77%
#   2.0   9,371  54.73%    56.10%    50.69%   (z falls to +12.2 on thin volume)
#
# There is no peak to find, so the two shipped tiers were chosen on volume and on
# one non-cherry-picked criterion rather than on max train hit: 0.5 is the lowest
# buffer at which every full year 2018-2026 clears its own ceiling. Push the
# parameter higher yourself for fewer, better signals — 2.0 reaches 54.73%, but on
# a third of the bets and with weaker significance.
#
# WHERE IT FAILS. 2017 is -2.5 to -2.8pp below its ceiling (and its ceiling is only
# 33% because a third of 1m candles were flat on thin early Binance liquidity).
# 2018 is the other soft year: -1.38pp at buf=0.3, +0.49pp at buf=0.5. Every year
# 2019-2026 clears comfortably, +2.85pp to +7.63pp. As with every fade in this
# repo, the failure mode is a sustained one-way trend.
_LINE_1M_COMMON = {
    **_LINE_COMMON,
    # 150 bars = 150 minutes, the wall-clock scale the 5m preset won on.
    "pivot_left": 150, "pivot_right": 1,
    "max_pivot_gap": 1000, "max_line_age_bars": 1000,
    # 1m bars are individually much smaller, so the volatility floor is rescaled.
    "atr_pct_min": 0.02, "atr_pct_max": 1.5,
}

PRESETS.update({
    # 29,164 bets, 52.88% vs a 48.44% ceiling; unswept 54.23%, holdout 54.85%.
    # 2018 sits marginally below its own ceiling at this buffer -- see above.
    "PM 1m Line Break Volume": {**_LINE_1M_COMMON, "break_buffer_atr": 0.3},
    # 25,274 bets, 53.43% vs a 48.44% ceiling; unswept 54.78%, holdout 55.70%.
    # Lowest buffer at which every full year 2018-2026 clears its own ceiling.
    "PM 1m Line Break Balanced": {**_LINE_1M_COMMON, "break_buffer_atr": 0.5},
})
