"""Strategy #8 from the video: Jump Exhaustion  ("fade the overshoot").

Idea
----
An abnormal, discontinuous bar (a "jump" — a Levy-style gap) tends to overshoot.
When a jump pushes price to a local extreme *and* shows an intrabar rejection
wick *and* momentum is stretched (RSI), the move is often exhausted, so we fade
it (short an up-jump, long a down-jump) and target a small mean reversion.

Day of week
-----------
Unlike the other strategies here, this one turned out to care *when* the jump
happens. Faded jumps resolve better on some UTC weekdays than others, and the
gap is not a price artifact — raw 5m bar direction is 49.8-50.2% on every day of
the week, so the difference lives in the setups, not the tape. `trade_mon` ...
`trade_sun` gate which days may fire; all seven are on by default. See the notes
above PRESETS for the evidence and the size of the effect.

Parameter groups (matching the config shown in the video)
---------------------------------------------------------
Core        atr_length, jump1_atr_mult, jump2_atr_mult
Candle      close_extreme_min, wick_min_ratio
RSI         rsi_length, rsi_overbought, rsi_oversold
Volatility  vol_atr_length, atr_pct_min, atr_pct_max
Day of Week trade_mon .. trade_sun  (UTC)

Entry logic for an UP-jump (mirror for a down-jump)
---------------------------------------------------
  1. Jump size:  bar_range / ATR(atr_length) must be in [jump1_atr_mult,
     jump2_atr_mult].  jump1 = "is this a jump?";  jump2 = "...but not a monster
     breakout we shouldn't fade."  (The upper bound encodes the lesson that on
     the biggest moves price keeps going instead of reverting.)
  2. Close extreme:  the close sits in the top `close_extreme_min` fraction of
     the last `atr_length` closes -> price pushed to a fresh local high.
  3. Rejection wick:  upper wick / bar range >= wick_min_ratio -> intrabar the
     spike went even higher and got sold, i.e. an overshoot.
  4. Momentum stretched:  RSI(rsi_length) >= rsi_overbought.
  5. Volatility regime:  ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max]
     -> skip dead tape (no follow-through) and violent regimes (jumps trend).

All of 1-5 must hold -> emit a SHORT signal (fade). Down-jump is the mirror:
close near local low, lower wick, RSI <= rsi_oversold -> LONG.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup, Signal, Strategy

_DAYS = ["trade_mon", "trade_tue", "trade_wed", "trade_thu",
         "trade_fri", "trade_sat", "trade_sun"]  # index == datetime.weekday()


class JumpExhaustion(Strategy):
    id = "jump_exhaustion"
    name = "Jump Exhaustion"
    description = ("Fade abnormal (jump) candles that overshoot to a local "
                   "extreme with a rejection wick and stretched RSI.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Core", [
                Param("atr_length", "ATR length", 14, "int", 2, 200, 1,
                      "Lookback for the ATR used to size jumps."),
                Param("jump1_atr_mult", "Jump1 ATR mult (min)", 1.8, "float", 0.5, 10, 0.1,
                      "Minimum bar range in ATRs to count as a jump."),
                Param("jump2_atr_mult", "Jump2 ATR mult (max)", 4.0, "float", 1.0, 20, 0.1,
                      "Maximum bar range in ATRs; bigger moves are NOT faded."),
            ]),
            ParamGroup("Candle", [
                Param("close_extreme_min", "Close extreme min", 0.55, "float", 0.0, 1.0, 0.01,
                      "How far (0-1) the close must be toward the local high/low "
                      "over the ATR-length window."),
                Param("wick_min_ratio", "Wick min ratio", 0.30, "float", 0.0, 1.0, 0.01,
                      "Minimum rejection wick as a fraction of the bar range."),
            ]),
            ParamGroup("RSI", [
                Param("rsi_length", "RSI length", 14, "int", 2, 100, 1,
                      "Lookback for RSI."),
                Param("rsi_overbought", "RSI overbought", 68, "float", 50, 100, 1,
                      "Up-jumps only fade above this RSI."),
                Param("rsi_oversold", "RSI oversold", 32, "float", 0, 50, 1,
                      "Down-jumps only fade below this RSI."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR length", 20, "int", 2, 200, 1,
                      "Lookback for the regime ATR (as % of price)."),
                Param("atr_pct_min", "ATR% min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR% max", 1.2, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent, trending regime)."),
            ]),
            ParamGroup("Day of Week (UTC)", [
                Param(f"trade_{d}", lbl, True, "bool",
                      help=f"Allow signals on {lbl} (UTC). Faded jumps do not "
                           f"resolve equally well on every weekday — see the "
                           f"Polymarket presets.")
                for d, lbl in (("mon", "Monday"), ("tue", "Tuesday"),
                               ("wed", "Wednesday"), ("thu", "Thursday"),
                               ("fri", "Friday"), ("sat", "Saturday"),
                               ("sun", "Sunday"))
            ]),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        atr_len = p["atr_length"]
        jmin, jmax = p["jump1_atr_mult"], p["jump2_atr_mult"]
        ce_min = p["close_extreme_min"]
        wick_min = p["wick_min_ratio"]
        rsi_len = p["rsi_length"]
        ob, os = p["rsi_overbought"], p["rsi_oversold"]
        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        # Day gate (UTC). Index matches datetime.weekday(): Monday == 0.
        allowed_days = {i for i in range(7) if p[_DAYS[i]]}
        gate_days = len(allowed_days) < 7

        atr = ind.atr(candles, atr_len)
        atr_vol = ind.atr(candles, vol_len)
        rsi = ind.rsi(candles, rsi_len)
        clo, chi = ind.rolling_close_extremes(candles, atr_len)

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            if gate_days and datetime.fromtimestamp(
                    c["time"], timezone.utc).weekday() not in allowed_days:
                continue
            a, av, r = atr[i], atr_vol[i], rsi[i]
            lo, hi = clo[i], chi[i]
            if None in (a, av, r, lo, hi) or a <= 0:
                continue

            o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
            rng = h - l
            if rng <= 0:
                continue

            jump = rng / a
            if jump < jmin or jump > jmax:
                continue

            atr_pct = av / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            span = hi - lo
            if span <= 0:
                continue
            pos = (cl - lo) / span  # 1 = fresh local high close, 0 = fresh low

            up_wick = h - max(o, cl)
            dn_wick = min(o, cl) - l

            if cl > o:  # bullish up-jump -> fade SHORT
                if pos < ce_min:
                    continue
                if up_wick / rng < wick_min:
                    continue
                if r < ob:
                    continue
                side = "short"
                reason = (f"Up-jump {jump:.1f}xATR faded "
                          f"(RSI {r:.0f}, wick {up_wick / rng:.0%}, ATR% {atr_pct:.2f})")
            elif cl < o:  # bearish down-jump -> fade LONG
                if (1.0 - pos) < ce_min:
                    continue
                if dn_wick / rng < wick_min:
                    continue
                if r > os:
                    continue
                side = "long"
                reason = (f"Down-jump {jump:.1f}xATR faded "
                          f"(RSI {r:.0f}, wick {dn_wick / rng:.0%}, ATR% {atr_pct:.2f})")
            else:
                continue

            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl,
                reason=reason, atr=a,
                meta={"jump_atr": round(jump, 2), "rsi": round(r, 1),
                      "atr_pct": round(atr_pct, 3), "close_pos": round(pos, 2)},
            ))
        return signals



# ---------------------------------------------------------------------------
# Presets. The two originals are the video's settings; the three "PM 5m" ones
# are tuned for **Polymarket up/down** mode (interval 5m), where exit params are
# unused and only the next candle's direction matters.
#
# The day-of-week question
# ------------------------
# Faded jumps do not resolve equally well on every UTC weekday, and **Saturday is
# the outlier**. Because "best of 7 days" always produces a winner, that claim
# was tested four ways before any parameter was tuned on it:
#
#   1. Control. Raw 5m bar direction carries no day bias at all — P(close>open)
#      is 49.82 / 49.91 / 50.21 / 49.79 / 49.94 / 49.87 / 49.79 % Mon..Sun over
#      all 936,829 bars. The effect lives in the setups, not in the tape.
#   2. Persistence. On the video's Aggressive preset (32,714 bets) Saturday beats
#      the other six days in NINE of ten calendar years — only 2026, a partial
#      year, is negative (-0.19pp). Overall +2.65pp, two-proportion z = +3.19.
#   3. Permutation. Shuffling the day labels 2,000 times, the best day looks this
#      good by chance in 0.1% of draws (p = 0.001). Chi-square 18.4 on 6 df.
#   4. Out-of-sample. Saturday picked on 2017-2023 alone, then scored on
#      2024-2026: 56.67% against a 54.51% all-days baseline there.
#
# Tuesday and Friday also look good in-sample and do NOT survive step 4 — tier
# winners that included them fell from ~61% on 2017-2023 to ~55% on 2024-26,
# while the Saturday-only picks held. That contrast is the whole reason the
# presets below are Saturday-only rather than "the best three days".
#
# With the parameters of PM 5m Sat Hi Hit, hit rate by day over the full history:
#
#     Mon 55.1%   Tue 57.2%   Wed 57.7%   Thu 56.5%
#     Fri 56.6%   Sat 60.4%   Sun 57.8%
#
# Sweep: 10,368 parameter combinations x 127 day-subsets. Parameters AND days
# were chosen on 2017-2023 only; 2024-2026 was scored afterwards and never used
# to select, so the TEST column below is genuinely out-of-sample. Admission:
# win every calendar year, z >= 2.5 on the training span.
#
# Measured results (whole DB, flat $1 per bet)
# --------------------------------------------
#   preset            bets     hit    train 17-23   TEST 24-26   worst yr    z
#   Sat Hi Hit       3,356  60.31%      60.67%        59.46%      53.24%   11.9
#   Sat Volume       6,325  59.19%      60.14%        56.97%      50.78%   14.6
#   All Days        29,185  57.20%      58.14%        55.03%      52.96%   24.6
#
# **Sat Hi Hit is the best recent hit rate in this repo** — 59.46% across 1,004
# out-of-sample bets, with every year from 2017 to 2026 landing between 53.2% and
# 64.9%. It gives up volume for it: Saturday is one day in seven, so the ceiling
# is ~470 bets a year. All Days is the same parameter set with the gate open, and
# is there to show what the day filter is worth (+3.1pp) and for when you want
# the bet count more than the edge.
#
# Two things the sweep rejected
# -----------------------------
#   * **The rejection wick earns nothing.** Every winning combination sets
#     wick_min_ratio = 0. The wick is the most intuitive part of the video's
#     setup and it does not survive measurement on 5m BTC.
#   * **So does the ATR% regime filter** — the top combinations run it wide open
#     (0 to 20). What does the work is the jump size floor plus stretched RSI.
#
# Caveats
# -------
# 1. Saturday-only is ~1/7 of the opportunity. If you need volume, All Days still
#    clears 55% over 2024-26 on 8,809 bets.
# 2. The edge decays here too: Sat Hi Hit runs ~60-65% in 2018-2023 against
#    ~59% in 2024-2026. Read the TEST column, not the headline.
# 3. 2017 is a partial year (Aug-Dec) and the thinnest sample on every preset.
# 4. Days are **UTC**, and the bar is stamped by its OPEN time. A different
#    timezone will not reproduce these numbers.
# 5. Why Saturday and not, say, Sunday is not explained by anything measured
#    here. Thin weekend books are the obvious guess, but Sunday is only middling
#    (57.8%), so treat the mechanism as unknown and the effect as empirical.
#
# A bet pays only when hit rate > your odds: Sat Hi Hit's 59.46% needs entry
# below ~0.5946.
_SAT_ONLY = {"trade_mon": False, "trade_tue": False, "trade_wed": False,
             "trade_thu": False, "trade_fri": False, "trade_sat": True,
             "trade_sun": False}

PRESETS: dict = {
    # The video's settings, kept for reference. Not tuned for Polymarket mode.
    "Aggressive": {
        "jump1_atr_mult": 1.3, "jump2_atr_mult": 5.0,
        "close_extreme_min": 0.45, "wick_min_ratio": 0.20,
        "rsi_overbought": 62, "rsi_oversold": 38,
    },
    "Conservative": {
        "jump1_atr_mult": 2.2, "jump2_atr_mult": 3.5,
        "close_extreme_min": 0.7, "wick_min_ratio": 0.4,
        "rsi_overbought": 72, "rsi_oversold": 28,
    },
    # Highest hit rate in the repo over 2024-26, and that span is out-of-sample.
    # No upper jump bound, no wick test, no ATR% window — a 1.3xATR jump with RSI
    # past 70/30, on Saturdays.
    "PM 5m Sat Hi Hit": {
        "atr_length": 20, "jump1_atr_mult": 1.3, "jump2_atr_mult": 20.0,
        "close_extreme_min": 0.0, "wick_min_ratio": 0.0,
        "rsi_length": 14, "rsi_overbought": 70, "rsi_oversold": 30,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        **_SAT_ONLY,
    },
    # Nearly twice the bets for ~1.1pp of hit rate: a looser RSI gate (65/35) and
    # an upper jump bound back at 5xATR. Still Saturdays only.
    "PM 5m Sat Volume": {
        "atr_length": 20, "jump1_atr_mult": 1.3, "jump2_atr_mult": 5.0,
        "close_extreme_min": 0.0, "wick_min_ratio": 0.0,
        "rsi_length": 14, "rsi_overbought": 65, "rsi_oversold": 35,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        **_SAT_ONLY,
    },
    # Sat Hi Hit's parameters with every day enabled — 8.7x the bets at -3.1pp.
    # Use it to see what the day gate is actually buying.
    "PM 5m All Days": {
        "atr_length": 20, "jump1_atr_mult": 1.3, "jump2_atr_mult": 20.0,
        "close_extreme_min": 0.0, "wick_min_ratio": 0.0,
        "rsi_length": 14, "rsi_overbought": 70, "rsi_oversold": 30,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
    },
}
