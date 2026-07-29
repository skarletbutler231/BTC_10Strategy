"""Momentum Indicators — nine oscillators on one scale, voting.

Idea
----
Momentum theory says the *rate* at which price is moving carries information the
price level does not: a move that is accelerating tends to continue, one that is
decelerating is running out of participants. Every classic momentum indicator is
a different way of measuring that same rate, and they disagree constantly —
mostly because they are measured on incomparable scales (RSI is 0-100, ROC is a
percent, MACD is in dollars).

So this strategy puts all nine on **one scale** and lets them vote. Each is
mapped to a momentum score in [-1, +1], where +1 is maximum bullish momentum:

    ROC          clamp(roc% / (norm x ATR%))      price-scale -> ATR-scale
    RSI          (rsi - 50) / 50
    Stochastic   (%K - 50) / 50
    Williams %R  (%R + 50) / 50
    CCI          clamp(cci / 200)
    Ultimate     (uo - 50) / 50
    MACD hist    clamp(hist / (norm x ATR))       price-scale -> ATR-scale
    TSI          tsi / 100
    Awesome      clamp(ao / (norm x ATR))         price-scale -> ATR-scale

The three that live in price units are divided by ATR, which is what makes them
comparable across 2017 and 2026 without refitting. The composite **M** is the
mean of whichever oscillators are enabled.

The single shared lookback
--------------------------
ROC, RSI, Stochastic, Williams %R, CCI and the Ultimate Oscillator all measure
one window, so they share one `osc_length` rather than carrying six near-
duplicate parameters. (Ultimate uses `osc_length/2 : osc_length : osc_length*2`,
which at the default 14 is exactly Wilder's 7/14/28.) Only the two-EMA family —
MACD, TSI, Awesome — keeps its own fast/slow pair, because there the gap between
the two lengths *is* the indicator.

Three ways to trade a momentum reading
--------------------------------------
`trigger_mode` picks which event fires, and each defines a momentum direction d:

  * **Extreme** — |M| reaches `score_threshold`. d = sign(M). The overbought /
    oversold reading. Fires only on the bar that first enters the zone, so one
    stretched move produces one bet rather than twenty.
  * **Zero Cross** — M changes sign. d = sign(M). The regime flip: momentum was
    negative, now it is positive.
  * **Momentum Turn** — the *slope* of M over `accel_lag` bars changes sign while
    |M| >= `score_threshold`. d = sign of the new slope. This is the second
    derivative — momentum peaking and rolling over while still high, which is
    what "decelerating" actually means.

`min_agree` then requires that many individual oscillators to share the
composite's sign, so a reading carried by one outlier is discarded.

`predict_direction` decides what to do: **Follow Momentum** trades in direction
d (momentum persists), **Fade Momentum** trades against it (momentum exhausts).

In Polymarket up/down mode the Exit/Backtest params are unused and each signal
is a bet on the next candle's direction.

Parameter groups
----------------
Momentum Panel   use_roc .. use_ao
Lookbacks        osc_length, macd_*, tsi_*, ao_*
Scale            norm_atr_mult
Composite        min_agree, score_threshold
Trigger          trigger_mode, accel_lag
Decision         predict_direction  (Follow | Fade)
Volatility       vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter     (shared)
Window           (shared)
"""

from __future__ import annotations

from typing import List, Optional

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_TRIGGERS = ["Extreme", "Zero Cross", "Momentum Turn"]
_DIRECTIONS = ["Follow Momentum", "Fade Momentum"]

# (param key, short label) for every oscillator on the panel, in display order.
_OSCILLATORS = [
    ("use_roc", "ROC"), ("use_rsi", "RSI"), ("use_stoch", "Stoch"),
    ("use_willr", "Williams"), ("use_cci", "CCI"), ("use_uo", "Ultimate"),
    ("use_macd", "MACD"), ("use_tsi", "TSI"), ("use_ao", "Awesome"),
]

