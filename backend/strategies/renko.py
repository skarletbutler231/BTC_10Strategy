"""Renko — trade the brick sequence of a price-only, time-free chart.

Idea
----
A Renko chart throws away time. It prints a fixed-size **brick** only when price
moves a full brick beyond the last one; a quiet hour prints nothing and a violent
one prints six. What is left is a stair-step of same-size moves, which is a
deliberately crude noise filter: anything smaller than one brick is invisible,
so the chart cannot wiggle.

Two things a Renko chart says that a candle chart does not say as plainly:

  * a **run** of N bricks in one direction is N brick-sizes of net, one-way
    movement — a trend statement with volatility already divided out;
  * a **reversal** brick after such a run is price giving back `reversal_bricks`
    bricks, which is the chart's own definition of "that trend just broke".

This strategy fires on those events and lets `predict_direction` decide whether
to trade with the newest brick or against it.

Construction, and what it costs
--------------------------------
Bricks are built from **closes only**. Intrabar highs and lows are ignored, so
the brick sequence never depends on assuming which of a bar's extremes came
first — an assumption a backtest cannot check and which flatters wick-based
Renko. The cost is honest: this is the slower, less sensitive Renko, and a bar
that spikes and returns prints nothing.

The rules, with `anchor` = the close of the most recent brick and `R` =
`reversal_bricks`:

  * In an up-run, ``floor((close - anchor) / brick)`` new UP bricks print
    whenever that is >= 1.
  * Otherwise a DOWN brick needs price to fall R bricks below the anchor — the
    first R-1 of those just retrace the last up brick's own body, which is why
    the classic setting R = 2 means "one brick down from the last brick's open".
    ``floor((anchor - close) / brick) - (R - 1)`` bricks then print.

So a single bar prints bricks in one direction only.

One consequence to know before comparing runs: the brick grid is **anchored at
the first bar of the series**, so changing the date range shifts every brick
after it. Backtesting the same preset over 2025-2026 alone does not reproduce
the 2025-2026 slice of a run that started in 2017 — the counts drift by a few
percent. This is inherent to Renko, not a bug, and it is the reason the preset
tables below are all scored over one fixed full-history run.

Brick size follows `brick_mode`:

  * **ATR** — ``brick_atr_mult`` x ATR at the bar the brick prints. Adaptive, and
    the only mode that stays comparable across a decade in which BTC went from
    $4k to $60k+. Note the honest consequence: the brick size is a function of
    recent volatility, so the chart is not reproducible from a single number and
    two runs over different date ranges will not share a brick grid.
  * **Percent** — ``brick_pct`` % of the current close. Also scale-free, and
    fixed with respect to volatility rather than tracking it.
  * **Fixed** — ``brick_fixed`` in quote units. What a charting package means by
    "brick size", and the one to use if you want a specific dollar grid.

Parameter groups
----------------
Brick Size    brick_mode, atr_length, brick_atr_mult, brick_pct, brick_fixed
Bricks        reversal_bricks
Signal        trigger, min_run_bricks, max_new_bricks
Volatility    vol_atr_length, atr_pct_min, atr_pct_max
Decision      predict_direction  (Follow Brick | Fade Brick)
Trend Filter  use_trend_filter, trend_logic, ma_type, ma_length, source

Triggers
--------
**Brick Reversal** — this bar printed brick(s) opposite to the previous
direction, and the run they ended was at least `min_run_bricks` long. The
canonical Renko signal.

**Brick Run** — the run of same-direction bricks reaches `min_run_bricks` on
this bar. Fires once per run (the bar that crosses the threshold), not on every
brick after it.

**Any New Brick** — any bar that prints at least one brick. The unfiltered
control condition: it measures what the brick grid alone is worth, with no run
or reversal structure on top.

`max_new_bricks` optionally rejects bars that printed more bricks than that at
once — a bar that prints five bricks is a gap or a liquidation, not the tidy
stair-step the pattern assumes. 0 disables the check.

Entry logic
-----------
  1. Update the brick sequence with this bar's close.
  2. The trigger above must fire on this bar.
  3. `max_new_bricks`, then the volatility regime: ATR%(vol_atr_length) inside
     [atr_pct_min, atr_pct_max].
  4. Optional trend-filter agreement.
  5. `predict_direction` sets the side relative to the NEWEST brick: Follow
     Brick trades its direction, Fade Brick trades the opposite.
"""

