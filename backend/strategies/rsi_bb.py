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
        # Tuned on 5 regime-diverse years of 5m BTC (2021-2025). Two findings
        # drive every preset:
        #   * The WITH-TREND gate is essential: with the trend filter off the raw
        #     RSI+BB fade has NEGATIVE gross expectancy (it loses even at zero
        #     fees), so the trend filter is ON in all presets.
        #   * The edge is many small trend-gated mean-reversions -> gross return
        #     rises with trade frequency, but so does fee drag. The presets are a
        #     frequency <-> fee-sensitivity spectrum, not a random/curve-fit set.
        # Numbers below are the 2021-2025 sum of yearly returns (5m BTC).
        return {
            # More, quicker fades. Strongest GROSS edge (~+50% over 2021-2025,
            # positive every year) but only at low cost -> best where fees are
            # tiny (maker/VIP) or for judging raw signal quality. Heavy fee drag
            # at taker rates.
            "High-Frequency": {
                "direction": "Both",
                "rsi_overbought": 65, "rsi_oversold": 35,
                "bb_mult": 1.9, "pctb_upper": 0.85, "pctb_lower": 0.15,
                "min_wick_ratio": 0.10, "min_close_recovery": 0.25,
                "use_bias_filter": False,
                "use_trend_filter": True, "trend_logic": "With Trend",
                "ma_type": "EMA", "ma_length": 200, "ma_source": "close",
                "atr_pct_min": 0.05, "atr_pct_max": 1.8,
                "tp_atr_mult": 1.2, "sl_atr_mult": 1.6, "max_hold_bars": 10,
                "fee_bps": 5,
            },
            # Recommended all-rounder (matches the param defaults). Moderate
            # frequency; solid gross edge (~+15%, 4/5 years) with less fee drag
            # than High-Frequency.
            "Balanced": {
                "direction": "Both",
                "rsi_overbought": 68, "rsi_oversold": 32,
                "bb_mult": 2.0, "pctb_upper": 0.90, "pctb_lower": 0.10,
                "min_wick_ratio": 0.15, "min_close_recovery": 0.30,
                "use_bias_filter": False,
                "use_trend_filter": True, "trend_logic": "With Trend",
                "ma_type": "EMA", "ma_length": 200, "ma_source": "close",
                "atr_pct_min": 0.05, "atr_pct_max": 1.6,
                "tp_atr_mult": 1.5, "sl_atr_mult": 1.5, "max_hold_bars": 12,
                "fee_bps": 5,
            },
            # Long-only dip buyer for spot accounts that can't/won't short: buys
            # oversold pullbacks in an uptrend only. Fewer trades and lower
            # variance, but note the two-sided presets net more gross here --
            # this is a directional-preference option, not a performance play.
            "Long-Only Dips": {
                "direction": "Long Only",
                "rsi_overbought": 68, "rsi_oversold": 33,
                "bb_mult": 2.0, "pctb_upper": 0.90, "pctb_lower": 0.10,
                "min_wick_ratio": 0.15, "min_close_recovery": 0.30,
                "use_bias_filter": False,
                "use_trend_filter": True, "trend_logic": "With Trend",
                "ma_type": "EMA", "ma_length": 200, "ma_source": "close",
                "atr_pct_min": 0.05, "atr_pct_max": 1.8,
                "tp_atr_mult": 1.5, "sl_atr_mult": 1.5, "max_hold_bars": 12,
                "fee_bps": 5,
            },
            # Fewest, highest-conviction fades: stricter RSI/%B, tight vol ceiling
            # and wider symmetric targets. Lowest gross edge but the least
            # fee-sensitive and smallest drawdowns -> the closest to break-even
            # once realistic taker fees are applied.
            "Selective": {
                "direction": "Both",
                "rsi_overbought": 70, "rsi_oversold": 30,
                "bb_mult": 2.0, "pctb_upper": 0.95, "pctb_lower": 0.05,
                "min_wick_ratio": 0.20, "min_close_recovery": 0.35,
                "use_bias_filter": False,
                "use_trend_filter": True, "trend_logic": "With Trend",
                "ma_type": "EMA", "ma_length": 200, "ma_source": "close",
                "atr_pct_min": 0.05, "atr_pct_max": 1.2,
                "tp_atr_mult": 1.5, "sl_atr_mult": 1.5, "max_hold_bars": 12,
                "fee_bps": 5,
            },

            # --- Polymarket 5-minute UP/DOWN mode -----------------------------
            # Run these with Mode = "Polymarket up/down" (interval 5m). Each
            # signal is an independent bet that the NEXT 5m candle closes in the
            # faded direction (long = UP, short = DOWN); TP/SL/fee are ignored.
            # Two things flip vs the TP/SL presets: the trend gate is best set to
            # AGAINST TREND (contrarian fading of stretches away from the MA
            # predicts the next candle far better than with-trend), and the candle
            # needs only a recovery close (min_close_recovery 0.3), no wick.
            # Tuned on 6 months of 5m BTC and validated on 7 disjoint months
            # (13 months / ~680-850 bets total). Base rate of an up-candle is
            # ~50%, so the hit rates below are a real directional edge. Note the
            # single-side and ultra-selective variants did NOT survive the
            # holdout, so only these two robust two-sided presets are shipped.

            # ~58.9% hit over 13 months (12/13 months >50%), ~53 bets/mo.
            # EV-positive buying shares at any price up to ~0.58. The flagship.
            "Polymarket 5m (Reversion)": {
                "direction": "Both",
                "rsi_oversold": 35, "rsi_overbought": 65,
                "bb_mult": 2.5, "pctb_upper": 1.0, "pctb_lower": 0.0,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.30,
                "use_bias_filter": False,
                "use_trend_filter": True, "trend_logic": "Against Trend",
                "ma_type": "EMA", "ma_length": 200, "ma_source": "close",
                "atr_pct_min": 0.03, "atr_pct_max": 5.0,
            },
            # ~57.2% hit over 13 months (11/13 >50%), ~66 bets/mo -> more action
            # for a slightly lower hit. Fades one %B step BEYOND the bands with no
            # trend filter and a small ATR% floor. EV-positive up to ~0.57.
            "Polymarket 5m (More Bets)": {
                "direction": "Both",
                "rsi_oversold": 35, "rsi_overbought": 65,
                "bb_mult": 2.0, "pctb_upper": 1.1, "pctb_lower": -0.1,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.30,
                "use_bias_filter": False,
                "use_trend_filter": False,
                "atr_pct_min": 0.08, "atr_pct_max": 5.0,
            },
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

            # 15m markets (Mode = "Polymarket up/down", interval 15m). Real edge
            # but CHOPPIER than 5m: ~57.3% hit over 13 months yet only 8/13 were
            # winning months (worst ~35%), so size stakes conservatively. ~38
            # bets/mo, less-extreme RSI (40/60) since 15m candles reach extremes
            # less often. EV-positive up to ~0.57. (1m was analysed and rejected:
            # weaker ~54% edge, a 47.9% base up-rate that flags a microstructure
            # artifact rather than real edge, and no 1m market on Polymarket.)
            "Polymarket 15m (Reversion)": {
                "direction": "Both",
                "rsi_oversold": 40, "rsi_overbought": 60,
                "bb_mult": 2.0, "pctb_upper": 1.1, "pctb_lower": -0.1,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.30,
                "use_bias_filter": False,
                "use_trend_filter": False,
                "atr_pct_min": 0.08, "atr_pct_max": 5.0,
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
