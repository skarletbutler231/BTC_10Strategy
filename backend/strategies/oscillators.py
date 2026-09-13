"""Oscillators — the textbook banded-oscillator rules, on one oscillator at a time.

Idea
----
An *oscillator* in the classic sense is a bounded indicator: it cannot trend
away, so it must turn, and every reading has a fixed meaning ("70" means the
same thing in 2017 and 2026). That boundedness is the whole premise, and it is
what generates the four rules every textbook teaches:

  * **overbought / oversold** — the reading enters a band;
  * **the band exit** — it crosses back out, which is the rule most books
    actually recommend, because "overbought" alone can stay overbought for days;
  * **the signal-line cross** — the oscillator crosses its own moving average;
  * **the centreline cross** — it crosses the midpoint, a regime flip;

plus Wilder's **failure swing**, the one pattern he described as a signal in its
own right: the oscillator makes an extreme, pulls back, fails to reclaim that
extreme, then breaks the intervening trough.

This is deliberately NOT [Momentum Indicators](momentum.py), which averages nine
oscillators into one composite and trades the composite. Here exactly **one**
oscillator is selected and the question is which *rule* on it pays — the axis
that composite hides. It is also not [Reversal](reversal.py), whose oscillator
leg is divergence against price; nothing here looks at price shape at all.

Putting seven oscillators on one scale
--------------------------------------
Every supported oscillator is mapped to a common **0-100** scale, so the zone
levels, the signal line and the failure-swing logic are written once and the
oscillator becomes a parameter rather than a fork in the code:

    RSI          native 0-100
    Stochastic   %K, native 0-100
    Stoch RSI    %K, native 0-100
    Williams %R  %R + 100          (-100..0 -> 0..100)
    CCI          50 + 50 x clamp(cci / 200)
    Ultimate     native 0-100
    TSI          50 + 50 x (tsi / 100)

All seven read one `osc_length` (Ultimate uses L/2 : L : 2L, which at 14 is
Wilder's own 7/14/28; TSI uses L for its first smoothing and L/2 for its
second), then optional `smooth_k` bars of SMA, then a `signal_length` SMA as the
signal line.

Which way is "up"
-----------------
Each trigger defines **d**, the direction the oscillator is reading *bullish*:

  * Zone Entry  — crossing into overbought: d = +1; into oversold: d = -1
  * Zone Exit   — leaving overbought (back below the line): d = +1 (it *was*
    the bullish extreme); leaving oversold: d = -1
  * Signal Cross / Centreline Cross — crossing up: d = +1, down: d = -1
  * Failure Swing — a top failure swing follows an overbought extreme: d = +1;
    a bottom failure swing: d = -1

`predict_direction` then decides what that is worth: **Fade Oscillator** trades
against d (the extreme exhausts — and note this is what makes Fade agree with
the textbook reading on the three zone-based triggers), **Follow Oscillator**
trades with it.

Causality
---------
Every trigger is evaluated from bar i and bar i-1 only, and the failure-swing
state machine is advanced strictly left to right, so no signal reads a bar that
had not printed. The prefix test in the repo's convention holds.

Parameter groups
----------------
Oscillator    osc_type, osc_length, smooth_k, signal_length
Zones         overbought, oversold
Trigger       trigger_mode, fs_max_bars
Decision      predict_direction  (Fade | Follow)
Volatility    vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter  (shared)
Window        (shared)

In Polymarket up/down mode the Exit/Backtest params are unused — each signal is
a bet on the next candle's direction.
"""

from __future__ import annotations

from typing import List, Optional

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_OSC_TYPES = ["RSI", "Stochastic", "Stoch RSI", "Williams %R", "CCI",
              "Ultimate", "TSI"]
_TRIGGERS = ["Zone Entry", "Zone Exit", "Signal Cross", "Centerline Cross",
             "Failure Swing"]
_DIRECTIONS = ["Fade Oscillator", "Follow Oscillator"]

# CCI's conventional "stretched" band is +/-100; +/-200 is a fair full scale.
_CCI_SCALE = 200.0
_MID = 50.0


def _clamp(x: float) -> float:
    return 1.0 if x > 1.0 else -1.0 if x < -1.0 else x


