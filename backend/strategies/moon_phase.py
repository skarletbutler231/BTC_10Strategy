"""Moon Phase — the lunar-cycle folklore, implemented so it can be measured.

Idea
----
The oldest piece of market astrology: *buy the new moon, sell the full moon*.
The claim is that sentiment tracks the lunar cycle, so the waxing half (new ->
full) is the bullish stretch and the waning half (full -> new) the bearish one.

There is real academic work behind the claim — Dichev & Janes (2003) and Yuan,
Zheng & Zhu (2006) both reported lower equity returns around full moons across
many markets — but it is widely regarded as not surviving correction for
multiple testing, and none of it concerns five-minute crypto bars. **Treat the
prior for a real edge here as very low.** This module exists so the claim can be
tested against the same evidence bar as every other strategy in the repo rather
than argued about; see PRESETS at the bottom for what the sweep actually found.

Phase model
-----------
Phase is a pure function of the timestamp, so this needs no market data and no
dependencies. The instant of every new and full moon is computed with Meeus
*Astronomical Algorithms* Ch.49, which matches published lunations to under a
minute, and a bar's phase is its interpolated position between the surrounding
anchors.

Both anchors are used deliberately. A lunation is **not** symmetric — the moon's
orbital speed varies with its anomaly, so the full moon can fall up to ~20 hours
away from the midpoint between two new moons. Interpolating from new moons alone
would misplace the Full Moon bucket by as much as 22% of a bucket width.

  fraction 0.00 = new moon (exact)      fraction 0.50 = full moon (exact)
  waxing = [0.0, 0.5)                   waning = [0.5, 1.0)

The eight named buckets are each 1/8 of a cycle **centred** on their phase, so
"Full Moon" means the ~3.7 days around the exact full moon, not after it.

Direction
---------
`predict_direction` reads the folklore straight (waxing -> LONG, waning ->
SHORT) or inverts it. Side comes from which half of the cycle the bar sits in,
never from the bucket — the New and Full buckets straddle the halfway line, so
deriving side per bucket would make them ambiguous. The eight toggles are purely
a *filter* on when trading is allowed.

Parameter groups
----------------
Lunar Phase   trade_new, trade_wax_cres, trade_first_q, trade_wax_gib,
              trade_full, trade_wan_gib, trade_last_q, trade_wan_cres
Entry         entry_mode  (Every Bar | Once Per Day | Once Per Phase)
Decision      predict_direction  (Waxing Long | Waxing Short)
Volatility    vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter  (shared)
Allowed Trading Window (shared)
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_SIN = lambda d: math.sin(math.radians(d))

SYNODIC = 29.530588853          # mean synodic month, days
_NM_EPOCH = 947182440.0         # 2000-01-06 18:14 UTC new moon, unix seconds
_DELTA_T = 69.0                 # TT -> UTC, seconds (this century; ~0 impact)

# Bucket index -> (param key, label). Each spans 1/8 of a cycle, centred on the
# named phase, so index = round(fraction * 8) mod 8.
PHASES = [
    ("trade_new",       "New Moon"),
    ("trade_wax_cres",  "Waxing Crescent"),
    ("trade_first_q",   "First Quarter"),
    ("trade_wax_gib",   "Waxing Gibbous"),
    ("trade_full",      "Full Moon"),
    ("trade_wan_gib",   "Waning Gibbous"),
    ("trade_last_q",    "Last Quarter"),
    ("trade_wan_cres",  "Waning Crescent"),
]
_ENTRY_MODES = ["Every Bar", "Once Per Day", "Once Per Phase"]
_DIRECTIONS = ["Waxing Long", "Waxing Short"]


def _phase_jde(k: float, full: bool) -> float:
    """JDE of the k-th new (full=False) or full (full=True) moon. Meeus Ch.49."""
    T = k / 1236.85
    jde = (2451550.09766 + 29.530588861 * k
           + 0.00015437 * T**2 - 0.000000150 * T**3 + 0.00000000073 * T**4)
    E = 1 - 0.002516 * T - 0.0000074 * T**2
    M = 2.5534 + 29.10535670 * k - 0.0000014 * T**2 - 0.00000011 * T**3
    Mp = (201.5643 + 385.81693528 * k + 0.0107582 * T**2
          + 0.00001238 * T**3 - 0.000000058 * T**4)
    F = (160.7108 + 390.67050284 * k - 0.0016118 * T**2
         - 0.00000227 * T**3 + 0.000000011 * T**4)
    Om = 124.7746 - 1.56375588 * k + 0.0020672 * T**2 + 0.00000215 * T**3
    a, b, c, d, e = ((-0.40614, 0.17302, 0.01614, 0.01043, 0.00734) if full
                     else (-0.40720, 0.17241, 0.01608, 0.01039, 0.00739))
    return jde + (
        a * _SIN(Mp) + b * E * _SIN(M) + c * _SIN(2 * Mp) + d * _SIN(2 * F)
        + e * E * _SIN(Mp - M) - 0.00515 * E * _SIN(Mp + M)
        + 0.00209 * E * E * _SIN(2 * M) - 0.00111 * _SIN(Mp - 2 * F)
        - 0.00057 * _SIN(Mp + 2 * F) + 0.00056 * E * _SIN(2 * Mp + M)
        - 0.00042 * _SIN(3 * Mp) + 0.00042 * E * _SIN(M + 2 * F)
        + 0.00038 * E * _SIN(M - 2 * F) - 0.00024 * E * _SIN(2 * Mp - M)
        - 0.00017 * _SIN(Om) - 0.00007 * _SIN(Mp + 2 * M))


def _k_range(t0: float, t1: float):
    """Lunation numbers bracketing [t0, t1], with a lunation of slack each end."""
    k0 = math.floor((t0 - _NM_EPOCH) / 86400.0 / SYNODIC) - 2
    k1 = math.ceil((t1 - _NM_EPOCH) / 86400.0 / SYNODIC) + 2
    return int(k0), int(k1)


def moon_anchors(t0: float, t1: float) -> List[tuple]:
    """Time-sorted (unix_seconds, fraction) for every new (0.0) and full (0.5)
    moon around [t0, t1]. New and full must strictly alternate."""
    k0, k1 = _k_range(t0, t1)
    to_unix = lambda jde: (jde - 2440587.5) * 86400.0 - _DELTA_T
    pts = sorted(
        [(to_unix(_phase_jde(k, False)), 0.0) for k in range(k0, k1 + 1)]
        + [(to_unix(_phase_jde(k + 0.5, True)), 0.5) for k in range(k0, k1 + 1)]
    )
    return pts


def moon_fractions(times: List[int], pts: List[tuple]) -> List[float]:
    """Phase fraction in [0,1) for each timestamp; 0 = new moon, 0.5 = full.

    `times` must be non-decreasing (candles always are) — a single cursor walks
    the anchor list, so this is O(n) over the bars rather than O(n log n).
    """
    out: List[float] = []
    j, n = 0, len(pts)
    for t in times:
        while j + 1 < n - 1 and pts[j + 1][0] <= t:
            j += 1
        (ta, fa), (tb, _) = pts[j], pts[j + 1]
        span = tb - ta
        frac = fa + 0.5 * (min(max((t - ta) / span, 0.0), 1.0) if span > 0 else 0.0)
        out.append(frac % 1.0)
    return out


def phase_index(fraction: float) -> int:
    """Bucket index 0-7 for a phase fraction (centred on the named phase)."""
    return int(round(fraction * 8)) % 8


class MoonPhase(Strategy):
    id = "moon_phase"
    name = "Moon Phase"
    description = ("Lunar-cycle folklore — long the waxing half, short the "
                   "waning half — with per-phase filters, from a Meeus Ch.49 "
                   "ephemeris. MEASURED AND FOUND EMPTY on 939k BTC 5m bars: "
                   "no phase deviates from the base rate by 0.15pp. Kept as a "
                   "documented negative, not a tradeable strategy.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Lunar Phase", [
                Param(key, label, True, "bool",
                      help=f"Allow entries during the ~3.7 days centred on the "
                           f"{label.lower()}.")
                for key, label in PHASES
            ]),
            ParamGroup("Entry", [
                Param("entry_mode", "Entry Mode", "Every Bar", "enum",
                      options=_ENTRY_MODES,
                      help="Every Bar gives the most statistical power and is "
                           "the right setting for testing the claim. Once Per "
                           "Day takes the first allowed bar of each UTC day; "
                           "Once Per Phase takes only the first bar of each "
                           "phase window (~12 per phase per year)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Waxing Long", "enum",
                      options=_DIRECTIONS,
                      help="Waxing Long is the folklore: long from new moon to "
                           "full, short from full back to new. Waxing Short "
                           "inverts it. Side always comes from the half of the "
                           "cycle, never from the bucket."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also sizes TP/SL for this strategy."),
                Param("atr_pct_min", "ATR % Min", 0.0, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR % Max", 20.0, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (too violent)."),
            ]),
            common.trend_filter_group(),
            common.trading_window_group(),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        if not candles:
            return []

        allowed_phase = [bool(p[key]) for key, _ in PHASES]
        if not any(allowed_phase):
            return []

        entry_mode = p["entry_mode"]
        waxing_long = p["predict_direction"] == "Waxing Long"
        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_logic"] == "With Trend"
        use_window = bool(p["use_trading_window"])
        allowed_days = common.allowed_days(p)
        win_start, win_end = common.window_minutes(p)

        n = len(candles)
        times = [c["time"] for c in candles]
        fracs = moon_fractions(times, moon_anchors(times[0], times[-1]))
        atr = ind.atr(candles, vol_len)
        trend_ma = (common.moving_average(common.source_values(candles, p["source"]),
                                          p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)

        signals: List[Signal] = []
        last_day = None          # for Once Per Day
        last_bucket = None       # for Once Per Phase
        for i, c in enumerate(candles):
            frac = fracs[i]
            bucket = phase_index(frac)

            # Entry-mode bookkeeping must run on EVERY bar, before any filter,
            # or "first bar of the phase" would silently become "first bar that
            # happened to pass the filters".
            day = times[i] // 86400
            new_day, new_bucket = day != last_day, bucket != last_bucket
            last_day, last_bucket = day, bucket

            if not allowed_phase[bucket]:
                continue
            if entry_mode == "Once Per Day" and not new_day:
                continue
            if entry_mode == "Once Per Phase" and not new_bucket:
                continue
            if use_window and not common.in_window(times[i], allowed_days,
                                                   win_start, win_end):
                continue

            a = atr[i]
            if a is None or a <= 0:
                continue
            cl = c["close"]
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            waxing = frac < 0.5
            side = ("long" if waxing else "short") if waxing_long else \
                   ("short" if waxing else "long")

            if use_trend:
                m = trend_ma[i]
                if m is None or not common.trend_ok(side, cl, m, trend_with):
                    continue

            label = PHASES[bucket][1]
            age = frac * SYNODIC
            reason = (f"{label} ({'waxing' if waxing else 'waning'}, "
                      f"age {age:.1f}d) -> {side.upper()}")
            signals.append(Signal(
                index=i, time=times[i], side=side, price=cl, reason=reason, atr=a,
                meta={"phase": label, "phase_index": bucket,
                      "fraction": round(frac, 4), "age_days": round(age, 2),
                      "waxing": waxing},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets — NONE, and that is the finding.
#
# MEASURED, 939,513 BTCUSDT 5m bars, 2017-08 -> 2026-07. Rather than a parameter
# sweep (which would just be 8 buckets x 2 directions of multiple testing), the
# hypothesis was measured directly: bucket every bar by lunar phase and record
# whether the NEXT candle closed up. The null is the base rate, not 50% — BTC's
# 5m candles close up 50.147% of the time.
#
#   phase bucket        bars     up-rate    vs base       z
#   New Moon         117,250     50.004%    -0.142pp   -0.97
#   Waxing Crescent  117,194     50.276%    +0.130pp   +0.89
#   First Quarter    117,082     50.026%    -0.121pp   -0.83
#   Waxing Gibbous   117,124     50.161%    +0.015pp   +0.10
#   Full Moon        116,677     50.145%    -0.001pp   -0.01
#   Waning Gibbous   116,530     50.120%    -0.026pp   -0.18
#   Last Quarter     116,001     50.270%    +0.124pp   +0.84
#   Waning Crescent  117,113     50.170%    +0.024pp   +0.16
#
# Not one bucket deviates from the base rate by even 0.15pp, and the largest
# |z| across all eight is 0.97 — short of significance before any correction for
# multiple testing, let alone after (8 buckets needs |z|>2.5 for 5%).
#
# The claim itself, tested directly: the waxing half's up-rate is 50.096% and
# the waning half's is 50.198%, a difference of -0.102pp (z = -0.99). The
# folklore predicts a POSITIVE difference, so the point estimate is not merely
# insignificant, it leans the wrong way.
#
# This is a strong null, not a weak one. At ~117k bars per bucket the standard
# error is 0.15pp, so a real effect of 0.5pp would have shown at z>3. Nothing is
# there.
#
# ON THE ~49.7% HIT RATE. Running the folklore end to end scores 49.708%, and
# INVERTING it scores 49.809% — both below 50%, which looks paradoxical until
# you count the flat candles. 4,541 bars (0.48%) close exactly at their open and
# are scored as losses either way. That penalty, not a hidden reverse edge, is
# the whole story: a zero-information signal paying a small structural cost. Any
# "moon strategy" that appears profitable on this data is picking up the
# selection you applied on top, not the moon.
#
# So: no preset. The Default params (every phase on, Every Bar, folklore
# direction) ARE the hypothesis, and they lose. The strategy is kept because a
# measured negative is worth more than an untested rumour, and because the
# ephemeris is reusable if anyone wants to test a lunar claim on another horizon
# — a daily or weekly bar, where the original equity research actually operated,
# is a different and untested question.
#
# Deliberately NOT registered in combined.py's SUB_IDS: a voter with no edge can
# only dilute an agreement rule. One line to add there if you disagree.
# ---------------------------------------------------------------------------
# A HIT-RATE OPTIMISATION WAS RUN. It found nothing, and the way it failed is
# worth keeping, because "sweep it harder" is the obvious next thought.
#
# Search space, 1,530 configs: 255 non-empty subsets of the 8 phase buckets
# x 2 directions x 3 trend-filter modes. Same protocol as the Reversal presets
# (train 2024-07..2025-11, holdout 2025-11..2026-07, unswept 2018..2024).
#
# Best on train: 51.76% (6,223 bets) -- WaxLong / Against Trend / Waxing
# Crescent only. That looks shippable until you check the two controls.
#
# CONTROL 1 — a meaningless cycle does just as well. Re-running the identical
# sweep with the lunar cycle replaced by an arbitrary 31.7-day cycle gives a
# best train hit of 51.92% -- BETTER than the real moon's 51.76%. Whatever the
# sweep is picking up, it is not lunar; it is the best-of-1,530 order statistic.
#
# CONTROL 2 — the moon adds nothing over the trend filter. Every top config
# used Against Trend, which FORCES side = long below EMA200 / short above it.
# The phase subset therefore cannot pick the direction, only which bars are
# taken. Measured over the unswept years:
#
#   rule                                       bets      hit
#   pure Against Trend, no moon at all      681,818   50.82%
#   + the winning subset (Waxing Crescent)   84,803   50.76%
#   + the other seven buckets                597,015   50.83%
#   + a fake 31.7-day cycle, 1 of 8           85,228   50.72%
#
# The "optimised" lunar filter scores WORSE than using no lunar filter, while
# cutting volume roughly 8x. The ~50.8% is the mean-reversion edge of the
# Against-Trend filter, which this repo documents elsewhere and which is far
# too thin to trade after costs. It is not a moon effect.
#
# So still no preset. An optimised one would be a trap: it carries a 51.76%
# train number that a fake cycle beats and that decays to the no-moon baseline
# out of sample.
PRESETS: dict = {}
