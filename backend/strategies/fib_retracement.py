"""Fibonacci Retracement — trade the pullback inside a measured swing leg.

Idea
----
A trend rarely moves in a straight line: it pushes, pauses, gives some of the
move back, then either resumes or fails. Fibonacci retracement is the standard
way of putting numbers on the "gives some of the move back" part. Take a swing
**leg** (a low-to-high push, or a high-to-low one) and measure how much of it
price has since undone, as a fraction of the leg:

    up-leg   (low -> high):   retracement = (leg_high - close) / (leg_high - leg_low)
    down-leg (high -> low):   retracement = (close - leg_low) / (leg_high - leg_low)

0.0 means price is still at the extreme that ended the leg; 1.0 means the whole
leg has been given back. The canonical levels are 23.6 / 38.2 / 50 / 61.8 / 78.6
percent, and the classic trade is to enter as price pulls back INTO one of them,
betting the leg resumes.

Two honest notes on the method, because they shaped how this is parameterised:

  * **50% is not a Fibonacci number.** It is the plain midpoint, included by
    convention. 61.8% is 1/phi, 38.2% is 1/phi^2, 23.6% is 1/phi^3, 78.6% is
    sqrt(0.618).
  * **There is no mechanism** by which the golden ratio should govern price; the
    usual story is reflexive (enough traders watch 61.8% that orders cluster
    there). So `fib_level` here is a free float, not a dropdown of the five
    blessed values — which lets a sweep ask the only question that matters: does
    0.618 do anything its neighbours at 0.55 or 0.70 don't? (It does not. See the
    preset notes at the bottom of this file.)

Defining the leg
----------------
Discretion is the usual problem with this tool — pick a different swing high and
every level moves, which makes it trivial to fit levels to a chart after the
fact. So the leg is defined mechanically and causally: inside a rolling window of
`swing_lookback` bars ending at the current bar, take the highest high and the
lowest low. Whichever came **last** is the end of the leg, which also gives the
leg its direction. No manual anchoring, no look-ahead, and the resulting
retracement is always in [0, 1] because the close is by construction inside the
window's range.

A leg only counts if it is big enough to be a move rather than noise
(`min_leg_atr`, in ATRs) and spans enough bars (`min_leg_bars`).

Parameter groups
----------------
Swing Leg     swing_lookback, min_leg_atr, min_leg_bars
Fibonacci     fib_level, fib_tolerance, require_fresh_touch
Entry Timing  require_opposing_bar, opposing_bar_min_atr
Volatility    vol_atr_length, atr_pct_min, atr_pct_max
Trend         use_trend_filter, trend_logic, ma_type, ma_length, source
Decision      predict_direction  (Trend Resume | Retrace Deeper)
Day of Week   trade_mon .. trade_sun

Entry logic for an UP-leg (mirror for a down-leg)
-------------------------------------------------
  1. Leg:        highest high came after the lowest low, at least `min_leg_bars`
                 apart and at least `min_leg_atr` x ATR tall.
  2. Zone:       |retracement - fib_level| <= fib_tolerance.
  3. Fresh:      the previous bar was NOT already in that zone on the same leg
                 direction (so one pullback fires once, not once per bar).
  4. Timing:     if `require_opposing_bar`, the signal bar must close AGAINST
                 the bet, by at least `opposing_bar_min_atr` x ATR.
  5. Volatility: ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max].
  6. Trend:      optional moving-average agreement.

**Trend Resume** bets the leg continues (LONG an up-leg's pullback — the textbook
trade); **Retrace Deeper** bets the pullback keeps going (SHORT it). The Vol ATR
sizes TP/SL; in Polymarket up/down mode the exit params are unused and each
signal is simply a bet on the next candle's direction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup, Signal, Strategy

_DAYS = ["trade_mon", "trade_tue", "trade_wed", "trade_thu",
         "trade_fri", "trade_sat", "trade_sun"]  # index == datetime.weekday()

# Saturday + Sunday only; used by the weekend-gated Polymarket presets.
_WEEKEND = {k: (k in ("trade_sat", "trade_sun")) for k in _DAYS}


class FibRetracement(Strategy):
    id = "fib_retracement"
    name = "Fib Retracement"
    description = ("Measures a swing leg mechanically (rolling high/low, whichever "
                   "came last ends the leg) and fires when price pulls back into a "
                   "Fibonacci retracement zone of it — betting either that the leg "
                   "resumes or that the pullback deepens.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Swing Leg", [
                Param("swing_lookback", "Swing Lookback (bars)", 96, "int", 4, 2000, 1,
                      "Window the leg's high and low are taken from; whichever came "
                      "last ends the leg (96 bars = 8h on 5m)."),
                Param("min_leg_atr", "Min Leg Size (xATR)", 2.0, "float", 0.0, 30.0, 0.1,
                      "The leg must be at least this many ATRs tall, so a flat "
                      "stretch of tape is not treated as a swing."),
                Param("min_leg_bars", "Min Leg Length (bars)", 3, "int", 1, 500, 1,
                      "Minimum number of bars between the leg's low and its high."),
            ]),
            ParamGroup("Fibonacci", [
                Param("fib_level", "Retracement Level", 0.618, "float", 0.05, 0.95, 0.001,
                      "Fraction of the leg given back. Canonical: 0.236 / 0.382 / "
                      "0.5 / 0.618 / 0.786 — but any value is allowed, deliberately."),
                Param("fib_tolerance", "Zone Half-Width", 0.05, "float", 0.005, 0.5, 0.005,
                      "Half-width of the zone around the level: a signal needs "
                      "|retracement - level| <= this."),
                Param("require_fresh_touch", "First Touch Only", True, "bool",
                      help="Fire only on the bar that ENTERS the zone, not on every "
                           "bar price spends inside it. Keeps one pullback from "
                           "producing a dozen near-identical bets."),
            ]),
            ParamGroup("Entry Timing", [
                Param("require_opposing_bar", "Signal Bar Opposes Bet", False, "bool",
                      help="Only take the bet when the signal bar itself closed against "
                           "it — for a Trend Resume long, the bar must still be pulling "
                           "back. Skips entries where the turn already happened."),
                Param("opposing_bar_min_atr", "Min Opposing Body (xATR)", 0.0, "float",
                      0.0, 3.0, 0.05,
                      "Also require that opposing bar's body be at least this multiple "
                      "of the Vol ATR. 0 accepts any opposing bar. Needs the box above."),
            ]),
            ParamGroup("Volatility Filter", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also measures the leg and sizes TP/SL (xATR)."),
                Param("atr_pct_min", "ATR% Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR% Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent regime)."),
            ]),
            ParamGroup("Trend Filter", [
                Param("use_trend_filter", "Use Trend Filter?", False, "bool",
                      help="Require price to agree with a moving-average trend."),
                Param("trend_logic", "Trend Logic", "With Trend", "enum",
                      options=["With Trend", "Against Trend"],
                      help="With Trend: long above / short below the MA. Against Trend: the opposite."),
                Param("ma_type", "MA Type", "EMA", "enum", options=ind.MA_TYPES,
                      help="Moving-average type for the trend filter."),
                Param("ma_length", "MA Length", 200, "int", 2, 1000, 1,
                      "Lookback for the trend MA."),
                Param("source", "Source", "close", "enum", options=ind.SOURCES,
                      help="Price source for the trend MA."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Trend Resume", "enum",
                      options=["Trend Resume", "Retrace Deeper"],
                      help="Trend Resume bets the leg continues after the pullback "
                           "(the textbook Fibonacci trade); Retrace Deeper bets the "
                           "pullback carries on instead."),
            ]),
            ParamGroup("Day of Week (UTC)", [
                Param(_DAYS[i], lbl, True, "bool",
                      help=f"Allow entries on {lbl} (UTC). See the Polymarket "
                           f"presets — the weekend premium here is real but modest.")
                for i, lbl in enumerate(("Monday", "Tuesday", "Wednesday",
                                         "Thursday", "Friday", "Saturday",
                                         "Sunday"))
            ]),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        # Day gate (UTC). Index matches datetime.weekday(): Monday == 0.
        allowed_days = {i for i in range(7) if p[_DAYS[i]]}
        gate_days = len(allowed_days) < 7

        atr_vol = ind.atr(candles, p["vol_atr_length"])
        hh, ll, hi_i, lo_i = ind.rolling_swing(candles, p["swing_lookback"])

        min_leg = p["min_leg_atr"]
        min_bars = p["min_leg_bars"]
        level = p["fib_level"]
        tol = p["fib_tolerance"]

        # Pass 1: the swing leg and how far it has retraced, per bar. This is a
        # property of price structure alone, so it is computed for EVERY bar —
        # the first-touch test below compares against the true previous bar, not
        # against the previous bar that happened to survive the other filters.
        legs: List = [None] * n        # 'up' | 'down' | None
        retrs: List = [None] * n       # retracement fraction in [0, 1]
        for i in range(n):
            a = atr_vol[i]
            top, bot, ti, bi = hh[i], ll[i], hi_i[i], lo_i[i]
            if a is None or a <= 0 or top is None or ti == bi:
                continue
            span = top - bot
            if span <= 0 or span < min_leg * a or abs(ti - bi) < min_bars:
                continue
            if ti > bi:                                  # high came last -> up-leg
                legs[i] = "up"
                retrs[i] = (top - candles[i]["close"]) / span
            else:                                        # low came last -> down-leg
                legs[i] = "down"
                retrs[i] = (candles[i]["close"] - bot) / span

        # Pass 2: which bars sit inside the retracement zone.
        in_zone = [r is not None and abs(r - level) <= tol for r in retrs]

        use_trend = p["use_trend_filter"]
        trend_ma = ind.ma(ind.price_source(candles, p["source"]),
                          p["ma_type"], p["ma_length"]) if use_trend else [None] * n

        fresh_only = p["require_fresh_touch"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        with_trend = p["trend_logic"] == "With Trend"
        resume = p["predict_direction"] == "Trend Resume"
        opposing_bar = p["require_opposing_bar"]
        opposing_min = p["opposing_bar_min_atr"]

        # Pass 3: apply the tradeable filters and emit.
        signals: List[Signal] = []
        for i, c in enumerate(candles):
            if not in_zone[i]:
                continue
            # First touch: the previous bar must not already have been in the
            # zone on the same leg. A leg that flips direction counts as fresh.
            if fresh_only and i > 0 and in_zone[i - 1] and legs[i - 1] == legs[i]:
                continue
            if gate_days and datetime.fromtimestamp(
                    c["time"], timezone.utc).weekday() not in allowed_days:
                continue

            a = atr_vol[i]
            cl = c["close"]
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            leg = legs[i]
            side = ("long" if leg == "up" else "short") if resume \
                else ("short" if leg == "up" else "long")

            # The bar must still be moving against the bet — once it has turned
            # our way the move we wanted to join is already under way.
            body = cl - c["open"]
            if opposing_bar:
                if side == "long" and body >= 0:
                    continue
                if side == "short" and body <= 0:
                    continue
                if opposing_min > 0 and abs(body) < opposing_min * a:
                    continue

            if use_trend:
                tm = trend_ma[i]
                if tm is None:
                    continue
                above = cl > tm
                agree = (side == "long" and above) or (side == "short" and not above)
                if with_trend and not agree:
                    continue
                if not with_trend and agree:
                    continue

            r = retrs[i]
            mode = "resume" if resume else "deeper"
            reason = (f"{leg.upper()}-leg retraced {r * 100:.1f}% "
                      f"(zone {level * 100:.1f}%±{tol * 100:.1f}) "
                      f"-> {mode} {side.upper()} (ATR% {atr_pct:.2f})")

            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"retrace": round(r, 4), "leg": leg,
                      "leg_atr": round((hh[i] - ll[i]) / a, 2),
                      "leg_bars": abs(hi_i[i] - lo_i[i]),
                      "atr_pct": round(atr_pct, 3), "mode": mode,
                      "bar_body_atr": round(body / a, 2)},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m). Exit / Backtest params are unused in that mode.
#
# Sweep: BTCUSDT 1m resampled to 5m over the entire DB — 938,857 bars,
# 2017-08-17 .. 2026-07-26 — in three stages totalling 10,800 combinations
# (2,400 structural + 4,500 geometric + 3,900 filter). Every combination was
# scored by calling this module's own `generate_signals`, so a preset cannot
# drift from what the dashboard runs.
#
# Parameters were selected on **2017-2023 only**; 2024-2026 was scored
# afterwards and never consulted while choosing anything. Admission: win every
# calendar year carrying >= 25 bets, binomial z >= 2.5 on train, and both halves
# of train (2017-2020, 2021-2023) independently clear 52%.
#
# Measured results (whole DB, flat $1 per bet)
# --------------------------------------------
#   preset      bets     hit   train 17-23   TEST 24-26  2025-26  worst yr    z
#   Volume    18,205  55.31%      56.46%       52.36%     51.86%   51.41%   14.3
#   Balanced   7,338  58.16%      58.86%       56.66%     56.32%   50.75%   14.0
#   Selective  3,843  57.69%      60.41%       53.63%     53.31%   51.89%    9.5
#   Hi Hit     1,132  59.45%      62.77%       53.09%     54.66%   50.66%    6.4
#
# **Balanced is the preset to use.** It is the only tier that survives the
# holdout intact — 56.66% across 2,328 out-of-sample bets, still 56.32% over
# 2025-26, every calendar year at or above 50.75%. The other three are shipped
# for the frontier they trace, not because they are recommended; read the
# shrinkage column before using them.
#
# THE HEADLINE FINDING: THE FIBONACCI RATIOS EARN NOTHING
# -------------------------------------------------------
# The `fib_level` grid deliberately interleaved the canonical ratios with
# non-canonical neighbours. Hit rate is **monotone in retracement depth** and
# shows no bump whatsoever at the golden-ratio values. Pooled over stage 1,
# Trend Resume, with each level also compared against the mean of its two grid
# neighbours holding every other parameter fixed:
#
#     level     train hit    vs neighbours       level    train hit   vs neighbours
#   * 0.236       49.43%        -1.07pp            0.55      52.40%      -0.06pp
#     0.30        50.49%        +0.12pp          * 0.618     52.82%      -0.09pp
#   * 0.382       51.43%        +0.28pp            0.70      53.73%      +0.24pp
#     0.45        51.98%        +0.21pp          * 0.786     54.78%      +0.45pp
#   * 0.50        52.21%        +0.04pp            0.85      55.61%      +0.83pp
#                                          (* = canonical Fibonacci level)
#
# 0.618 — the golden ratio, the one level every chartist watches — comes in
# 0.09pp BELOW the average of its neighbours at 0.55 and 0.70. The two largest
# positive deviations sit at the edge of the grid, where the monotone trend has
# nothing to be averaged against on one side. There is no level effect here;
# `fib_level` is a depth knob wearing a Fibonacci hat. Stage 2 pushed the grid
# to the 0.95 ceiling and found the curve flattens and turns over: 0.85 =
# 55.88%, 0.90 = 55.76%, 0.95 = 55.55%. Depth pays until price has given back
# ~85% of the leg, then stops.
#
# BUT THE LEG DOES EARN ITS KEEP
# -------------------------------
# A deep retracement means the close sits near the far end of the window's
# range — which is what Williams %R measures with no Fibonacci and no leg. So
# the leg gate was tested directly against a matched control that keeps the
# window, leg-size floor, zone, first-touch, opposing-bar and volatility rules
# and removes ONLY the requirement that the swing be intact (high after low, no
# new low since):
#
#   config                              fib bets   fib hit   ctrl bets  ctrl hit      z
#   lb=24  leg=3 lvl=0.85  tol=0.05       13,365    54.96%     74,025    54.01%   +2.03
#   lb=24  leg=3 lvl=0.95  tol=0.05        5,010    54.83%     71,056    55.06%   -0.31
#   lb=48  leg=2 lvl=0.618 tol=0.05       27,505    52.98%     56,071    52.11%   +2.38
#   lb=144 leg=3 lvl=0.85  tol=0.02        3,595    56.75%     26,668    53.46%   +3.71
#
# So the structural half of the idea is real and the arithmetic half is not.
# What this strategy actually trades is "price rallied, gave nearly all of it
# back, but held above the prior low — buy that retest", and the leg definition
# is what encodes "held above the prior low". Worth ~1-3pp over the leg-blind
# version; the ratios are worth 0.
#
# Other findings
# --------------
#   * **Trend Resume only.** All 50 of the top-50 training configs bet the leg
#     resumes; pooled, Trend Resume 51.73% vs Retrace Deeper 49.40%. The
#     textbook direction is the right one — which also makes this a
#     mean-reversion strategy at the 5m scale (buying a pullback = fading the
#     most recent move), consistent with every other strategy in this repo.
#   * **`require_opposing_bar` is the single most valuable filter**, exactly as
#     in Multi Horizon: pooled across stage 1, ON 52.51% vs OFF 49.75%, two-
#     proportion z = +189. Demanding a real body on that bar helps further and
#     monotonically — 0.0 / 0.25 / 0.50 / 0.75 xATR give 54.94 / 55.26 / 55.79 /
#     56.01%. Every preset below uses it.
#   * **"Against Trend" helps**, matching Volume Exhaustion and opposing Multi
#     Horizon. Pooled over stage 3: Against Trend SMA100 57.74%, filter off
#     57.03%, With Trend EMA50 54.51%. Combined with Trend Resume it means:
#     buy the pullback in an up-leg while price is *below* the MA.
#   * **No weekend gate.** Held fixed and split by UTC day, the weekend premium
#     is +0.43 / +2.32 / -2.06pp on Balanced / Selective / Hi Hit (all
#     |z| < 1.4). Only Volume shows anything recent (+4.70pp, z=+2.55 over
#     2024-26) and its full-record split is +1.21pp at z=+1.41 — one nominal
#     result out of eight comparisons. No weekend presets are shipped.
#
# Caveats, in order of how much they should worry you
# ----------------------------------------------------
# 1. **Shrinkage scales with training hit rate, steeply.** Ranked by train hit,
#    the four tiers lose 2.2 / 4.1 / 6.8 / 9.7 points out-of-sample, and the
#    ordering is exactly inverted: the best-looking preset on 2017-2023 (Hi Hit,
#    62.77%) is the worst on 2024-2026 (53.09%). Treat any in-sample number on
#    this strategy as an upper bound, and prefer the higher-volume tiers.
# 2. **Train-internal stability did NOT predict survival.** Configurations that
#    scored 63.7% and 66.4% on the two halves of the training span — as stable
#    as it is possible to look without a holdout — still collapsed to 50.0% on
#    2024-2026. The half-split admission rule below is kept because it costs
#    nothing, but it should not be mistaken for evidence. Only the real holdout
#    separated the tiers.
# 3. **Selective and Hi Hit are not recommended.** Both clear the admission
#    rules and both fall apart out-of-sample (-6.8 and -9.7pp). Hi Hit runs
#    ~110 bets/year and its 2024 is 50.66%. They are shipped so the frontier is
#    visible, not as picks.
# 4. **Volume decays.** 56.46% on train, 52.36% on the holdout, 51.86% over
#    2025-26 — the trend is one direction. Its edge over breakeven is now thin
#    enough that fees or an unfavourable entry price would erase it.
# 5. Days are UTC and a bar is stamped by its open time.
#
# A bet pays only when hit rate > your odds: Balanced's 56.66% needs entry below
# ~0.5666 to be +EV.
PRESETS: dict = {
    # 18,205 bets, 55.31% hit. The widest net, and the weakest edge — 52.36%
    # out-of-sample and 51.86% over 2025-26. Use only if bet count matters more
    # than margin, and check your entry price carefully.
    "PM 5m Volume": {
        "swing_lookback": 24, "min_leg_atr": 2.0, "min_leg_bars": 3,
        "fib_level": 0.786, "fib_tolerance": 0.1, "require_fresh_touch": True,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.5,
        "vol_atr_length": 50, "atr_pct_min": 0.1, "atr_pct_max": 2.0,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Trend Resume",
    },
    # *** THE PICK. *** 7,338 bets, 58.16% hit; 56.66% across 2,328
    # out-of-sample bets and 56.32% over 2025-26, worst year 50.75% (2017, a
    # partial year on 268 bets — every other year is at or above 54.8%). Loses only
    # 2.2pp from train to holdout, the smallest shrinkage of the four, and its
    # z of 14.0 rests on real volume rather than a lucky tail.
    "PM 5m Balanced": {
        "swing_lookback": 72, "min_leg_atr": 6.0, "min_leg_bars": 1,
        "fib_level": 0.85, "fib_tolerance": 0.1, "require_fresh_touch": True,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.5,
        "vol_atr_length": 50, "atr_pct_min": 0.1, "atr_pct_max": 2.0,
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "SMA", "ma_length": 50, "source": "close",
        "predict_direction": "Trend Resume",
    },
    # 3,843 bets, 57.69% hit — but 60.41% on train against 53.63% on the
    # holdout. NOT RECOMMENDED (caveat 3): it looked like the second-best preset
    # in-sample and is the second-worst out of it.
    "PM 5m Selective": {
        "swing_lookback": 48, "min_leg_atr": 6.0, "min_leg_bars": 3,
        "fib_level": 0.85, "fib_tolerance": 0.1, "require_fresh_touch": True,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.75,
        "vol_atr_length": 50, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "EMA", "ma_length": 50, "source": "close",
        "predict_direction": "Trend Resume",
    },
    # 1,132 bets, 59.45% hit — the highest headline number here and the least
    # trustworthy one. 62.77% train vs 53.09% holdout is the worst shrinkage of
    # the four, 2024 came in at 50.66%, and ~110 bets/year gives it no room to
    # prove otherwise. NOT RECOMMENDED; kept to show where the frontier ends.
    "PM 5m Hi Hit": {
        "swing_lookback": 72, "min_leg_atr": 6.0, "min_leg_bars": 3,
        "fib_level": 0.786, "fib_tolerance": 0.02, "require_fresh_touch": True,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.75,
        "vol_atr_length": 50, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "SMA", "ma_length": 200, "source": "close",
        "predict_direction": "Trend Resume",
    },
    # -------------------------------------------------------------------------
    # 15-MINUTE preset, fitted on the latest six months of BTCUSDT 15m under
    # the protocol the Reversal, Oscillators and Gann 15m presets use:
    # LOADED 2026-03-13 -> 2026-09-13 (train 03-13 -> 07-13 for selection,
    # holdout 07-13 -> 09-13 scored once after the pick was frozen);
    # UNLOADED 2017-08-17 -> 2026-03-13, never read by any sweep stage and
    # scored once at the end as the real out-of-sample check (7,824 bets
    # there against 479 in the window). The selection rule was fixed before
    # tuning: train bets >= 300, both train halves above 52%, every swept
    # parameter off its grid edge, then highest train hit — read against the
    # marginals, since at a few hundred bets a config the SE is 1.5-2.5pp
    # and the single best row is mostly noise.
    #
    # SWEEP. One stage of 1,200 configs raced Trend Resume/Retrace Deeper x
    # swing_lookback {8..72} x min_leg_atr {2, 4, 6} x fib_level {0.382,
    # 0.5, 0.618, 0.786, 0.85} x tolerance {0.05, 0.1} x opposing bar on/off
    # x trend filter {off, Against SMA50}; a second pass (~10) tried the
    # opposing-bar size, the fresh-touch rule, min_leg_bars and the ATR band
    # on the frozen pick.
    #
    # FOUND. Trend Resume is the family (pooled train 52.91% vs 48.66% for
    # Retrace Deeper) and it is a thin one on 15m: the pooled numbers barely
    # clear 52%, and the edge lives at the deep levels — the fib_level
    # marginal is monotone (0.382: 50.88% train / 50.62% holdout; 0.618:
    # 53.09 / 52.14; 0.786: 56.19 / 54.66) as it was on 5m. The swing needs
    # to be long (lookback 48 = 12 h beats 8-24 by 1-4pp on both windows)
    # and the opposing-bar entry gate adds 2.3pp on train. The rule's row
    # sits at 0.618 — the golden ratio itself, the one level with a story
    # behind it — on a 48-bar swing of at least 4 ATR, entered on a bar
    # still pushing against the bet: 479 bets at 56.78% in the window and
    # 56.57% on 7,824 unloaded bets, the most bets of anything in the family
    # that holds out of sample. The 0.786 rows score higher in the window
    # (59.07% on 430 bets at lookback 24) but not out of it (56.69%); the
    # Against-Trend SMA50 filter the 5m Balanced carries is worth +0.5pp
    # unloaded on this row and costs a quarter of the bets, so it is off;
    # the ATR band and fresh-touch rule are inert.
    #
    # RESULTS — flat $1 per bet, next-candle direction
    #   preset             6m bets  6m hit   train   HOLDOUT   unloaded 8.5y             worst yr
    #   PM 15m Balanced       479  56.78%  57.37%   55.62%    56.57% (7,824, z +11.6)  54.14% (2018)
    #
    # Per year on the full record, none of it fitted except the last six months:
    #   2017  54.22% (249)       2018  54.14% (809)       2019  57.70% (941)       2020  57.60% (908)
    #   2021  55.31% (875)       2022  55.31% (960)       2023  59.51% (1,072)     2024  56.62% (906)
    #   2025  56.61% (931)       2026  56.13% (652)
    #
    # Train halves 58.54% / 56.13%. About 2.6 bets a day. The 18 months
    # right before the window (2024-09 -> 2026-03) score 56.27% on 1,395
    # bets; whole record 8,303 bets, 56.58%, z +12.0. Read the hit rates
    # against 49.9%: 0.13% of 15m candles close exactly at their open.
    # Checks after the pick was frozen: the prefix (no look-ahead) test
    # passes with 0 mismatches at three cut points; bets run 52% long in the
    # window and both sides win (window long 59.68% / short 53.68%; unloaded
    # 56.67% / 56.47%); the mirror on the same settings scores 57.05% in the
    # window and 54.47% unloaded.
    #
    # WHERE IT FAILS. The worst month in the window is 2026-09 at 46.2% on
    # 39 bets; the worst full year 2018 at 54.14%. Thin: ~2.7 bets a day,
    # and the window months swing widely. 2018 is the worst full year at
    # 54.1%, 2021 and 2022 at 55.3%; 2026-09 opened at 46% on 39 bets. The
    # 0.50-odds EV the dashboard prints assumes a fill at even; a real 15m
    # book prices away from it. Hit rate is the finding.
    #
    # NOT SHIPPED. 0.786 at lookback 24 without the opposing bar: 430 window
    # bets at 59.07% (train 61.69, holdout 55.03), 56.69% unloaded. 0.618
    # with Against Trend SMA50: 380 at 55.79%, 57.14% unloaded. PM 5m
    # Balanced carried over as-is (lookback 72, 0.85, Against SMA50): 148
    # window bets, 46.15% holdout — the 5m geometry does not transfer. PM 5m
    # Volume as-is: 369 at 58.54%, 55.96% unloaded.
    # 479 bets, 56.78% hit on 2026-03..09; unloaded 2017-08..2026-03 56.57% on
    # 7,824 bets (z +11.6). The 0.618 retracement of a 12-hour swing, entered
    # on a bar still pushing against the bet.
    "PM 15m Balanced": {
        "swing_lookback": 48, "min_leg_atr": 4.0, "min_leg_bars": 1,
        "fib_level": 0.618, "fib_tolerance": 0.1, "require_fresh_touch": True,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.5,
        "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": False, "trend_logic": "Against Trend",
        "ma_type": "SMA", "ma_length": 50, "source": "close",
        "predict_direction": "Trend Resume",
    },
}