def _smooth(series: List[Optional[float]], period: int) -> List[Optional[float]]:
    """SMA over a series that may carry None gaps. period<=1 is a pass-through,
    so 'no smoothing' costs nothing.

    Gaps are not only the leading warm-up: Williams %R is undefined on a flat
    window, Ultimate on a zero true-range window and TSI on a zero denominator,
    all of which dead 2017 tape produces mid-series. Any window that contains a
    None yields None and the running sum restarts after the gap. On a gap-free
    series this is the same arithmetic, in the same order, as ``ind.sma``.
    """
    n = len(series)
    if period <= 1:
        return list(series)
    out: List[Optional[float]] = [None] * n
    run, s = 0, 0.0                          # consecutive valid bars, their sum
    for i, v in enumerate(series):
        if v is None:
            run, s = 0, 0.0
            continue
        run += 1
        s += v if run <= period else v - series[i - period]
        if run >= period:
            out[i] = s / period
    return out


class Oscillators(Strategy):
    id = "oscillators"
    name = "Oscillators"
    description = ("One banded oscillator (RSI, Stochastic, Stoch RSI, Williams "
                   "%R, CCI, Ultimate or TSI) on a common 0-100 scale, traded by "
                   "the textbook rule: zone entry, zone exit, signal-line cross, "
                   "centreline cross or Wilder's failure swing.")

    # ---- schema ---------------------------------------------------------

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Oscillator", [
                Param("osc_type", "Oscillator", "RSI", "enum", options=_OSC_TYPES,
                      help="Which oscillator to read. All seven are rescaled to "
                           "0-100 so the zone levels below mean the same thing "
                           "whichever is chosen."),
                Param("osc_length", "Oscillator Length", 14, "int", 2, 500, 1,
                      "Shared lookback. Ultimate uses length/2 : length : "
                      "length*2; TSI uses length then length/2; Stoch RSI uses "
                      "it for both the RSI and the range it is ranked against."),
                Param("smooth_k", "Smoothing (bars)", 1, "int", 1, 100, 1,
                      "SMA applied to the raw oscillator before any rule is "
                      "evaluated. 1 = raw. The classic 'slow' Stochastic is 3."),
                Param("signal_length", "Signal Line Length", 3, "int", 1, 200, 1,
                      "SMA of the oscillator that forms the signal line. Used "
                      "only by the Signal Cross trigger."),
            ]),
            ParamGroup("Zones", [
                Param("overbought", "Overbought", 70, "float", 50, 100, 1,
                      "Upper band on the 0-100 scale. 70 is Wilder's RSI level; "
                      "80 is the Stochastic convention."),
                Param("oversold", "Oversold", 30, "float", 0, 50, 1,
                      "Lower band on the 0-100 scale."),
            ]),
            ParamGroup("Trigger", [
                Param("trigger_mode", "Trigger", "Zone Entry", "enum", options=_TRIGGERS,
                      help="Zone Entry: crosses INTO a band. Zone Exit: crosses "
                           "back OUT of one (the textbook confirmation). Signal "
                           "Cross: crosses its own signal line. Centerline "
                           "Cross: crosses 50. Failure Swing: Wilder's pattern "
                           "— an extreme, a pullback, a failed retest, then the "
                           "break of the intervening pivot."),
                Param("fs_max_bars", "Failure Swing Max Bars", 30, "int", 2, 500, 1,
                      "How long a forming failure swing stays live before it is "
                      "abandoned. Only used by that trigger."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Fade Oscillator",
                      "enum", options=_DIRECTIONS,
                      help="Fade trades against the oscillator's bullish "
                           "reading (the extreme exhausts) — on the zone "
                           "triggers this IS the textbook signal. Follow trades "
                           "with it."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also sizes TP/SL (xATR) in TP/SL mode."),
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

    # ---- the oscillator, on a 0-100 scale --------------------------------

    def _oscillator(self, candles: List[dict], p: dict) -> List[Optional[float]]:
        """The selected oscillator rescaled to 0-100 and smoothed."""
        kind = p["osc_type"]
        L = p["osc_length"]
        n = len(candles)

        if kind == "Stochastic":
            raw, _ = ind.stochastic(candles, L, 3)
        elif kind == "Stoch RSI":
            raw, _ = ind.stoch_rsi(candles, L, L, 1, 1)
        elif kind == "Williams %R":
            raw = [None if v is None else v + 100.0
                   for v in ind.williams_r(candles, L)]
        elif kind == "CCI":
            raw = [None if v is None else _MID + _MID * _clamp(v / _CCI_SCALE)
                   for v in ind.cci(candles, L)]
        elif kind == "Ultimate":
            raw = ind.ultimate_oscillator(candles, max(2, L // 2), L, L * 2)
        elif kind == "TSI":
            closes = [c["close"] for c in candles]
            raw = [None if v is None else _MID + _MID * _clamp(v / 100.0)
                   for v in ind.tsi(closes, L, max(2, L // 2))]
        else:                                        # RSI (default)
            raw = ind.rsi(candles, L)

        if len(raw) != n:                            # defensive: keep alignment
            raw = (list(raw) + [None] * n)[:n]
        return _smooth(raw, p["smooth_k"])

    # ---- triggers --------------------------------------------------------

    @staticmethod
    def _failure_swings(osc: List[Optional[float]], ob: float, os_: float,
                        max_bars: int) -> List[Optional[int]]:
        """Wilder's failure swings -> d (+1 top, -1 bottom) at the completing bar.

        Both mirrors run through :func:`_top_failure_swings`; the bottom swing is
        the top swing of the reflected series ``100 - osc`` against the reflected
        band ``100 - oversold``, which is exactly what "mirror" means on a 0-100
        scale. Writing it once removes the possibility of the two sides drifting
        apart, which is the usual bug in hand-written pattern mirrors.

        If both complete on the same bar (possible only with a very wide band on
        a whipsawing reading) neither is emitted — there is no defensible
        direction to pick.
        """
        n = len(osc)
        up = _top_failure_swings(osc, ob, max_bars)
        flipped = [None if v is None else 100.0 - v for v in osc]
        dn = _top_failure_swings(flipped, 100.0 - os_, max_bars)
        out: List[Optional[int]] = [None] * n
        for i in range(n):
            if up[i] and not dn[i]:
                out[i] = 1
            elif dn[i] and not up[i]:
                out[i] = -1
        return out

    # ---- signals ---------------------------------------------------------

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        ob, os_ = p["overbought"], p["oversold"]
        if os_ >= ob:                       # a degenerate band has no inside
            return []

        osc = self._oscillator(candles, p)
        mode = p["trigger_mode"]
        sig = _smooth(osc, p["signal_length"]) if mode == "Signal Cross" else [None] * n
        fs = (self._failure_swings(osc, ob, os_, p["fs_max_bars"])
              if mode == "Failure Swing" else [None] * n)

        atr = ind.atr(candles, p["vol_atr_length"])
        fade = p["predict_direction"] == "Fade Oscillator"
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_logic"] == "With Trend"
        trend_ma = (common.moving_average(common.source_values(candles, p["source"]),
                                          p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)
        use_window = bool(p["use_trading_window"])
        allowed = common.allowed_days(p)
        win_start, win_end = common.window_minutes(p)

        signals: List[Signal] = []
        for i in range(1, n):
            v, prev = osc[i], osc[i - 1]
            if v is None or prev is None:
                continue

            # --- does this bar fire, and which way does the oscillator read? --
            if mode == "Zone Entry":
                if prev < ob <= v:
                    d, ev = 1, f"entered overbought ({v:.1f} >= {ob:.0f})"
                elif prev > os_ >= v:
                    d, ev = -1, f"entered oversold ({v:.1f} <= {os_:.0f})"
                else:
                    continue
            elif mode == "Zone Exit":
                if prev >= ob > v:
                    d, ev = 1, f"left overbought ({v:.1f} < {ob:.0f})"
                elif prev <= os_ < v:
                    d, ev = -1, f"left oversold ({v:.1f} > {os_:.0f})"
                else:
                    continue
            elif mode == "Signal Cross":
                s, s_prev = sig[i], sig[i - 1]
                if s is None or s_prev is None:
                    continue
                if prev <= s_prev and v > s:
                    d, ev = 1, f"crossed above signal ({v:.1f} > {s:.1f})"
                elif prev >= s_prev and v < s:
                    d, ev = -1, f"crossed below signal ({v:.1f} < {s:.1f})"
                else:
                    continue
            elif mode == "Centerline Cross":
                if prev <= _MID < v:
                    d, ev = 1, f"crossed above 50 ({v:.1f})"
                elif prev >= _MID > v:
                    d, ev = -1, f"crossed below 50 ({v:.1f})"
                else:
                    continue
            else:                                    # Failure Swing
                if fs[i] is None:
                    continue
                d = fs[i]
                ev = ("top failure swing" if d > 0 else "bottom failure swing")

            c = candles[i]
            a = atr[i]
            if a is None or a <= 0:
                continue
            cl = c["close"]
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue
            if use_window and not common.in_window(c["time"], allowed,
                                                   win_start, win_end):
                continue

            side = ("short" if d > 0 else "long") if fade else \
                   ("long" if d > 0 else "short")
            if use_trend and not common.trend_ok(side, cl, trend_ma[i], trend_with):
                continue

            act = "Fade" if fade else "Follow"
            reason = (f"{p['osc_type']} {mode}: {ev} -> {act} {side.upper()} "
                      f"(ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"osc_type": p["osc_type"], "osc": round(v, 2),
                      "osc_prev": round(prev, 2), "trigger": mode,
                      "osc_dir": d, "atr_pct": round(atr_pct, 3)},
            ))
        return signals


def _top_failure_swings(osc: List[Optional[float]], ob: float,
                        max_bars: int) -> List[bool]:
    """Bars where a TOP failure swing completes, scanning strictly left to right.

    The pattern, in Wilder's terms:

      1. the oscillator prints an extreme **X** above `ob`;
      2. it pulls back to a trough **T**;
      3. it rallies off T but *fails* to exceed X;
      4. it breaks back below T — the swing completes on that bar.

    Held as three numbers: the running extreme X, the running trough T since X,
    and whether a rally off T has armed the break. A reading above X invalidates
    the setup and restarts it there (the move was not exhausted after all). A
    setup that has not completed within `max_bars` of its extreme is abandoned,
    so a stale hour-old shape cannot fire on unrelated tape.
    """
    n = len(osc)
    out = [False] * n
    st: Optional[dict] = None
    for i, v in enumerate(osc):
        if v is None:
            continue
        if st is not None and i - st["since"] > max_bars:
            st = None
        if st is None:
            if v > ob:                       # a new extreme starts the pattern
                st = {"ext": v, "trough": None, "armed": False, "since": i}
            continue
        if v > st["ext"]:                    # exceeded the extreme -> restart
            st = {"ext": v, "trough": None, "armed": False, "since": i}
        elif st["trough"] is None or v < st["trough"]:
            if st["armed"]:                  # broke the trough after a failed retest
                out[i] = True
                st = None
            else:
                st["trough"] = v
        elif v > st["trough"]:               # rallied off the trough, under X
            st["armed"] = True
    return out


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m, BTCUSDT). Exit / Backtest params are unused in that mode.
#
# METHOD — fitted to the last two years, with a sealed holdout inside them
# -----------------------------------------------------------------------
#   TRAIN    2024-07-31 -> 2025-11-01   selection happened here and only here
#   HOLDOUT  2025-11-01 -> 2026-07-30   scored once, after the picks were frozen
#   UNSWEPT  2017-08-17 -> 2024-07-31   never loaded by any sweep stage
#
# 2,040 configurations in three stages, each scored by calling this module's own
# `generate_signals`. Stage 1 (210) settled the family — oscillator x trigger x
# direction x length — before anything was tuned. Stage 2 (1,680) tuned length,
# smoothing and band level inside the winning family. Stage 3 (150) tested the
# ATR band and the trend filter on the frozen stage-2 winners. Selection was
# mechanical: train bets >= the tier floor, BOTH halves of train above 52%,
# `osc_length` off the boundary of the grid, then highest train hit rate.
#
# RESULTS — flat $1 per bet, next-candle direction, whole record
# --------------------------------------------------------------
#   preset       bets     hit      z | unswept 17-24 |  train  HOLDOUT
#   Volume     56,853  56.15%  +29.3 | 41,015 56.75% | 54.35%  55.03%
#   Balanced   20,988  56.95%  +20.1 | 15,953 57.25% | 56.07%  55.91%
#   Selective   5,247  56.93%  +10.0 |  3,940 57.08% | 56.90%  55.74%
#
# **Balanced is the pick** — and it is Wilder's published RSI defaults, 14 bars
# with a 70/30 band, unchanged. The sweep was free to choose any of six
# oscillators, ten lengths, four smoothings and seven band widths, and what it
# landed on at the middle tier is the setting printed in the 1978 book.
#
# Read the hit rates against **49.52%, not 50%**: 0.48% of 5m candles close
# exactly at their open and lose whichever side you take.
#
# WHAT THE SWEEP ACTUALLY FOUND
# -----------------------------
# 1. **Only Zone Entry earns.** Best train config per trigger (Fade, n >= 1,000):
#
#        Zone Entry        55.84%      Centerline Cross   ~50%
#        Zone Exit         53.46%      Signal Cross       ~50%
#        Failure Swing     52.33%
#
#    and out of sample the gap widens rather than closing: the same RSI settings
#    score 55.97% on the holdout via Zone Entry against 50.49% via Zone Exit.
#    This is worth dwelling on, because **the band exit is the rule the books
#    actually recommend** — "overbought is not a sell signal until it crosses
#    back down" — and here it destroys the edge instead of confirming it. By the
#    time the oscillator has climbed back out of the band, the reversion it was
#    predicting has already happened. So has the entire finding.
#
#    Wilder's **failure swing**, the one pattern he singled out as a signal in
#    its own right, is the clearest negative in the set: 53.88% on train and
#    **45.76%** on the holdout. It is a curve fit, not a pattern.
#
#    The two cross triggers are noise at every setting tried. Neither the signal
#    line nor the centreline carries anything at this horizon.
#
# 2. **Fade, not follow — and the mirror is exact.** Taking the extreme at face
#    value instead of fading it scores 43.27% / 42.85% / 42.61%, as far below the
#    ceiling as the presets are above it. A selection artifact does not produce a
#    clean sign flip on the same bets.
#
# 3. **Both marginals are monotone, which is the reassuring part.** Pooled over
#    all 1,680 stage-2 configs, train hit rate rises with the band and with
#    smoothing, without a single inversion:
#
#        band  65: 50.72%   70: 50.88%   75: 51.15%   80: 51.31%
#              85: 51.45%   90: 51.78%   95: 52.07%
#        smooth 1: 50.45%    2: 51.53%    3: 51.68%    5: 51.79%
#
#    Rarer and cleaner readings are better readings. A monotone marginal cannot
#    be produced by a lucky cell, so this is the one result here that carries
#    weight independent of the selection rule.
#
# 4. **RSI is the best oscillator, and Stoch RSI the worst.** Pooled over stage
#    2 (train / holdout): RSI 53.51 / 54.16, TSI 53.11 / 53.48, Ultimate 52.35 /
#    53.67, CCI 51.82 / 52.32, Stochastic 51.10 / 52.50, Stoch RSI 50.23 /
#    51.26. Ranking the RSI of the RSI *below* the RSI is the expected direction
#    — re-ranging a bounded reading against its own recent range adds noise
#    without adding information — but it is worth having measured.
#
# 5. **Stochastic %K and Williams %R are the same number**, verified to 1.4e-14
#    over 200,000 bars: %K = 100(c-LL)/(HH-LL) and %R + 100 is the same
#    expression. Both are offered because both are asked for by name; choosing
#    between them is a choice of label, not of indicator.
#
# 6. **The trend filter buys nothing.** Pooled over stage 3 it spans 54.59% to
#    55.13% on train with no ordering that survives the holdout, and for RSI the
#    "Against Trend / SMA50" rows are *byte-identical* to the unfiltered ones —
#    fading an overbought extreme is definitionally against the short-term
#    trend, so that filter passes 100% of signals. All three presets ship with
#    it off. The ATR band is similarly flat (~1pp across every setting tried);
#    the repo default 0.05-1.5 is kept rather than fitted.
#
# CHECKS RUN AFTER THE PICKS WERE FROZEN
# ---------------------------------------
# * **No look-ahead.** The prefix test — signals from `candles[:m]` must equal
#   the signals from the whole series that fall before m — passes with **0
#   mismatches** across all 7 oscillators x 5 triggers at three cut points.
# * **Not Momentum Indicators relabelled.** Against that strategy's own Balanced
#   preset only 9-22% of these signals are shared (Jaccard 6-16%), and the
#   exclusive majority scores 55.9-56.7% on its own. Same against CCI Williams:
#   14-19% shared, exclusive half 55.6-56.6%. The edge lives in the bets the
#   other strategies never place.
# * **Not directional beta.** Bets split 45-50% long and both sides win
#   (Balanced: long 57.98%, short 55.93%) while 49.6-50.5% of all 5m candles
#   close up in every year.
#
# CAVEATS
# -------
# 1. **2017 is the losing year** — 47.4% / 47.2% / 52.0% (partial year, Aug-Dec,
#    thin early Binance liquidity). Fading an extreme loses in a parabolic run,
#    which is what 2017 was. Every full year 2018-2026 clears 54.4% on Volume,
#    55.3% on Balanced and 52.7% on Selective. This is the same year that breaks
#    Momentum Indicators, Support & Resistance and Reversal; treat it as one
#    shared regime risk, not as four independent warnings.
# 2. **The edge decays.** 2018-2023 runs 55.2-60.4%; 2024-2026 runs 52.7-58.1%.
#    The recent numbers are the live estimate.
# 3. Selective's ~580 bets/year cannot prove itself either way inside a year,
#    and its two train halves are 54.9% / 59.2%. It is shipped for the volume
#    tier, not as a recommendation.
# 4. A bet pays only when the hit rate beats the price paid. Balanced's ~56%
#    needs a fill below 0.56, which a real Polymarket book will not offer on a
#    directional 5m market. **The hit rate is the finding**; the EV per $1 the
#    dashboard prints at 0.50 odds is an upper bound.
# 5. Not swept: the 1m interval. `osc_length` counts bars, so Balanced's 14 is
#    70 minutes here and would be 14 minutes on 1m — a different setup.
# 6. Days and times are UTC; a bar is stamped by its open time.
_OSC_COMMON = {
    "trigger_mode": "Zone Entry",            # the only trigger that earns — finding 1
    "predict_direction": "Fade Oscillator",  # not Follow — finding 2
    "signal_length": 3,                      # unused outside Signal Cross
    "fs_max_bars": 30,                       # unused outside Failure Swing
    "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
    "use_trend_filter": False,               # buys nothing — finding 6
    "trend_logic": "With Trend", "ma_type": "EMA", "ma_length": 200,
    "source": "close",
    "use_trading_window": False,
}

PRESETS: dict = {
    # 56,853 bets, 56.15% hit (z +29.3); train 54.35%, holdout 55.03%, and
    # 56.75% across the never-swept 2017-2024. A tight 10/90 band on a smoothed
    # 11-bar Stochastic: the widest net here, ~6,300 bets/year.
    "PM 5m Volume": {
        **_OSC_COMMON,
        "osc_type": "Stochastic", "osc_length": 11, "smooth_k": 2,
        "overbought": 90, "oversold": 10,
    },
    # *** THE PICK. *** 20,988 bets, 56.95% hit (z +20.1); train 56.07%, holdout
    # 55.91%, never-swept 2017-2024 57.25%, and every full year from 2018 at or
    # above 55.3%. Wilder's own RSI defaults — 14 bars, 70/30, unsmoothed —
    # selected out of a 1,680-config grid that was free to choose otherwise.
    "PM 5m Balanced": {
        **_OSC_COMMON,
        "osc_type": "RSI", "osc_length": 14, "smooth_k": 1,
        "overbought": 70, "oversold": 30,
    },
    # 5,247 bets, 56.93% hit (z +10.0); train 56.90%, holdout 55.74%. Same edge
    # as Balanced at a quarter of the volume, so it buys selectivity rather than
    # accuracy. Its train halves are 54.9% / 59.2% — see caveat 3.
    "PM 5m Selective": {
        **_OSC_COMMON,
        "osc_type": "TSI", "osc_length": 7, "smooth_k": 2,
        "overbought": 90, "oversold": 10,
    },
}

# ---------------------------------------------------------------------------
# 15-MINUTE preset. Fitted on the latest six months of BTCUSDT 15m, which is a
# different protocol from the three above and needs saying up front:
#
#   LOADED   2026-03-13 -> 2026-09-13   the six months the sweep ran on
#     train  2026-03-13 -> 2026-07-13   selection happened here (4 months)
#     hold   2026-07-13 -> 2026-09-13   scored after the pick was frozen (2)
#   UNLOADED 2017-08-17 -> 2026-03-13   never read by the sweep; scored once at
#                                       the end as the real out-of-sample check
#
# Six months of 15m is 17,700 bars, so the fitted window cannot on its own
# separate a fit from a fluke. The unloaded 8.5 years carry the result: 21,602
# bets there against 1,383 in the fitted window.
#
# SWEEP. Stage 1 (1,960 configs) raced every family — 7 oscillators x 5
# triggers x Fade/Follow x 7 lengths x 2 bands x 2 smoothings. Stage 2 (2,744)
# tuned length (3-28), smoothing (1-5) and band (60/40-90/10) inside the
# survivor. Stage 3 (66) tried the ATR band, ATR length, trend filter and
# smoothing on the frozen finalists. The rule was fixed before stage 2 ran:
# train bets >= 600, both train halves above 52%, length and band off the grid
# boundary, then highest train hit.
#
# WHAT THE 15m SWEEP FOUND — the 5m findings, one for one
#
# 1. Zone Entry + Fade is again the only family that earns. Pooled over stage 1
#    (train / holdout): Zone Entry Fade 53.31 / 53.22; Centerline Cross Fade
#    51.46 / 51.52; everything else at or under 50 on the holdout. The textbook
#    band EXIT on the shipped settings scores 52.02% in the window and 50.41%
#    on the unloaded years — the wait for confirmation still eats the edge.
# 2. Follow is the exact mirror: 42.81% in the window, 42.44% unloaded.
# 3. Both marginals are monotone again. Pooled train hit rises with the band
#    (60: 52.42 -> 90: 54.38) and with length (3: 52.51 -> 28: 54.39) without an
#    inversion. Smoothing, unlike on 5m, does NOT help: 1 bar 53.30, 2: 53.22,
#    3: 52.47, 5: 53.26, and on the finalists it only removes bets.
# 4. Same oscillator ranking. Zone Entry Fade, stage 2 pooled (train / hold):
#    RSI 54.93 / 55.45, TSI 54.38 / 54.56, Ultimate 54.08 / 54.32, Stochastic
#    53.47 / 53.07, CCI 53.01 / 53.13, Stoch RSI 51.62 / 51.83.
# 5. The filters buy nothing. The ATR band changes <= 0.3pp and <= 20 bets at
#    every setting tried (p5 of 15m ATR% is 0.097, so the 0.05 floor is a
#    sanity check, not a fit); ATR length is inert; Against Trend at EMA20 is
#    byte-identical to unfiltered and longer MAs only remove bets; With Trend
#    removes 60-97% of them.
#
# THE PICK, AND WHY IT IS NOT THE RULE'S PICK. The rule selected TSI 9 30/70:
# 963 bets at 59.09% in the window, train 59.82%, holdout 57.56%. The train
# frontier from 600 to 1,200 train bets is a plateau, though, not a peak:
#
#   config             6m bets  6m hit  train (halves)      HOLDOUT  UNLOADED 8.5y
#   TSI 9   30/70         963   59.09%  59.82 (62.6/57.1)   57.56%   14,617  57.84%
#   RSI 7   30/70       1,383   57.19%  57.86 (58.1/57.6)   55.89%   21,602  57.50%
#   RSI 9   35/65       1,563   57.39%  57.82 (56.3/59.4)   56.50%   24,182  57.29%
#   RSI 8   35/65       1,782   56.96%  57.48 (56.0/58.8)   55.88%   28,080  57.52%
#
# TSI's two-point in-window lead shrinks to a third of a point on the unloaded
# years, on half the bets; its train halves are 5.5pp apart against RSI 7's
# 0.5pp. The bet gap is structural (it is the band and the length) and the hit
# gap is not, so for a brief of bets AND hit rate RSI 7 30/70 is the knee of
# the frontier: 44% more bets than TSI 9 at the same out-of-sample edge, and
# the most even train halves of anything on it. It is also Wilder's band at 7
# bars — 105 minutes, the same wall-clock scale as the 5m Balanced's 14 x 5m =
# 70 min. The 5m preset's length carried over unchanged (RSI 14 on 15m, 3.5 h)
# scores 59.04% on 459 window bets but 55.40% on 7,099 unloaded ones with
# three years under 54%: length has to be rescaled, not copied.
#
# RESULTS — flat $1 per bet, next-candle direction
#
#   preset             6m bets  6m hit   train   HOLDOUT   unloaded 8.5y          worst yr
#   PM 15m Balanced      1,383  57.19%  57.86%   55.89%    57.50% (21,602, z +22)  56.09% (2021)
#
# Per year on the full record, none of it fitted except the last six months:
#   2017  50.70% (785)    2020  58.08% (2450)   2023  58.70% (2472)   2026  57.32% (1907)
#   2018  58.21% (2479)   2021  56.09% (2619)   2024  58.45% (2628)
#   2019  57.71% (2367)   2022  57.38% (2541)   2025  57.54% (2737)
#
# Every full year sits in a 56.1-58.7% band, the 18 months right before the
# window (2024-09 -> 2026-03) score 57.41% on 4,057 bets, and 2017 — the year
# that loses on every 5m preset — is at chance rather than under it. Whole
# record: 22,985 bets, 57.48%, z +22.7. About 7.5 bets a day.
#
# Read the hit rates against 49.9%, not 50%: 0.13% of 15m candles close
# exactly at their open (0.48% on 5m) and lose whichever side you take.
#
# CHECKS RUN AFTER THE PICK WAS FROZEN
# * No look-ahead: the prefix test passes with 0 mismatches at three cut points.
# * Not directional beta: bets run 49% long / 51% short and both sides win —
#   window 57.14% / 57.24%, unloaded 58.22% / 56.81% — while 49.56% of window
#   candles close up.
# * Not Reversal's PM 15m BOS relabelled: 21% of these bars are shared (Jaccard
#   15.6%), and the exclusive 1,090 bets score 55.87% against a 51.95% base
#   rate for a 15m bar reversing the one before it. The 293 shared bars run
#   ~62%: the two strategies agree on the strongest setups and disagree on the
#   rest.
#
# WHERE IT FAILS. The worst month in the window is 2026-06 at 54.3% on 221
# bets; the other six range 55.8-60.9%. The worst full year is 2021 at 56.09%.
# Expect weeks at 54-55%, and read the 57% as a multi-month average. The
# 0.50-odds EV the dashboard prints assumes a fill at even; a real 15m book
# prices away from it. Hit rate is the finding.
#
# NOT SHIPPED. TSI 9 30/70 (above) if hit rate alone is the brief — its 57.84%
# unloaded is real, it simply costs half the bets. RSI 8 35/65 for volume:
# 1,782 window bets at 56.96%, 28,080 unloaded at 57.52% (the highest z in the
# sweep, +25.2), but a narrower band than the marginal favours and train halves
# 2.8pp apart. Smoothing (RSI 7 sk=2: 921 bets, 57.87% / 57.93%) trades a
# third of the bets for nothing out of sample. An ATR floor of 0.20% lifts the
# window hit to 57.87% and removes a third of the bets; not taken.
PRESETS.update({
    # 1,383 bets, 57.19% hit on 2026-03..09; unloaded 2017-08..2026-03 57.50%
    # on 21,602 bets (z +22.0); every full year 56.1-58.7%. Wilder's 30/70 band
    # on a 7-bar (105 min) RSI, unsmoothed — the knee of the bets/hit frontier.
    "PM 15m Balanced": {
        **_OSC_COMMON,
        "osc_type": "RSI", "osc_length": 7, "smooth_k": 1,
        "overbought": 70, "oversold": 30,
    },
})