from __future__ import annotations

from math import floor
from typing import List

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

MODE_ATR = "ATR"
MODE_PCT = "Percent"
MODE_FIXED = "Fixed"

TRIG_REVERSAL = "Brick Reversal"
TRIG_RUN = "Brick Run"
TRIG_ANY = "Any New Brick"


def build_bricks(candles: List[dict], atr: List, mode: str, atr_mult: float,
                 pct: float, fixed: float, reversal_bricks: int):
    """Close-based Renko -> one record per bar that printed bricks.

    Returns a list of ``{"i", "dir", "count", "run", "prev_run", "size"}`` where
    ``dir`` is +1/-1, ``count`` is how many bricks that bar printed, ``run`` is
    the length of the same-direction run *after* those bricks, and ``prev_run``
    is the length of the run this bar reversed (0 if it did not reverse).

    Fully causal: each bar is processed with information available at its close.
    """
    out: List[dict] = []
    anchor = None
    bdir = 0
    run = 0
    R = max(1, int(reversal_bricks))

    for i, c in enumerate(candles):
        cl = c["close"]
        if mode == MODE_PCT:
            size = pct / 100.0 * cl
        elif mode == MODE_FIXED:
            size = fixed
        else:
            a = atr[i]
            if a is None:
                continue
            size = atr_mult * a
        # A brick smaller than a rounding error would print thousands at a time.
        if size <= 0 or size < cl * 1e-6:
            continue
        if anchor is None:
            anchor = cl
            continue

        count = 0
        d = 0
        if bdir >= 0:                                  # up-run (or no direction yet)
            n_up = floor((cl - anchor) / size)
            if n_up >= 1:
                count, d = int(n_up), 1
                anchor += count * size
            else:
                need = R if bdir > 0 else 1             # no run yet -> no reversal cost
                n_dn = floor((anchor - cl) / size)
                if n_dn >= need:
                    count, d = int(n_dn) - (need - 1), -1
                    anchor -= (need - 1 + count) * size
        else:                                          # down-run
            n_dn = floor((anchor - cl) / size)
            if n_dn >= 1:
                count, d = int(n_dn), -1
                anchor -= count * size
            else:
                n_up = floor((cl - anchor) / size)
                if n_up >= R:
                    count, d = int(n_up) - (R - 1), 1
                    anchor += (R - 1 + count) * size
        if count <= 0:
            continue

        if d == bdir:
            prev_run, run = 0, run + count
        else:
            prev_run, run = run, count
            bdir = d
        out.append({"i": i, "dir": d, "count": count, "run": run,
                    "prev_run": prev_run, "size": size})
    return out


