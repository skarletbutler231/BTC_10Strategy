"""Strategy #1 from the video: RSI + Bollinger Bands ("fade the band stretch").

Idea
----
A classic mean-reversion fade. When price stretches to a Bollinger Band *and*
momentum is at an extreme (RSI) *and* the bar shows an intrabar rejection wick
that closes back off the extreme (a recovery/hammer bar), the stretch is often
exhausted. We fade it: long a stretched-below oversold bar, short a
stretched-above overbought bar, and target a mean reversion toward the basis.

A Direction control restricts trading to one side (e.g. long-only dip-buying for
spot accounts that can't short). On top of the core trigger there are three
optional gates that trade robustness for selectivity: a volatility regime
filter, an EMA-slope "bias" filter, and a moving-average "trend" filter.

Parameter groups (matching the config shown in the video)
---------------------------------------------------------
Direction        direction  (Both | Long Only | Short Only)
RSI              rsi_length, rsi_overbought, rsi_oversold
Bollinger Bands  bb_length, bb_mult, pctb_upper, pctb_lower
Candle           min_wick_ratio, min_close_recovery
Bias Filter      use_bias_filter, bias_ema_length, bias_slope_bars
Volatility       vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter     use_trend_filter, trend_logic, ma_type, ma_length, ma_source

Entry logic for a LONG (mirror for a SHORT)
-------------------------------------------
  1. Momentum extreme:  RSI(rsi_length) <= rsi_oversold.
  2. Band stretch:      %B = (close-lower)/(upper-lower) <= pctb_lower, i.e. the
     close is pinned to / below the lower Bollinger Band.
  3. Rejection wick:    lower wick / bar range >= min_wick_ratio  (the lows were
     bought back intrabar).
  4. Close recovery:    (close-low) / bar range >= min_close_recovery  (the bar
     closed well off its low -> a reversal candle, not a continuation dump).
  5. Volatility regime: ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max].
  6. Bias filter (opt):  EMA(bias_ema_length) must be RISING over bias_slope_bars
     -> only buy dips while the trend bias is up.
  7. Trend filter (opt): MA(ma_type, ma_length, ma_source) vs price, per
     `trend_logic` (With Trend: price>MA for longs; Against Trend: price<MA).

All active conditions must hold -> emit a LONG (fade). The SHORT is the mirror:
RSI >= rsi_overbought, %B >= pctb_upper, upper wick, close near the high's
rejection, bias down, trend side flipped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup, Signal, Strategy

# Enum option lists. _MA_TYPES / _SOURCES are ordered so a list index equals the
# integer code the matching indicators helper expects (see ind.MA_TYPE_LABELS /
# ind.SOURCE_LABELS), which keeps the string<->code mapping trivial.
_DIRECTIONS = ["Both", "Long Only", "Short Only"]
_TREND_LOGIC = ["With Trend", "Against Trend"]
_MA_TYPES = ["SMA", "EMA", "WMA", "RMA", "HMA"]
_SOURCES = ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"]
_DAYS = ["trade_mon", "trade_tue", "trade_wed", "trade_thu",
         "trade_fri", "trade_sat", "trade_sun"]  # index == datetime.weekday()

# Saturday + Sunday only; used by the weekend-gated Polymarket presets.
_WEEKEND = {k: (k in ("trade_sat", "trade_sun")) for k in _DAYS}


class RsiBb(Strategy):
    id = "rsi_bb"
    name = "RSI + BB"
    description = ("Fade Bollinger-Band stretches confirmed by an RSI extreme "
                  "and a rejection/recovery candle, with optional volatility, "
                  "bias-slope and trend-MA filters.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Direction", [
                Param("direction", "Direction", "Both", "enum", options=_DIRECTIONS,
                      help="Which sides to trade. Long Only buys oversold dips "
                           "(spot-friendly); Short Only fades overbought rips. "
                           "'Both' nets the most on the tuned presets; the "
                           "single-side options are for a directional preference."),
            ]),
            ParamGroup("RSI", [
                Param("rsi_length", "RSI Length", 14, "int", 2, 100, 1,
                      "Lookback for RSI."),
                Param("rsi_overbought", "RSI Overbought", 68, "float", 50, 100, 1,
                      "Shorts only fire at/above this RSI."),
                Param("rsi_oversold", "RSI Oversold", 32, "float", 0, 50, 1,
                      "Longs only fire at/below this RSI."),
            ]),
            ParamGroup("Bollinger Bands", [
                Param("bb_length", "BB Length", 20, "int", 2, 200, 1,
                      "Lookback for the BB basis (SMA of close) and stdev."),
                Param("bb_mult", "BB Multiplier", 2.0, "float", 0.5, 6.0, 0.1,
                      "Band half-width in standard deviations."),
                Param("pctb_upper", "%B Upper", 0.90, "float", 0.5, 1.5, 0.01,
                      "Shorts require %B >= this (1.0 = at the upper band)."),
                Param("pctb_lower", "%B Lower", 0.10, "float", -0.5, 0.5, 0.01,
                      "Longs require %B <= this (0.0 = at the lower band)."),
            ]),
            ParamGroup("Candle", [
                Param("min_wick_ratio", "Min Wick Ratio", 0.15, "float", 0.0, 1.0, 0.01,
                      "Minimum rejection wick as a fraction of the bar range."),
                Param("min_close_recovery", "Min Close Recovery", 0.30, "float", 0.0, 1.0, 0.01,
                      "Minimum fraction the close recovers off the extreme "
                      "(1 = closed at the opposite end of the bar)."),
            ]),
            ParamGroup("Bias Filter", [
                Param("use_bias_filter", "Use Bias Filter", False, "bool",
                      help="Require the bias EMA to slope with the trade "
                           "(up for longs, down for shorts)."),
                Param("bias_ema_length", "Bias EMA Length", 50, "int", 2, 400, 1,
                      "Lookback for the bias EMA (on close)."),
                Param("bias_slope_bars", "Bias Slope Bars", 5, "int", 1, 100, 1,
                      "Bars back used to measure the EMA slope sign."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "Lookback for the ATR (drives both the regime filter and "
                      "TP/SL sizing)."),
                Param("atr_pct_min", "Min ATR %", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "Max ATR %", 1.6, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent, trending regime). "
                      "Tightening this ceiling trades away signals for robustness "
                      "in bear / high-vol tape."),
            ]),
            ParamGroup("Trend Filter", [
                Param("use_trend_filter", "Use Trend Filter", True, "bool",
                      help="Gate entries on price vs the trend MA. On by default: "
                           "the with-trend gate is the single biggest robustness "
                           "lever in backtests."),
                Param("trend_logic", "Trend Logic", "With Trend", "enum", options=_TREND_LOGIC,
                      help="With Trend: longs need price>MA, shorts price<MA. "
                           "Against Trend: the opposite."),
                Param("ma_type", "MA Type", "EMA", "enum", options=_MA_TYPES,
                      help="Moving-average type for the trend filter."),
                Param("ma_length", "MA Length", 200, "int", 2, 500, 1,
                      "Lookback for the trend MA."),
                Param("ma_source", "Source", "close", "enum", options=_SOURCES,
                      help="Price source for the trend MA."),
            ]),
            ParamGroup("Day of Week (UTC)", [
                Param(_DAYS[i], lbl, True, "bool",
                      help=f"Allow entries on {lbl} (UTC). Band fades resolve "
                           f"better at the weekend than midweek — see the "
                           f"Polymarket presets.")
                for i, lbl in enumerate(("Monday", "Tuesday", "Wednesday",
                                         "Thursday", "Friday", "Saturday",
                                         "Sunday"))
            ]),
        ]

    def presets(self) -> dict:
        return {
            # --- Polymarket 5m, day-aware sweep (2026) ------------------------
            # A 15,552-combination sweep over the WHOLE DB (936,829 5m bars,
            # 2017-08 .. 2026-07), scored in Polymarket up/down mode. Two families
            # of three tiers: all-days, and weekend-gated. Admission: hit >50% in
            # every calendar year, overall z >= 2.5, and the 2024-26 span must
            # still clear 52% so nothing already dead gets shipped.
            #
            # The day-of-week question
            # ------------------------
            # Band fades resolve better at the WEEKEND than midweek. Measured on
            # the two pre-existing PM presets, weekend (Sat+Sun) vs weekday:
            # +2.71pp (z=+2.17) and +2.87pp (z=+2.55) over the full history, and
            # still +1.8 / +1.4pp over 2024-26 and +1.8 / +2.0pp over 2025-26.
            # Monday is the worst day in both presets on both spans.
            #
            # Saturday ALONE looks even better on the full history (+3.21 / +3.48pp)
            # but that edge has DECAYED: over 2024-26 Saturday is -1.46 / -1.14pp,
            # i.e. negative, while Sunday became the strongest day. Gating on
            # Saturday would be fitting to stale history, so the presets gate on
            # the weekend as a pair, which is positive on every span. (Contrast
            # Jump Exhaustion, where Saturday specifically does still hold.)
            #
            # Measured results (whole DB, flat $1 per bet)
            # -------------------------------------------
            #   preset            bets     hit   worst yr   2024-26   2025-26     z
            #   Volume          22,569  58.31%    51.54%     56.82%    56.59%  25.0
            #   Balanced        10,977  58.70%    51.95%     57.16%    56.23%  18.2
            #   Hi Hit             734  64.03%    58.33%     69.33%    66.25%   7.6
            #   Wknd Volume      7,057  59.13%    53.41%     56.27%    56.41%  15.3
            #   Wknd Balanced    4,211  60.58%    54.91%     58.42%    59.16%  13.7
            #   Wknd Hi Hit        991  62.06%    56.58%     60.32%    57.50%   7.6
            #
            # Weekend gating beats all-days at the Volume and Balanced tiers
            # (59.13 vs 58.31, 60.58 vs 58.70) on roughly a third of the bets, so
            # it is a genuine quality-for-quantity trade rather than a free lunch.
            #
            # Two findings beyond the numbers
            # -------------------------------
            #   * **Long Only wins.** Four of the six tier winners, and both
            #     weekend Volume/Balanced tiers, are Long Only. Buying the
            #     oversold lower-band fade beats fading the overbought upper band
            #     on 5m BTC -- the short side dilutes the edge.
            #   * **The candle filters earn nothing.** Every winner sets
            #     min_wick_ratio = 0 AND min_close_recovery = 0. The rejection
            #     wick and recovery close are the most intuitive part of the
            #     setup and neither survives measurement, which extends the
            #     existing note above (that preset kept recovery at 0.30).
            #
            # Caveats
            # -------
            # 1. **No holdout.** These were selected on the full record, so the
            #    headline hit rates carry selection bias. The per-year and
            #    2024-26 / 2025-26 columns are in-sample too -- they are a
            #    recency check, not out-of-sample evidence. Budget a few points
            #    of shrinkage on anything live.
            # 2. **The Hi Hit tiers are thin**: 734 and 991 bets, ~80-110 a year.
            #    All Days Hi Hit shows 69.33% over 2024-26 but on only 150 bets
            #    (+/-4pp standard error); treat it as suggestive, not established.
            # 3. Days are **UTC** and a bar is stamped by its open time.
            # 4. 2017 is a partial year (Aug-Dec) and the thinnest sample.
            #
            # A bet pays only when hit rate > your odds: Wknd Balanced's 58.42%
            # over 2024-26 needs entry below ~0.5842.
            "PM 5m Volume": {
                "direction": "Long Only",
                "rsi_length": 7, "rsi_oversold": 30, "rsi_overbought": 70,
                "bb_length": 20, "bb_mult": 2.0,
                "pctb_upper": 1.1, "pctb_lower": -0.1,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_bias_filter": False, "use_trend_filter": False,
                "vol_atr_length": 14, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
            },
            "PM 5m Balanced": {
                "direction": "Both",
                "rsi_length": 7, "rsi_oversold": 25, "rsi_overbought": 75,
                "bb_length": 20, "bb_mult": 2.0,
                "pctb_upper": 0.9, "pctb_lower": 0.1,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_bias_filter": False,
                "use_trend_filter": True, "trend_logic": "With Trend",
                "ma_type": "EMA", "ma_length": 200, "ma_source": "close",
                "vol_atr_length": 14, "atr_pct_min": 0.08, "atr_pct_max": 5.0,
            },
            # Highest hit rate here, and the thinnest sample -- see caveat 2.
            "PM 5m Hi Hit": {
                "direction": "Both",
                "rsi_length": 7, "rsi_oversold": 25, "rsi_overbought": 75,
                "bb_length": 50, "bb_mult": 2.5,
                "pctb_upper": 1.1, "pctb_lower": -0.1,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_bias_filter": False,
                "use_trend_filter": True, "trend_logic": "With Trend",
                "ma_type": "EMA", "ma_length": 200, "ma_source": "close",
                "vol_atr_length": 14, "atr_pct_min": 0.08, "atr_pct_max": 5.0,
            },
            "PM 5m Wknd Volume": {
                "direction": "Long Only",
                "rsi_length": 7, "rsi_oversold": 25, "rsi_overbought": 75,
                "bb_length": 20, "bb_mult": 2.0,
                "pctb_upper": 1.0, "pctb_lower": 0.0,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_bias_filter": False,
                "use_trend_filter": True, "trend_logic": "Against Trend",
                "ma_type": "EMA", "ma_length": 100, "ma_source": "close",
                "vol_atr_length": 14, "atr_pct_min": 0.08, "atr_pct_max": 5.0,
                **_WEEKEND,
            },
            # The pick of the six: 60.58% over 4,211 bets, every year above 54.9%,
            # and the only preset whose 2025-26 number (59.16%) beats its 2024-26.
            "PM 5m Wknd Balanced": {
                "direction": "Long Only",
                "rsi_length": 14, "rsi_oversold": 35, "rsi_overbought": 65,
                "bb_length": 20, "bb_mult": 2.0,
                "pctb_upper": 1.1, "pctb_lower": -0.1,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_bias_filter": False,
                "use_trend_filter": True, "trend_logic": "Against Trend",
                "ma_type": "EMA", "ma_length": 200, "ma_source": "close",
                "vol_atr_length": 14, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
                **_WEEKEND,
            },
            "PM 5m Wknd Hi Hit": {
                "direction": "Long Only",
                "rsi_length": 14, "rsi_oversold": 30, "rsi_overbought": 70,
                "bb_length": 50, "bb_mult": 3.0,
                "pctb_upper": 1.1, "pctb_lower": -0.1,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_bias_filter": False, "use_trend_filter": False,
                "vol_atr_length": 14, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
                **_WEEKEND,
            },
            # --- Re-optimized for HIT RATE at similar volume, 2yr window ------
            # Same coarse grid as before (direction x rsi_oversold x
            # rsi_overbought x bb_mult; 360 combos), trailing 2 years only
            # (2024-07-19 -> 2026-07-19). CHANGED OBJECTIVE from the previous
            # version of this preset: instead of maximizing bet count, this
            # picks the combo whose bet count stays close to the full-history
            # "PM 5m Volume" preset's own trailing-2yr count (4,868 bets) while
            # maximizing hit rate, subject to both halves of the window
            # individually clearing 50%. Result: 7,154 bets (147% of baseline
            # -- needed a wide +/-60% band to find anything better) at 56.43%
            # hit (56.1% / 56.7% by half) vs. baseline's 4,868 bets at 56.39%.
            # CAVEAT: that's a +0.04pp edge over baseline -- noise, not a real
            # improvement (standard error at this n is roughly +/-1pp). Kept
            # as the best available, but don't read this as meaningfully
            # better than just using "PM 5m Volume" directly.
            "PM 5m Volume - 2yr Train": {
                "direction": "Both",
                "rsi_length": 7, "rsi_oversold": 30, "rsi_overbought": 80,
                "bb_length": 20, "bb_mult": 2.0,
                "pctb_upper": 1.1, "pctb_lower": -0.1,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_bias_filter": False, "use_trend_filter": False,
                "vol_atr_length": 14, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
            },
            # -------------------------------------------------------------------------
            # 15-MINUTE preset, fitted on the latest six months of BTCUSDT
            # 15m under the protocol the Reversal, Oscillators and Gann 15m
            # presets use: LOADED 2026-03-13 -> 2026-09-13 (train 03-13 ->
            # 07-13 for selection, holdout 07-13 -> 09-13 scored once after
            # the pick was frozen); UNLOADED 2017-08-17 -> 2026-03-13, never
            # read by any sweep stage and scored once at the end as the real
            # out-of-sample check (29,560 bets there against 1,795 in the
            # window). The selection rule was fixed before tuning: train
            # bets >= 300, both train halves above 52%, every swept
            # parameter off its grid edge, then highest train hit — read
            # against the marginals, since at a few hundred bets a config
            # the SE is 1.5-2.5pp and the single best row is mostly noise.
            #
            # SWEEP. Stage 1 (225 configs) raced direction {Both, Long Only,
            # Short Only} x trend filter {off, With, Against EMA200} x
            # rsi_length {5..21} x band {20/80..40/60} at BB 20/2.0. Stage 2
            # (1,152) tuned rsi_length, band, bb_length {14..50}, bb_mult
            # {1.5..3.0} and the %B band inside the two surviving
            # directions. Stage 3 (~20) tried the ATR band, the candle
            # filters, the bias filter, the trend filter and a weekend gate
            # on the frozen pick. Candle filters at 0 throughout, as every
            # 5m winner set them.
            #
            # FOUND. The window favours the SHORT side (Short Only pooled
            # train 59.67% against Both 57.81%, holdout 55.47 vs 55.36) and
            # the 5m sweep found the opposite, Long Only. Neither survives
            # the other's years: Short Only scores 56.21% unloaded with 2017
            # at 46.1%, Long Only 57.69% unloaded but 55.92% in the window.
            # The side that wins is a regime, not a property of the setup,
            # so the preset trades both; it is Wilder's 30/70 band on a
            # 7-bar RSI with the stock 20/2.0 Bollinger and a 0-1 %B band,
            # i.e. the setup with nothing fitted but the length. Marginals:
            # rsi_length 5-10 flat (57.3-57.9% train), 14 loses the holdout;
            # bb_mult rises monotonically on train (1.5: 56.85 -> 3.0:
            # 58.72) but the wider band costs 6x the bets; bb_length 14-20
            # beat 30-50 on the holdout (56.4 / 55.8 vs 54.6 / 54.0). The
            # candle filters are actively harmful — min_close_recovery 0.3
            # drops the pick to 52.86% (holdout 49.55%) and min_wick_ratio
            # 0.3 to 53.03% — which extends the 5m finding that they earn
            # nothing. The ATR band, ATR length and bias filter are inert or
            # only remove bets.
            #
            # RESULTS — flat $1 per bet, next-candle direction
            #   preset             6m bets  6m hit   train   HOLDOUT   unloaded 8.5y             worst yr
            #   PM 15m Balanced     1,795  57.10%  57.43%   56.45%    56.94% (29,560, z +23.9)  55.28% (2022)
            #
            # Per year on the full record, none of it fitted except the last six months:
            #   2017  49.34% (1,058)     2018  57.16% (3,445)     2019  59.12% (3,383)     2020  58.66% (3,408)
            #   2021  56.48% (3,513)     2022  55.28% (3,569)     2023  58.31% (3,423)     2024  57.03% (3,635)
            #   2025  55.39% (3,430)     2026  57.85% (2,491)
            #
            # Train halves 59.76% / 55.17%. About 9.7 bets a day. The 18
            # months right before the window (2024-09 -> 2026-03) score
            # 56.32% on 5,201 bets; whole record 31,355 bets, 56.95%, z
            # +24.6. Read the hit rates against 49.9%: 0.13% of 15m candles
            # close exactly at their open. Checks after the pick was frozen:
            # the prefix (no look-ahead) test passes with 0 mismatches at
            # three cut points; bets run 49% long in the window and both
            # sides win (window long 55.92% / short 58.23%; unloaded 57.69%
            # / 56.21%).
            #
            # WHERE IT FAILS. The worst month in the window is 2026-08 at
            # 55.0% on 289 bets; the worst full year 2022 at 55.28%. Train
            # halves are 59.76 / 55.17%, and 2026-08 ran at 55.0%. Expect
            # weeks at 54-55%. The 0.50-odds EV the dashboard prints assumes
            # a fill at even; a real 15m book prices away from it. Hit rate
            # is the finding.
            #
            # NOT SHIPPED. The rule's own single best row (Both, RSI 10, BB
            # 20/2.5): 570 window bets at 59.12%, but 56.30% unloaded with
            # two years at 53.8% — it is on the same plateau on a third of
            # the bets. With Trend EMA200 on the pick: 337 bets at 61.42%
            # with train 61.40% and holdout 61.47%, a genuine high-hit
            # variant at a fifth of the volume. Weekend-only: 545 bets at
            # 58.17%. The 5m Wknd/Hi Hit families were not re-swept at 15m;
            # their presets carried over as-is produce 19-159 window bets.
            "PM 15m Balanced": {
                "direction": "Both",
                "rsi_length": 7, "rsi_oversold": 30, "rsi_overbought": 70,
                "bb_length": 20, "bb_mult": 2.0,
                "pctb_upper": 1.0, "pctb_lower": 0.0,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_bias_filter": False, "use_trend_filter": False,
                "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
            },
        }

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        rsi_len = p["rsi_length"]
        ob, os = p["rsi_overbought"], p["rsi_oversold"]
        bb_len, bb_mult = p["bb_length"], p["bb_mult"]
        pb_up, pb_lo = p["pctb_upper"], p["pctb_lower"]
        wick_min = p["min_wick_ratio"]
        rec_min = p["min_close_recovery"]

        direction = p["direction"]
        allow_long = direction != "Short Only"
        allow_short = direction != "Long Only"

        use_bias = bool(p["use_bias_filter"])
        bias_len = p["bias_ema_length"]
        slope_bars = p["bias_slope_bars"]

        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_logic"] == "With Trend"
        ma_type_code = _MA_TYPES.index(p["ma_type"]) if p["ma_type"] in _MA_TYPES else 1
        ma_len = p["ma_length"]
        ma_src_code = _SOURCES.index(p["ma_source"]) if p["ma_source"] in _SOURCES else 0

        # Day gate (UTC). Index matches datetime.weekday(): Monday == 0.
        allowed_days = {i for i in range(7) if p[_DAYS[i]]}
        gate_days = len(allowed_days) < 7

        closes = [c["close"] for c in candles]
        rsi = ind.rsi(candles, rsi_len)
        _, bb_hi, bb_lo = ind.bollinger(closes, bb_len, bb_mult)
        atr = ind.atr(candles, vol_len)
        bias = ind.ema(closes, bias_len) if use_bias else None
        if use_trend:
            src = ind.source(candles, ma_src_code)
            ma = ind.moving_average(src, ma_len, ma_type_code)
        else:
            src = ma = None

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            if gate_days and datetime.fromtimestamp(
                    c["time"], timezone.utc).weekday() not in allowed_days:
                continue
            r, up, lo, a = rsi[i], bb_hi[i], bb_lo[i], atr[i]
            if None in (r, up, lo, a) or a <= 0 or up <= lo:
                continue

            o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
            rng = h - l
            if rng <= 0:
                continue

            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            pct_b = (cl - lo) / (up - lo)
            up_wick = h - max(o, cl)
            dn_wick = min(o, cl) - l

            side = None
            if allow_long and r <= os and pct_b <= pb_lo:    # oversold @ lower band -> LONG
                if dn_wick / rng < wick_min:
                    continue
                if (cl - l) / rng < rec_min:
                    continue
                side, wick_frac = "long", dn_wick / rng
            elif allow_short and r >= ob and pct_b >= pb_up:  # overbought @ upper band -> SHORT
                if up_wick / rng < wick_min:
                    continue
                if (h - cl) / rng < rec_min:
                    continue
                side, wick_frac = "short", up_wick / rng
            else:
                continue

            # --- optional bias filter (EMA slope must agree with the trade) ---
            if use_bias:
                j = i - slope_bars
                if j < 0 or bias[i] is None or bias[j] is None:
                    continue
                slope = bias[i] - bias[j]
                if side == "long" and slope <= 0:
                    continue
                if side == "short" and slope >= 0:
                    continue

            # --- optional trend filter (price vs MA, per trend_logic) ---------
            if use_trend:
                m, sv = ma[i], src[i]
                if m is None:
                    continue
                price_agrees = (sv > m) if side == "long" else (sv < m)
                ok = price_agrees if trend_with else (not price_agrees)
                if not ok:
                    continue

            reason = (f"{'Oversold' if side == 'long' else 'Overbought'} band fade "
                      f"(RSI {r:.0f}, %B {pct_b:.2f}, wick {wick_frac:.0%}, "
                      f"ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"rsi": round(r, 1), "pct_b": round(pct_b, 3),
                      "wick": round(wick_frac, 2), "atr_pct": round(atr_pct, 3)},
            ))
        return signals
