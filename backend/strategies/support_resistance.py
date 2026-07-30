"""Support & Resistance — horizontal levels clustered from swing pivots.

Idea
----
The oldest tool on the chart, and the one most often drawn by eye: a horizontal
price at which the market has repeatedly turned. What makes it hard to test is
that it is normally chosen after the fact — you see which line "worked" and draw
that one. Here the levels are built mechanically:

  * every confirmed fractal pivot (high **or** low) is a candidate price,
  * a pivot within ``cluster_tol_atr`` x ATR of an existing level joins it,
    pulling the level to the running mean of its members and incrementing its
    **touch count**; otherwise it starts a new level,
  * only levels with at least ``min_touches`` members are tradeable — "two
    touches make a level" is the textbook rule, and it is a parameter here.

Highs and lows go into the **same** pool on purpose. A level's role is decided
per bar by where price sits: a level above the previous close is resistance, one
below it is support. That is polarity flip — broken resistance becomes support —
falling out of the representation instead of being special-cased. Only the
**nearest** level on each side is evaluated, since a further one cannot be
reached without passing it.

This is deliberately *not* :mod:`trend_lines`, which joins two pivots into one
sloping line and re-anchors as new pivots print. There a level is two points and
lives until it is replaced; here a level is a **cluster** of any size, is scored
by how many swings confirmed it, and is horizontal.

Two events are detected, and each can be traded or ignored:

  * **Break** — a close through the level by more than ``break_buffer_atr``.
    Breaking resistance is bullish, breaking support bearish.
  * **Bounce** — the bar's extreme reaches into the zone (``zone_tol_atr``) but
    the close stays on the approach side: the level held. Bouncing off support
    is bullish, off resistance bearish. The previous bar must not have been
    touching, so a slow grind along a level emits one signal rather than ten.

``predict_direction`` takes that at face value (**With Signal**) or fades it
(**Against Signal**) — worth having, because this repo has repeatedly found the
fade to be the better side of a structure event on BTC 5m.

Look-ahead
----------
A fractal pivot at bar j is not knowable until bar ``j + pivot_right``. Pivots
are admitted through a confirmation cursor that releases a pivot only once the
scan reaches ``j + pivot_right``, so no level is ever built from a swing that
had not yet formed. Everything else a bar reads — its own OHLC, the previous
close, ATR — is already closed. Verified by truncation: re-running on the series
cut at any signal bar reproduces that signal exactly.

Parameter groups
----------------
Pivots        pivot_left, pivot_right
Levels        cluster_tol_atr, min_touches, max_level_age_bars, max_levels,
              retire_on_break, use_support, use_resistance
Trigger       use_break, break_buffer_atr, use_bounce, zone_tol_atr
Decision      predict_direction  (With Signal | Against Signal)
Volatility    vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter  (shared)
Allowed Trading Window (shared)
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_DIRECTIONS = ["With Signal", "Against Signal"]


class SupportResistance(Strategy):
    id = "support_resistance"
    name = "Support & Resistance"
    description = ("Horizontal levels built by clustering confirmed swing pivots "
                   "and scored by touch count. Trades the break through the "
                   "nearest level or the rejection off it — at face value or faded.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Pivots", [
                Param("pivot_left", "Pivot Left Bars", 12, "int", 1, 500, 1,
                      "Bars before a pivot that it must dominate. Larger means "
                      "only significant swings can seed or confirm a level."),
                Param("pivot_right", "Pivot Right Bars", 2, "int", 1, 100, 1,
                      "Bars after a pivot that it must dominate. Also the delay "
                      "before the pivot may be used, so larger is more reliable "
                      "but later."),
            ]),
            ParamGroup("Levels", [
                Param("cluster_tol_atr", "Cluster Tolerance (xATR)", 0.50, "float", 0.0, 10.0, 0.05,
                      "How close a new pivot must be to an existing level to "
                      "count as the same level. Wider tolerance means fewer, "
                      "heavier levels; narrower means many thin ones."),
                Param("min_touches", "Min Touches", 2, "int", 1, 10, 1,
                      "How many clustered pivots a level needs before it is "
                      "tradeable. 2 is the textbook 'two touches make a level'; "
                      "1 makes every swing a level."),
                Param("max_level_age_bars", "Max Level Age (bars)", 500, "int", 1, 20000, 1,
                      "Retire a level this many bars after its most recent "
                      "clustered pivot. Old levels stop meaning anything."),
                Param("max_levels", "Max Active Levels", 12, "int", 1, 100, 1,
                      "Cap on how many levels are tracked at once. When full, "
                      "the weakest (fewest touches, then oldest) is dropped."),
                Param("retire_on_break", "Retire Level On Break", True, "bool",
                      help="On: a level is discarded once broken, so it breaks "
                           "at most once. Off: it survives and flips polarity — "
                           "broken resistance becomes support — so the retest "
                           "can fire as a bounce."),
                Param("use_support", "Use Support Levels", True, "bool",
                      help="Evaluate the nearest level BELOW price."),
                Param("use_resistance", "Use Resistance Levels", True, "bool",
                      help="Evaluate the nearest level ABOVE price."),
            ]),
            ParamGroup("Trigger", [
                Param("use_break", "Trade Level Breaks", True, "bool",
                      help="Fire when a close pierces the level."),
                Param("break_buffer_atr", "Break Buffer (xATR)", 0.10, "float", 0.0, 5.0, 0.05,
                      "How far past the level the close must be to count as a "
                      "break. Filters closes that merely graze it."),
                Param("use_bounce", "Trade Level Bounces", False, "bool",
                      help="Fire when the bar reaches into the level's zone but "
                           "closes back on the approach side — the level held."),
                Param("zone_tol_atr", "Zone Tolerance (xATR)", 0.25, "float", 0.0, 5.0, 0.05,
                      "Half-width of the level's zone: how close the bar's "
                      "extreme must come to count as a touch."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "With Signal", "enum",
                      options=_DIRECTIONS,
                      help="With Signal: resistance break -> LONG, support break "
                           "-> SHORT, support bounce -> LONG, resistance bounce "
                           "-> SHORT. Against Signal inverts all four."),
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
        cluster_tol = p["cluster_tol_atr"]
        min_touches = p["min_touches"]
        max_age = p["max_level_age_bars"]
        max_levels = p["max_levels"]
        retire = bool(p["retire_on_break"])

        use_sup, use_res = bool(p["use_support"]), bool(p["use_resistance"])
        use_break, use_bounce = bool(p["use_break"]), bool(p["use_bounce"])
        break_buf, zone_tol = p["break_buffer_atr"], p["zone_tol_atr"]

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

        # Active levels: {price, touches, last_pivot, touch_bar}. `touch_bar` is
        # the last bar this level was being touched, which is how the bounce rule
        # fires once per approach rather than once per bar.
        levels: List[dict] = []
        cursor = 0                                  # pivot confirmation cursor

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            # --- release newly confirmed pivots into the level set -------------
            # Scan state: must run on every bar, before any `continue`.
            while cursor <= i - piv_right and cursor < n:
                j = cursor
                cursor += 1
                a_j = atr[j]
                if not a_j or a_j <= 0:
                    continue                        # warm-up: no scale to cluster on
                tol = cluster_tol * a_j
                for pv in (piv_hi[j], piv_lo[j]):
                    if pv is None:
                        continue
                    best = None
                    best_d = None
                    for L in levels:
                        d = abs(L["price"] - pv)
                        if d <= tol and (best_d is None or d < best_d):
                            best, best_d = L, d
                    if best is None:
                        levels.append({"price": pv, "touches": 1,
                                       "last_pivot": j, "touch_bar": -1})
                        if len(levels) > max_levels:
                            levels.remove(min(levels, key=lambda L: (L["touches"],
                                                                    L["last_pivot"])))
                    else:
                        t = best["touches"]
                        best["price"] = (best["price"] * t + pv) / (t + 1)
                        best["touches"] = t + 1
                        best["last_pivot"] = j

            a = atr[i]
            if i == 0 or a is None or a <= 0:
                continue
            prev_close = candles[i - 1]["close"]
            cl, hi, lo = c["close"], c["high"], c["low"]

            # --- expire stale levels; pick the nearest tradeable one each side --
            res_L = sup_L = None
            expired = False
            for L in levels:
                if i - L["last_pivot"] > max_age:
                    expired = True
                    continue
                if L["touches"] < min_touches:
                    continue
                v = L["price"]
                if v > prev_close:
                    if use_res and (res_L is None or v < res_L["price"]):
                        res_L = L
                elif v < prev_close:
                    if use_sup and (sup_L is None or v > sup_L["price"]):
                        sup_L = L
            if expired:
                levels = [L for L in levels if i - L["last_pivot"] <= max_age]

            # Evaluate both. `touch_bar` is level state and is updated even when a
            # filter below rejects the bar, so "first touch only" stays honest.
            events = []          # (kind, bias, label, level_value, touches, dist_atr)
            for kind, L in (("resistance", res_L), ("support", sup_L)):
                if L is None:
                    continue
                v = L["price"]
                is_res = kind == "resistance"
                # break: the close pierces the level by the buffer
                pierced = (cl > v + break_buf * a) if is_res else (cl < v - break_buf * a)
                # touch: the bar's extreme reaches the zone, close stays onside
                near = (hi >= v - zone_tol * a) if is_res else (lo <= v + zone_tol * a)
                onside = (cl < v) if is_res else (cl > v)
                was_touching = L["touch_bar"] == i - 1
                if near and onside:
                    L["touch_bar"] = i

                if use_break and pierced:
                    events.append((kind, "bull" if is_res else "bear",
                                   f"{kind} break", v, L["touches"], abs(cl - v) / a))
                    if retire:
                        levels.remove(L)            # a retired level breaks once
                elif use_bounce and near and onside and not was_touching:
                    events.append((kind, "bear" if is_res else "bull",
                                   f"{kind} bounce", v, L["touches"], abs(cl - v) / a))

            if not events:
                continue
            # Both sides firing opposite ways on one bar is a genuine disagreement
            # (price pinched between two levels) -- take neither.
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
                      f"(level {events[0][3]:.1f}, {events[0][4]} touches, "
                      f"{events[0][5]:.2f}xATR away, ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"events": [e[2] for e in events], "bias": bias,
                      "level": round(events[0][3], 2),
                      "touches": events[0][4],
                      "dist_atr": round(events[0][5], 3),
                      "atr_pct": round(atr_pct, 3)},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets — FADE the level break. Read the hit rates against 49.76%, not 50%:
# 0.48% of 5m candles close exactly at their open and lose whichever side you
# take, so the ceiling for any 50/50 bettor is (1 - flat) / 2.
#
# METHOD, the same protocol as Trend Lines and Reversal:
#   TRAIN    2024-01-01 -> 2025-10-01   selection here and only here
#   HOLDOUT  2025-10-01 -> 2026-07-29   scored once, after the pick was frozen
#   UNSWEPT  2018-01-01 -> 2024-01-01   never loaded by any sweep stage
#
# Stage A (36 configs) settled the family before any tuning, and it settled it
# the same way this repo keeps settling structure events:
#
#   break only / Against Signal   52.84% train   <- winner
#   break only / With Signal      47.42% train   <- the mirror; taking the break loses
#
# Bounces lost outright. Every bounce-enabled variant landed between 49.8% and
# 50.8% on train, so the "level held" event carries nothing here and the shipped
# presets disable it. `retire_on_break=True` also won by ~1.8pp over letting a
# broken level survive and flip polarity — once a level goes, it is spent.
#
# Stage B (6,480 configs) tuned pivots, clustering, touches, buffer, level count
# and age. Selection rule, fixed in advance: >= 3,000 train bets, both train
# halves > 50%, pivot_left off the grid boundary, then maximise train hit.
#
# Stage C (1,536 configs) re-ran the winners with max_levels and
# max_level_age_bars extended past the Stage B grid, because both had landed on
# an edge. They turned out not to matter: across the whole rule-passing top 20,
# every value of those two axes lands within 0.9pp. The nominal Stage C winner
# (max_levels=50, age=20000) sat pinned to the new edge at 55.61%, so the tie was
# broken on a non-statistical ground — as the Trend Lines pivot_right tie was —
# by taking the config INTERIOR on both axes. It costs 0.53pp of train hit, about
# 0.6 standard errors at n=3,400, and buys a parameter that is not on a wall.
#
#   preset      bets     hit    edge    train  HOLDOUT  UNSWEPT      z
#   Volume    40,578  56.04%  +6.28pp  54.18%  55.56%   57.59%   +25.3
#   Confirmed 16,772  55.15%  +5.39pp  55.08%  54.39%   55.72%   +14.0
#
# VOLUME DOMINATES, WHICH IS NOT WHAT THE TRAINING WINDOW SAID. Confirmed won on
# train (55.08% vs 54.18%) and is the config the selection rule actually picked;
# out of sample it is the weaker of the two on both axes at once — Volume carries
# 2.4x the bets AND a higher hit rate on the holdout, on the unswept years, and
# over the full record. Train hit rate ranked these two backwards. Both ship,
# and Volume is the pick.
#
# WHY THESE ARE PROBABLY REAL. Three checks, all run after the picks were frozen:
#
#  * The mirror is symmetric. Taking the break instead of fading it scores
#    43.67% / 44.50% — as far below the ceiling as these are above it. A
#    selection artifact would not produce a clean sign flip on the same bets.
#  * No look-ahead. Re-deriving 40 sampled signals per preset on the series
#    truncated AT the signal bar reproduced every one: 0 mismatches.
#  * It is not Trend Lines relabelled. Against that strategy's own presets, only
#    25-30% of these signals are shared (Jaccard 21-24%), and the 28,512 bets
#    Volume fires that Trend Lines never does score 55.70% on their own — the
#    exclusive half carries the edge, so this is a separate signal source rather
#    than a sloping-line result rediscovered horizontally.
#
# It is also not directional beta: bets run 47.2% long / 52.8% short and both
# sides win (long 56.57%, short 55.56%) while 49.6-50.5% of all 5m candles close
# up in every year.
#
# WHERE IT FAILS. 2017 (partial year, Aug-Dec, thin early Binance liquidity) is
# the one losing year for Volume at 44.31%, 2.19pp BELOW its own 46.50% ceiling.
# Every full year 2018-2026 clears, worst 53.94% in 2024 (+4.05pp). This is the
# usual failure mode of a fade — a sustained one-way trend, in which broken
# levels keep going — and it is exactly the year Trend Lines and Reversal fail
# too. Confirmed is the more robust of the two here: it clears its ceiling in
# every year including 2017 (+2.72pp), at 41% of the volume.
#
# THE BUFFER IS NOT A CLEAN DIAL, unlike Trend Lines'. On the full record it dips
# before it recovers, so 0.0 is a genuine peak rather than the low end of a ramp:
#
#   buf   Volume bets    hit  |  buf   Volume bets    hit
#   0.0        40,578  56.04% |  0.8        21,996  55.69%
#   0.1        38,581  55.66% |  1.2        10,655  55.99%
#   0.3        34,818  55.11% |  2.0         2,467  55.45%
#   0.5        30,529  54.85% |
#
# Nothing beats buf=0.0 at any volume, so both presets ship at 0.0. Raising it
# only trades bets away for the same hit rate.
#
# NOT SWEPT: the 1-minute interval. pivot_left counts BARS, so these presets'
# 20/30 are 100-150 *minutes* on 5m and would be 20-30 minutes on 1m — a
# different setup entirely. Trend Lines and Reversal both needed a separate 1m
# sweep for exactly this reason; do that before running these on 1m tape.
_SR_COMMON = {
    # Breaks only. Bounces were swept and lost (see Stage A above).
    "use_break": True, "use_bounce": False, "zone_tol_atr": 0.25,
    "use_support": True, "use_resistance": True,
    "retire_on_break": True,                  # a spent level does not flip
    "break_buffer_atr": 0.0,                  # the peak, not a floor (see dial)
    "max_level_age_bars": 3000,
    "predict_direction": "Against Signal",    # FADE the break
    "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
    "use_trend_filter": False, "trend_logic": "With Trend",
    "ma_type": "EMA", "ma_length": 200, "source": "close",
    "use_trading_window": False,
}

PRESETS: dict = {
    # 40,578 bets, 56.04% vs a 49.76% ceiling (z +25.3); train 54.18%, holdout
    # 55.56%, unswept 57.59%. THE PICK: it beats Confirmed on both bet count and
    # hit rate everywhere except the window it was fitted on. min_touches=1 makes
    # every clustered swing tradeable immediately, which is where the volume
    # comes from; pivot_right=1 keeps the confirmation lag at one bar.
    "PM 5m Level Break Volume": {
        **_SR_COMMON,
        "pivot_left": 30, "pivot_right": 1,
        "cluster_tol_atr": 1.0, "min_touches": 1, "max_levels": 20,
    },
    # 16,772 bets, 55.15% vs a 49.76% ceiling (z +14.0); train 55.08%, holdout
    # 54.39%, unswept 55.72%. The textbook reading — a level needs three touches
    # before it is worth trading — at 41% of the volume. Worth having despite
    # losing to Volume out of sample: it is the only preset here that clears its
    # ceiling in EVERY year, 2017 included.
    "PM 5m Level Break Confirmed": {
        **_SR_COMMON,
        "pivot_left": 20, "pivot_right": 3,
        "cluster_tol_atr": 1.5, "min_touches": 3, "max_levels": 30,
    },
}