class Renko(Strategy):
    id = "renko"
    name = "Renko"
    description = ("Builds a close-based Renko brick sequence (ATR-, percent- or "
                   "fixed-size) and trades its runs and reversals — following the "
                   "newest brick or fading it, with volatility and trend filters.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Brick Size", [
                Param("brick_mode", "Brick Mode", MODE_ATR, "enum",
                      options=[MODE_ATR, MODE_PCT, MODE_FIXED],
                      help="How big a brick is: a multiple of ATR (adaptive), a "
                           "percentage of price (scale-free), or a fixed number of "
                           "quote units (a literal price grid)."),
                Param("atr_length", "ATR Length", 14, "int", 2, 200, 1,
                      "Lookback for the ATR that sizes bricks in ATR mode — and, in "
                      "every mode, sizes TP/SL."),
                Param("brick_atr_mult", "Brick Size (xATR)", 0.5, "float", 0.05, 10.0, 0.05,
                      "ATR mode only: brick = this many ATRs."),
                Param("brick_pct", "Brick Size (% of price)", 0.1, "float", 0.005, 5.0, 0.005,
                      "Percent mode only: brick = this percent of the close."),
                Param("brick_fixed", "Brick Size (quote units)", 100.0, "float", 0.01, 100000.0, 1.0,
                      "Fixed mode only: brick size in USDT. Only sensible over a range "
                      "where price does not change by an order of magnitude."),
            ]),
            ParamGroup("Bricks", [
                Param("reversal_bricks", "Bricks To Reverse", 2, "int", 1, 5, 1,
                      "How far price must move against the run to print the first "
                      "opposite brick. 2 is the classic Renko rule; 1 makes the chart "
                      "flip on every brick-sized wiggle."),
            ]),
            ParamGroup("Signal", [
                Param("trigger", "Trigger", TRIG_REVERSAL, "enum",
                      options=[TRIG_REVERSAL, TRIG_RUN, TRIG_ANY],
                      help="Brick Reversal: bricks flipped after a run of at least "
                           "'Min Run'. Brick Run: the run just reached 'Min Run'. Any "
                           "New Brick: every brick-printing bar (the control)."),
                Param("min_run_bricks", "Min Run (bricks)", 3, "int", 1, 50, 1,
                      "Run length the trigger needs — the run being ended for Brick "
                      "Reversal, the run being reached for Brick Run. Ignored by Any "
                      "New Brick."),
                Param("max_new_bricks", "Max Bricks This Bar", 0, "int", 0, 50, 1,
                      "Reject bars that printed more than this many bricks at once (a "
                      "gap or a liquidation cascade rather than a stair-step). "
                      "0 = no limit."),
            ]),
            ParamGroup("Volatility Filter", [
                Param("vol_atr_length", "Vol ATR Length", 20, "int", 2, 200, 1,
                      "Lookback for the regime ATR (as % of price)."),
                Param("atr_pct_min", "ATR% Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR% Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent regime)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Fade Brick", "enum",
                      options=["Follow Brick", "Fade Brick"],
                      help="Follow Brick trades in the direction of the newest brick "
                           "(the trend-following reading). Fade Brick trades against "
                           "it."),
            ]),
            common.trend_filter_group(),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        atr = ind.atr(candles, p["atr_length"])
        atr_vol = ind.atr(candles, p["vol_atr_length"])
        bricks = build_bricks(candles, atr, p["brick_mode"], p["brick_atr_mult"],
                              p["brick_pct"], p["brick_fixed"], p["reversal_bricks"])
        if not bricks:
            return []

        use_trend = p["use_trend_filter"]
        trend_ma = (ind.ma(ind.price_source(candles, p["source"]),
                           p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)

        trigger = p["trigger"]
        min_run = p["min_run_bricks"]
        max_new = p["max_new_bricks"]
        follow = p["predict_direction"] == "Follow Brick"
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        with_trend = p["trend_logic"] == "With Trend"

        signals: List[Signal] = []
        for b in bricks:
            i = b["i"]
            if trigger == TRIG_REVERSAL:
                if b["prev_run"] < min_run:
                    continue                       # not a reversal, or too short a run
            elif trigger == TRIG_RUN:
                # fire on the bar that CROSSES the threshold, once per run
                if b["run"] < min_run or (b["run"] - b["count"]) >= min_run:
                    continue
            if max_new > 0 and b["count"] > max_new:
                continue

            a, av = atr[i], atr_vol[i]
            if a is None or a <= 0 or av is None or av <= 0:
                continue
            c = candles[i]
            cl = c["close"]
            atr_pct = av / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            up = b["dir"] > 0
            side = ("long" if up else "short") if follow else ("short" if up else "long")

            if use_trend:
                tm = trend_ma[i]
                if tm is None:
                    continue
                agree = (side == "long") == (cl > tm)
                if agree != with_trend:
                    continue

            what = ("reversal after a run of %d" % b["prev_run"]) \
                if b["prev_run"] else ("run of %d" % b["run"])
            reason = (f"{b['count']} {'UP' if up else 'DOWN'} brick"
                      f"{'s' if b['count'] > 1 else ''} "
                      f"({b['size'] / cl * 100:.2f}% each), {what} "
                      f"-> {'follow' if follow else 'fade'} {side.upper()} "
                      f"(ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"brick_dir": b["dir"], "bricks": b["count"], "run": b["run"],
                      "prev_run": b["prev_run"], "brick_pct": round(b["size"] / cl * 100, 4),
                      "atr_pct": round(atr_pct, 3),
                      "mode": "follow" if follow else "fade"},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m). Exit / Backtest params are unused in that mode.