# CCI's conventional "stretched" band is +/-100; +/-200 is a fair full-scale.
_CCI_SCALE = 200.0


def _clamp(x: float) -> float:
    return 1.0 if x > 1.0 else -1.0 if x < -1.0 else x


def _sign(x: float, dead: float = 0.0) -> int:
    return 1 if x > dead else -1 if x < -dead else 0


class Momentum(Strategy):
    id = "momentum"
    name = "Momentum Indicators"
    description = ("Nine momentum oscillators (ROC, RSI, Stochastic, Williams %R, "
                   "CCI, Ultimate, MACD, TSI, Awesome) normalised onto one "
                   "-1..+1 scale and averaged into a composite — traded at an "
                   "extreme, on a zero cross, or when momentum rolls over.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Momentum Panel", [
                Param("use_roc", "Rate of Change", True, "bool",
                      help="Percent change over the shared lookback, divided by "
                           "ATR% so it is comparable across regimes."),
                Param("use_rsi", "RSI", True, "bool",
                      help="Wilder's RSI, recentred so 50 maps to zero momentum."),
                Param("use_stoch", "Stochastic %K", True, "bool",
                      help="Where the close sits in the lookback's range."),
                Param("use_willr", "Williams %R", True, "bool",
                      help="The same idea as Stochastic, measured from the high."),
                Param("use_cci", "CCI", True, "bool",
                      help="Typical price against its mean, scaled by mean deviation."),
                Param("use_uo", "Ultimate Oscillator", True, "bool",
                      help="Buying pressure across three horizons "
                           "(lookback/2 : lookback : lookback*2), weighted 4:2:1."),
                Param("use_macd", "MACD Histogram", True, "bool",
                      help="MACD minus its signal line, divided by ATR."),
                Param("use_tsi", "True Strength Index", True, "bool",
                      help="Double-smoothed momentum over double-smoothed "
                           "absolute momentum. Already bounded, so no ATR scaling."),
                Param("use_ao", "Awesome Oscillator", True, "bool",
                      help="Fast minus slow SMA of the bar midpoint, divided by ATR."),
            ]),
            ParamGroup("Lookbacks", [
                Param("osc_length", "Shared Lookback", 14, "int", 2, 500, 1,
                      "One window for ROC, RSI, Stochastic, Williams %R, CCI and "
                      "Ultimate — they all measure the same span, so they share "
                      "the parameter rather than carrying six copies of it."),
                Param("macd_fast", "MACD Fast", 12, "int", 2, 200, 1, "MACD fast EMA."),
                Param("macd_slow", "MACD Slow", 26, "int", 3, 400, 1, "MACD slow EMA."),
                Param("macd_signal", "MACD Signal", 9, "int", 1, 100, 1, "MACD signal EMA."),
                Param("tsi_long", "TSI Long", 25, "int", 2, 300, 1, "TSI first smoothing."),
                Param("tsi_short", "TSI Short", 13, "int", 2, 200, 1, "TSI second smoothing."),
                Param("ao_fast", "Awesome Fast", 5, "int", 2, 200, 1, "Awesome fast SMA."),
                Param("ao_slow", "Awesome Slow", 34, "int", 3, 400, 1, "Awesome slow SMA."),
            ]),
            ParamGroup("Scale", [
                Param("norm_atr_mult", "Full Scale (xATR)", 2.0, "float", 0.1, 20.0, 0.1,
                      "How many ATRs of movement count as maximum momentum for the "
                      "three price-unit oscillators (ROC, MACD, Awesome). Lower "
                      "saturates them sooner; it does not affect the six that are "
                      "already bounded."),
            ]),
            ParamGroup("Composite", [
                Param("min_agree", "Min Oscillators Agreeing", 5, "int", 1, 9, 1,
                      "How many enabled oscillators must share the composite's "
                      "sign. Clamped to the number actually enabled, so it can "
                      "never mute the strategy outright."),
                Param("score_threshold", "Score Threshold", 0.30, "float", 0.0, 1.0, 0.01,
                      "|composite| needed for an Extreme or Momentum Turn signal. "
                      "Unused by Zero Cross, which is defined by the crossing."),
            ]),
            ParamGroup("Trigger", [
                Param("trigger_mode", "Trigger", "Extreme", "enum", options=_TRIGGERS,
                      help="Extreme: the composite reaches the threshold. Zero "
                           "Cross: it changes sign. Momentum Turn: its slope "
                           "changes sign while the reading is still extreme."),
                Param("accel_lag", "Slope Lag (bars)", 3, "int", 1, 100, 1,
                      "Bars used to measure the composite's slope for Momentum "
                      "Turn. Larger is smoother and later."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Fade Momentum", "enum",
                      options=_DIRECTIONS,
                      help="Follow Momentum trades in the direction momentum "
                           "points (it persists). Fade Momentum trades against it "
                           "(it exhausts)."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback. Scales the price-unit oscillators and sizes "
                      "TP/SL in TP/SL mode."),
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

    def _scores(self, candles: List[dict], p: dict, atr: List) -> List[List[Optional[float]]]:
        """Per-bar list of enabled oscillator scores, each in [-1, +1]."""
        n = len(candles)
        closes = [c["close"] for c in candles]
        L = p["osc_length"]
        norm = p["norm_atr_mult"]
        series: List[List] = []

        if p["use_roc"]:
            raw = ind.roc(closes, L)
            # ROC is a percent; ATR% puts it on the same footing in any regime.
            out: List = [None] * n
            for i in range(n):
                a, r = atr[i], raw[i]
                if a is None or r is None or a <= 0:
                    continue
                atr_pct = a / closes[i] * 100.0
                if atr_pct > 0:
                    out[i] = _clamp(r / (norm * atr_pct))
            series.append(out)
        if p["use_rsi"]:
            series.append([None if v is None else _clamp((v - 50.0) / 50.0)
                           for v in ind.rsi(candles, L)])
        if p["use_stoch"]:
            k, _ = ind.stochastic(candles, L, 3)
            series.append([None if v is None else _clamp((v - 50.0) / 50.0) for v in k])
        if p["use_willr"]:
            series.append([None if v is None else _clamp((v + 50.0) / 50.0)
                           for v in ind.williams_r(candles, L)])
        if p["use_cci"]:
            series.append([None if v is None else _clamp(v / _CCI_SCALE)
                           for v in ind.cci(candles, L)])
        if p["use_uo"]:
            series.append([None if v is None else _clamp((v - 50.0) / 50.0)
                           for v in ind.ultimate_oscillator(
                               candles, max(2, L // 2), L, L * 2)])
        if p["use_macd"]:
            _, _, hist = ind.macd(closes, p["macd_fast"], p["macd_slow"], p["macd_signal"])
            series.append([None if (h is None or a is None or a <= 0)
                           else _clamp(h / (norm * a)) for h, a in zip(hist, atr)])
        if p["use_tsi"]:
            series.append([None if v is None else _clamp(v / 100.0)
                           for v in ind.tsi(closes, p["tsi_long"], p["tsi_short"])])
        if p["use_ao"]:
            raw = ind.awesome_oscillator(candles, p["ao_fast"], p["ao_slow"])
            series.append([None if (v is None or a is None or a <= 0)
                           else _clamp(v / (norm * a)) for v, a in zip(raw, atr)])
        return series

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        enabled = sum(1 for k, _ in _OSCILLATORS if p[k])
        if enabled == 0:
            return []
        need = max(1, min(p["min_agree"], enabled))

        atr = ind.atr(candles, p["vol_atr_length"])
        series = self._scores(candles, p, atr)
        names = [lbl for k, lbl in _OSCILLATORS if p[k]]

        # Composite momentum per bar: the mean of the enabled scores, plus how
        # many of them agree with its sign. None until every enabled oscillator
        # has warmed up, so the composite never shifts meaning mid-run.
        comp: List[Optional[float]] = [None] * n
        agree: List[int] = [0] * n
        for i in range(n):
            vals = [s[i] for s in series]
            if any(v is None for v in vals):
                continue
            m = sum(vals) / len(vals)
            comp[i] = m
            sg = _sign(m)
            agree[i] = sum(1 for v in vals if _sign(v) == sg) if sg else 0

        mode = p["trigger_mode"]
        thr = p["score_threshold"]
        lag = p["accel_lag"]
        follow = p["predict_direction"] == "Follow Momentum"
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_logic"] == "With Trend"
        trend_ma = (common.moving_average(common.source_values(candles, p["source"]),
                                          p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)
        use_window = bool(p["use_trading_window"])
        allowed = common.allowed_days(p)
        win_start, win_end = common.window_minutes(p)

        # Slope of the composite, for Momentum Turn.
        slope: List[Optional[float]] = [None] * n
        if mode == "Momentum Turn":
            for i in range(lag, n):
                a, b = comp[i], comp[i - lag]
                if a is not None and b is not None:
                    slope[i] = a - b

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            m = comp[i]
            if m is None or i == 0:
                continue
            prev = comp[i - 1]
            if prev is None:
                continue

            # --- does this bar fire, and which way is momentum pointing? ------
            if mode == "Zero Cross":
                s_now, s_prev = _sign(m), _sign(prev)
                if s_now == 0 or s_prev == 0 or s_now == s_prev:
                    continue
                d = s_now
            elif mode == "Momentum Turn":
                sl, sl_prev = slope[i], slope[i - 1]
                if sl is None or sl_prev is None or abs(m) < thr:
                    continue
                s_now, s_prev = _sign(sl), _sign(sl_prev)
                if s_now == 0 or s_prev == 0 or s_now == s_prev:
                    continue
                d = s_now
            else:                                   # Extreme
                if abs(m) < thr:
                    continue
                # First bar into the zone only — otherwise one stretched move
                # would fire on every bar it stays stretched.
                if abs(prev) >= thr and _sign(prev) == _sign(m):
                    continue
                d = _sign(m)
            if d == 0:
                continue

            if agree[i] < need:
                continue

            a = atr[i]
            if a is None or a <= 0:
                continue
            cl = c["close"]
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue
            if use_window and not common.in_window(c["time"], allowed, win_start, win_end):
                continue

            side = ("long" if d > 0 else "short") if follow else \
                   ("short" if d > 0 else "long")

            if use_trend and not common.trend_ok(side, cl, trend_ma[i], trend_with):
                continue

            act = "Follow" if follow else "Fade"
            reason = (f"{mode}: composite {m:+.3f} ({agree[i]}/{enabled} agree) "
                      f"-> {act} {side.upper()} (ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"composite": round(m, 4), "agree": agree[i],
                      "enabled": enabled, "oscillators": names,
                      "trigger": mode, "momentum_dir": d,
                      "slope": None if slope[i] is None else round(slope[i], 4),
                      "atr_pct": round(atr_pct, 3)},
            ))
        return signals


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
# 1,644 configurations in three stages (1,125 structural, 432 fine, 87 subset and
# filter), each scored by calling this module's own `generate_signals`. Selection
# was mechanical: train bets >= the tier floor, both halves of train >= 52%,
# `osc_length` off the boundary of the union grid, then highest train hit rate.
#
# RESULTS — flat $1 per bet, next-candle direction, whole record
# --------------------------------------------------------------
#   preset       bets     hit      z | unswept 17-24 |  2yr    train  HOLDOUT
#   Volume     33,061  57.18%  +26.1 | 23,350 57.98% | 55.25%  54.93%  55.86%
#   Balanced   12,765  58.46%  +19.1 |  8,623 59.17% | 56.98%  57.40%  56.10%
#   Selective   1,124  62.81%   +8.6 |    634 62.93% | 62.65%  64.26%  59.24%
#
# **Balanced is the pick.** Volume trades 2.6x as often for 1.3pp less edge;
# Selective posts the best headline and is the least trustworthy number here
# (see caveat 3). Every full calendar year from 2018 on clears 55.4% on all
# three.
#
# THE FIVE THINGS THE SWEEP ACTUALLY FOUND
# -----------------------------------------
# 1. **Fade, not follow.** Pooled over stage 1, Fade 50.23% train / 51.50%
#    holdout against Follow 49.62% / 48.43% — and every Follow config that
#    looked good on train fell to 47-49% out of sample. Momentum theory's
#    headline claim, that a fast move keeps going, is simply false at the 5m
#    horizon. What is true is the exhaustion half of the same theory.
#
# 2. **Only the Extreme trigger works.** Pooled, train / holdout:
#
#        Extreme         52.16% / 53.72%
#        Momentum Turn   49.62% / 50.19%
#        Zero Cross      49.31% / 51.04%
#
#    This is worth dwelling on, because "momentum is decelerating" is the more
#    sophisticated-sounding idea and it is the one that carries nothing. The
#    second derivative — the composite's slope rolling over while the reading is
#    still extreme — is indistinguishable from noise. So is the zero cross. All
#    of the edge is in the plain, unfashionable observation that the reading is
#    stretched.
#
# 3. **Stochastic %K and Williams %R are the same number.** Over one window,
#    %K = (close - LL) / (HH - LL) x 100 and %R = %K - 100, so after recentring
#    they normalise to an identical score — verified equal to 2e-16 on random
#    data. Two of the nine "independent" oscillators are one measurement wearing
#    two names, which means the default panel silently double-weights it. The
#    presets leave both on because that is what the sweep selected and the
#    duplication is harmless once it is known about; turning Williams off gives
#    a 6-measure panel that scores within noise of it.
#
# 4. **RSI carries the panel.** One oscillator at a time, at the Balanced
#    settings over the two years:
#
#        RSI alone        5,531 bets  56.28%      Awesome alone   8,611  51.68%
#        Ultimate alone   6,183 bets  54.50%      Stochastic      41,994  51.28%
#        CCI alone       20,625 bets  52.07%      ROC alone       38,165  50.32%
#        (MACD and TSI alone almost never reach the threshold: 163 and 19 bets)
#
#    Against the full nine-oscillator composite at 4,088 bets and 56.65%. So
#    eight further momentum indicators, averaged in, buy about 0.4pp over RSI on
#    its own. The composite is not useless — it is more selective and slightly
#    better — but anyone expecting nine indicators to be nine independent
#    opinions should look at that table. ROC, the purest expression of momentum
#    in the whole family, is a coin flip by itself.
#
# 5. **`min_agree` does nothing.** 1 / 5 / 9 give 54.12 / 54.12 / 54.14% pooled
#    over stage 2. Once |composite| >= 0.5 the oscillators already agree almost
#    by construction, so the vote threshold has nothing left to reject. It is
#    kept as a parameter because it is meaningful at low thresholds; it is set
#    to 1 in every preset because at these thresholds it is inert.
#
# A NOTE ON THE TREND FILTER. Fading a momentum extreme is *definitionally*
# against the short-term trend: the composite is only stretched positive when
# price has just run up, so the bet is short and price is above its 50-SMA.
# "Against Trend / SMA50" therefore passes 100% of signals and "With Trend /
# SMA50" passes 0% — the filter is redundant at that length and is left off in
# Volume and Balanced. Only a much slower MA can disagree, which is exactly what
# Selective's With-Trend EMA200 exploits: it keeps the ~12% of fades that happen
# while price is on the *other* side of the 200-EMA.
#
# CAVEATS, in order of how much they should worry you
# ----------------------------------------------------
# 1. **2017 breaks it.** The partial year 2017 (Aug-Dec) scores 45.5 / 46.7 /
#    53.8% — well below chance on 80-1,739 bets. Fading exhaustion loses in a
#    parabolic run, which is precisely what 2017 was. Every mean-reversion
#    strategy in this repo fails in the same year; treat that as the shared
#    regime risk, not as three independent warnings.
# 2. **The edge decays.** 2018-2023 runs 57-64% across the three presets;
#    2024-2026 runs 55-57%. The recent numbers are the live estimate.
# 3. **Selective is NOT RECOMMENDED.** Its two train halves score 55.4% and
#    69.3% — a 14pp spread that no amount of holdout can make respectable — and
#    it shrinks 64.26% -> 59.24% into the holdout on 157 bets. At ~125 bets a
#    year it cannot prove itself either way. Shipped to show where the frontier
#    ends, not as a pick.
# 4. Bets run slightly short-heavy (Balanced: 5,814 long at 59.13%, 6,951 short
#    at 57.89%). Both sides win, and the share of 5m candles closing up is
#    49.6-50.5% every year, so this is not direction beta — but it is a tilt.
# 5. A bet pays only when hit rate > the price paid. Balanced's 57.0% over the
#    fitted window needs a fill below ~0.57, which a real Polymarket book will
#    not offer on a directional 5m market. The hit rate is the finding; the EV
#    figure the dashboard prints is an upper bound.
# 6. Days and times are UTC; a bar is stamped by its open time.
_MOM_COMMON = {
    # The whole panel on. See finding 3 — Stochastic and Williams %R are the
    # same measurement, so this is 8 distinct readings, not 9.
    "use_roc": True, "use_rsi": True, "use_stoch": True, "use_willr": True,
    "use_cci": True, "use_uo": True, "use_macd": True, "use_tsi": True,
    "use_ao": True,
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "tsi_long": 25, "tsi_short": 13, "ao_fast": 5, "ao_slow": 34,
    "min_agree": 1,                      # inert at these thresholds — finding 5
    "trigger_mode": "Extreme",           # the only trigger that works — finding 2
    "accel_lag": 3,                      # unused outside Momentum Turn
    "predict_direction": "Fade Momentum",  # not Follow — finding 1
    "atr_pct_min": 0.05, "atr_pct_max": 1.5,
    "use_trading_window": False,
}

PRESETS: dict = {
    # 33,061 bets, 57.18% hit (z +26.1); 55.25% over the two years it was fitted
    # to, 55.86% on the holdout, 57.98% across the never-swept 2017-2024. A
    # longer lookback and a lower bar: the widest net, the thinnest edge.
    "PM 5m Volume": {
        **_MOM_COMMON,
        "osc_length": 10, "score_threshold": 0.50, "norm_atr_mult": 4.0,
        "vol_atr_length": 14,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # *** THE PICK. *** 12,765 bets, 58.46% hit (z +19.1); train 57.40%, holdout
    # 56.10%, never-swept 2017-2024 59.17%, and every full year from 2018 at or
    # above 56.9%. A 5-bar lookback with a high bar (0.70) — only genuinely
    # stretched readings.
    "PM 5m Balanced": {
        **_MOM_COMMON,
        "osc_length": 5, "score_threshold": 0.70, "norm_atr_mult": 1.0,
        "vol_atr_length": 50,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # 1,124 bets, 62.81% hit (z +8.6) — the best headline here and the least
    # trustworthy. Balanced plus a With-Trend EMA200 filter, which keeps only
    # the fades that fire on the far side of the 200-EMA. Its two train halves
    # are 55.4% and 69.3%; see caveat 3. NOT RECOMMENDED.
    "PM 5m Selective": {
        **_MOM_COMMON,
        "osc_length": 5, "score_threshold": 0.70, "norm_atr_mult": 1.0,
        "vol_atr_length": 50,
        "use_trend_filter": True, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
}