#
# Sweep: BTCUSDT 1m resampled to 5m over the entire DB — 939,433 bars,
# 2017-08-17 .. 2026-07-28 — in three stages totalling 2,832 combinations
# (660 structural + 1,452 grid x trigger + 720 filter). Every combination was
# scored by calling this module's own `generate_signals`.
#
# 'Fixed' brick mode was excluded from the sweep and no preset uses it: over
# this record BTC ranges from ~$3k to ~$110k, so one dollar brick is absurdly
# coarse at one end and absurdly fine at the other. It is in the strategy for
# short hand-picked windows, where it is the mode a charting package means.
#
# Parameters were selected on **2017-2023 only**; 2024-2026 was scored
# afterwards and never consulted while choosing. Admission: binomial z >= 2.5 on
# train, both halves of train (2017-2020, 2021-2023) independently >= 52%, and
# every TRAIN calendar year carrying >= 25 bets above 50%. Of 2,172 scored
# configurations 793 were admissible; each preset is the highest TRAIN hit rate
# inside its bet-count band.
#
# Measured results (whole DB, flat $1 per bet)
# --------------------------------------------
#   preset      bets     hit   train 17-23   TEST 24-26  2025-26  worst yr    z
#   Volume     8,115  58.16%      58.59%       55.94%     56.01%   55.17%   14.2
#   Balanced   3,844  59.81%      60.81%       56.07%     57.58%   54.45%   11.9
#   Selective  1,154  60.92%      62.56%       52.41%     53.61%   51.11%    7.8
#   Hi Hit       328  63.11%      64.55%       60.19%     56.94%   44.00%    4.3
#
# **Volume is the preset to use.** It loses only 2.7 points from train to
# holdout — the smallest shrinkage here — holds 55.94% across 1,314
# out-of-sample bets and 56.01% over 2025-26, and every single calendar year in
# the record is at or above 55.17%. It is also the highest-volume tier, which is
# not the usual ordering in this repo.
#
# THE RUN STRUCTURE IS WORTH ~1pp — BUT THE NAIVE COMPARISON SAYS IT IS WORTH
# NOTHING
# ---------------------------------------------------------------------------
# Stage 1 pooled each trigger over its whole grid and concluded that 'Any New
# Brick' — the control, with no run or reversal structure at all — BEAT both
# structured triggers (54.57% vs 53.79% and 52.47%). That was an artifact: the
# structured triggers were being averaged over min_run values from 1 to 8, and
# the short ones are worthless. Stage 2 ran the same brick grid through every
# trigger so the comparison is matched (132 grids, train only):
#
#   trigger                     bets      hit     vs the Any-New-Brick control
#   Brick Reversal, run >= 8    108,710  57.98%             +1.19pp
#   Brick Reversal, run >= 5    280,062  57.42%             +1.13pp
#   Brick Run,      run >= 8     88,978  57.13%             +0.56pp
#   Brick Reversal, run >= 3    576,241  56.81%             +0.51pp
#   Any New Brick             3,491,721  56.55%              0.00
#   Brick Reversal, run >= 2    850,997  56.61%             +0.26pp
#   Brick Reversal, run >= 1  1,270,608  56.09%             -0.13pp
#
# So the pattern is real and monotone in run length: fading a brick that ENDS a
# long one-way run beats fading an arbitrary brick by about a point, and the
# effect only appears from run >= 3. Every preset below uses Brick Reversal with
# min_run_bricks of 5 or 8. Note also that 'Brick Run' (fade a run as it
# extends) is consistently worse than 'Brick Reversal' (fade the brick that
# breaks it) at the same length — the turn matters, not just the run.
#
# Other findings
# --------------
#   * **Fade Brick, overwhelmingly.** Pooled over stage 1, Fade beats Follow by
#     8-10 points on every trigger: 54.57% vs 44.93% on Any New Brick, 53.79% vs
#     45.55% on Brick Run, 52.47% vs 46.84% on Brick Reversal. A Renko brick on
#     BTC 5m is an overshoot, not a breakout. Same direction as every other
#     strategy in this repo.
#   * **Bigger bricks, better bets**, up to a point: pooled over stage 2,
#     brick_atr_mult 1.0 / 1.5 / 2.0 / 3.0 / 4.0 / 6.0 gives 56.26 / 57.14 /
#     57.25 / 56.32 / 56.04 / 55.28%, and brick_pct 0.3 / 0.5 / 0.75 / 1.0 / 1.5
#     gives 56.07 / 56.56 / 56.59 / 57.03 / 57.06%. The two sizings perform
#     about equally (ATR 56.60% vs Percent 56.39%); three of four presets use
#     Percent because it won on train, not because it is structurally better.
#   * **`reversal_bricks` >= 2 is worth ~0.7pp** over the flip-on-every-brick
#     setting: 1 / 2 / 3 / 4 give 56.15 / 56.84 / 56.81 / 56.72%. The classic
#     Renko rule is the right one and there is nothing beyond it.
#   * **`max_new_bricks` earns nothing.** 0 (off) / 1 / 2 give 56.48 / 56.53 /
#     56.50% — a filter with no effect, which is worth knowing before reaching
#     for it. It is kept because it costs nothing and isolates gap bars.
#   * **"With Trend" helps here**, opposite to Fib Retracement and Volume
#     Exhaustion: pooled over stage 3, With Trend EMA200 58.70% vs filter-off
#     57.89% vs Against Trend ~57.8%. Combined with Fade Brick that reads as
#     "fade DOWN bricks while price is above the EMA200" — buy dips in an
#     uptrend, which is what a brick chart makes visible.
#
# Caveats
# -------
# 1. **Do not push this for volume either.** The widest admissible net — 82,807
#    bets, train 54.24% — falls to 50.80% on the holdout and 50.95% over
#    2025-26. Same shape as Elliott Wave: the edge is in the selectivity.
# 2. **Selective is NOT RECOMMENDED.** 62.56% on train against 52.41% on the
#    holdout is the worst shrinkage here (-10.2pp), and 2024 came in at 51.11%.
#    It is shipped to show the frontier, not as a pick.
# 3. **Hi Hit is thin and unstable at the edges.** ~36 bets a year; 2026 so far
#    is 44.0% on 25 bets and 2017 was 44.4% on 18. Its holdout 60.19% rests on
#    108 bets (+/-9pp).
# 4. **ATR-mode bricks are not reproducible from one number.** Brick size tracks
#    recent volatility, so two runs over different date ranges do not share a
#    brick grid. Percent mode has the same scale-free property with a fixed
#    yardstick, which is one reason most presets use it.
# 5. Percent-mode bet counts are strongly front-loaded in the record (1,218 bets
#    in 2017 vs 264 in 2026 for Volume) because BTC's relative volatility fell
#    over the period. The hit rate is flat across that; the volume is not.
#
# A bet pays only when hit rate > your odds: Volume's 55.94% needs entry below
# ~0.5594 to be +EV.
#
# ---------------------------------------------------------------------------
# THE "- 2yr Train" PRESETS: refitted on the trailing two years alone
# ---------------------------------------------------------------------------
# Same three stages re-run over 2024-07-28 .. 2026-07-28 (210,528 bars) alone.
# No holdout exists in that window, so admission could only use in-window
# stability: z >= 2.5, both one-year halves independently >= 52%, >= 60 bets per
# half. 867 of 2,172 configurations passed; each preset is the highest hit rate
# in its bet-count band (bands scaled by 210,528/939,433).
#
# The in-window number is fitted; the 2017-2023 column is not — those are years
# these configurations never saw:
#
#   preset                      last 2yr (fitted)      2017-2023 (unseen)   halves
#   Volume - 2yr Train          1,852  56.80%         11,143  57.92%     55.41/58.58
#   Balanced - 2yr Train          784  59.69%          4,883  59.57%     59.38/60.12
#
# Both hold their rate across the unseen years — *Balanced - 2yr Train* almost
# exactly (59.69% vs 59.57%). Renko's structure is evidently stable across
# regimes in a way Elliott Wave's was not. Run over the WHOLE record, every
# single year clears 52% for both (scored inside a full-history run, so the
# 2024-26 cells differ slightly from the standalone table above — caveat 2):
#
#              2017   2018   2019   2020   2021   2022   2023   2024   2025   2026
#   Volume     52.2%  57.9%  63.1%  59.6%  57.5%  58.4%  60.3%  55.2%  57.2%  54.6%
#   Balanced   53.7%  60.6%  62.6%  59.1%  59.7%  60.0%  61.5%  56.1%  56.5%  57.4%
#
# **What the refit buys here is volume, not rate.** Unlike Elliott Wave, the
# full-record Renko presets have NOT decayed: *PM 5m Volume* still runs 58.09%
# over the trailing 2 years, better per bet than the 56.80% of the refit. But it
# only fires 964 times in that window against the refit's 1,852. So:
#
#   * want the best rate on recent tape          -> full-record *PM 5m Volume*
#   * want roughly double the bets for ~1.3pp    -> *PM 5m Volume - 2yr Train*
#   * want both improved over the full-record
#     Balanced (57.44% on 585 bets)              -> *PM 5m Balanced - 2yr Train*
#                                                   (59.69% on 784)
#
# The refit also softened the run requirement: *Volume - 2yr Train* uses
# min_run_bricks = 2, below the run >= 3 threshold where the full-record matched
# comparison found the run structure starts paying. Over two years that shorter
# run wins on volume; over nine it does not. Treat it as the volume knob it is.
#
# Caveats specific to these two
# ------------------------------
# 1. **No out-of-sample evidence for the fit itself.** The 2017-2023 column is
#    the past, not the future.
# 2. **Renko's brick-anchor effect shows up in the numbers.** Scoring the same
#    preset inside a full-history run versus a standalone 2-year run gives
#    55.38% vs 56.80% for *Volume - 2yr Train* — a 1.4pp gap purely from where
#    the brick grid is anchored. The tables above use the standalone run, which
#    is what you get by entering that date range in the dashboard.
# 3. **No Selective or Hi Hit tier is shipped for this window.** The two best
#    candidates were 253 bets at 63.64% and 239 at 64.02% — near-duplicate
#    configurations (they differ only in max_new_bricks), about 2.5 bets a week,
#    +/-6pp interval, no holdout. Use the full-record tiers if you want thin.
PRESETS: dict = {
    # *** THE PICK. *** 8,115 bets (~1 per 9.5 hours), 58.16% hit; 55.94% across
    # 1,314 holdout bets, 56.01% over 2025-26, and no calendar year below
    # 55.17%. The only preset here that uses no filters at all.
    "PM 5m Volume": {
        "brick_mode": MODE_PCT, "atr_length": 14, "brick_atr_mult": 0.5,
        "brick_pct": 0.3, "brick_fixed": 100.0,
        "reversal_bricks": 3,
        "trigger": TRIG_REVERSAL, "min_run_bricks": 5, "max_new_bricks": 0,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": "Fade Brick",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # 3,844 bets, 59.81% hit; 56.07% holdout, 57.58% over 2025-26. Volume's
    # brick grid plus the two filters that won on train — a quiet-tape band and
    # With-Trend EMA200 agreement.
    "PM 5m Balanced": {
        "brick_mode": MODE_PCT, "atr_length": 14, "brick_atr_mult": 0.5,
        "brick_pct": 0.3, "brick_fixed": 100.0,
        "reversal_bricks": 3,
        "trigger": TRIG_REVERSAL, "min_run_bricks": 5, "max_new_bricks": 2,
        "vol_atr_length": 20, "atr_pct_min": 0.05, "atr_pct_max": 0.8,
        "predict_direction": "Fade Brick",
        "use_trend_filter": True, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # 1,154 bets, 60.92% hit — and 62.56% train against 52.41% holdout.
    # NOT RECOMMENDED (caveat 2): the big-brick tier looks like the second-best
    # preset in-sample and is the worst out of it.
    "PM 5m Selective": {
        "brick_mode": MODE_PCT, "atr_length": 14, "brick_atr_mult": 0.5,
        "brick_pct": 1.0, "brick_fixed": 100.0,
        "reversal_bricks": 2,
        "trigger": TRIG_REVERSAL, "min_run_bricks": 5, "max_new_bricks": 1,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": "Fade Brick",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # 328 bets, 63.11% hit; 60.19% on 108 holdout bets. The longest-run tier —
    # fade the brick that breaks a run of 8+ ATR-sized bricks. ~36 bets/year and
    # two ugly partial years; read caveat 3.
    "PM 5m Hi Hit": {
        "brick_mode": MODE_ATR, "atr_length": 14, "brick_atr_mult": 3.0,
        "brick_pct": 0.1, "brick_fixed": 100.0,
        "reversal_bricks": 2,
        "trigger": TRIG_REVERSAL, "min_run_bricks": 8, "max_new_bricks": 0,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": "Fade Brick",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },

    # ---- refitted on the trailing 2 years only (see the block above) --------

    # 1,852 bets over 2024-07-28..2026-07-28 (~1 per 9.5 hours), 56.80% hit,
    # halves 55.41/58.58 — and 57.92% across 2017-2023, unseen by the fit.
    # Roughly double the bets of the full-record Volume tier over this window,
    # at ~1.3pp less per bet.
    "PM 5m Volume - 2yr Train": {
        "brick_mode": MODE_PCT, "atr_length": 14, "brick_atr_mult": 0.5,
        "brick_pct": 0.3, "brick_fixed": 100.0,
        "reversal_bricks": 3,
        "trigger": TRIG_REVERSAL, "min_run_bricks": 2, "max_new_bricks": 2,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": "Fade Brick",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # 784 bets over the window (~1 per 22 hours), 59.69% hit, halves
    # 59.38/60.12 — the steadiest config in this file — and 59.57% across
    # 2017-2023. Beats the full-record Balanced tier on both volume and rate
    # over recent tape. A quiet-tape band replaces the trend filter.
    "PM 5m Balanced - 2yr Train": {
        "brick_mode": MODE_PCT, "atr_length": 14, "brick_atr_mult": 0.5,
        "brick_pct": 0.3, "brick_fixed": 100.0,
        "reversal_bricks": 3,
        "trigger": TRIG_REVERSAL, "min_run_bricks": 5, "max_new_bricks": 2,
        "vol_atr_length": 20, "atr_pct_min": 0.15, "atr_pct_max": 1.0,
        "predict_direction": "Fade Brick",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
}
