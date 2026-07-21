"""Strategy #1 from the video: RSI + Bollinger Bands ("fade the band stretch").

Idea
----
A classic mean-reversion fade. When price stretches to a Bollinger Band *and*
momentum is at an extreme (RSI) *and* the bar shows an intrabar rejection wick
that closes back off the extreme (a recovery/hammer bar), the stretch is often
exhausted. We fade it: long a stretched-below oversold bar, short a
stretched-above overbought bar, and target a mean reversion toward the basis.

On top of the core trigger there are three optional gates that trade robustness
for selectivity: a volatility regime filter (skip dead / violent tape), an
EMA-slope "bias" filter, and a moving-average "trend" filter.

Parameter groups (matching the config shown in the video)
---------------------------------------------------------
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
     `trend_logic` (0 = with trend: price>MA for longs; 1 = against trend:
     price<MA for longs).

All active conditions must hold -> emit a LONG (fade). The SHORT is the mirror:
RSI >= rsi_overbought, %B >= pctb_upper, upper wick, close near the high's
rejection, bias down, trend side flipped.

Dropdown-style choices (trend_logic / ma_type / ma_source / the on-off toggles)
are integer-coded because the dashboard renders every param as a numeric input;
the code->label mapping is spelled out in each param's help text.
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup, Signal, Strategy


class RsiBb(Strategy):
    id = "rsi_bb"
    name = "RSI + BB"
    description = ("Fade Bollinger-Band stretches confirmed by an RSI extreme "
                  "and a rejection/recovery candle, with optional volatility, "
                  "bias-slope and trend-MA filters.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("RSI", [
                Param("rsi_length", "RSI length", 14, "int", 2, 100, 1,
                      "Lookback for RSI."),
                Param("rsi_overbought", "RSI overbought", 68, "float", 50, 100, 1,
                      "Shorts only fire at/above this RSI."),
                Param("rsi_oversold", "RSI oversold", 32, "float", 0, 50, 1,
                      "Longs only fire at/below this RSI."),
            ]),
            ParamGroup("Bollinger Bands", [
                Param("bb_length", "BB length", 20, "int", 2, 200, 1,
                      "Lookback for the BB basis (SMA of close) and stdev."),
                Param("bb_mult", "BB multiplier", 2.0, "float", 0.5, 6.0, 0.1,
                      "Band half-width in standard deviations."),
                Param("pctb_upper", "%B upper", 0.90, "float", 0.5, 1.5, 0.01,
                      "Shorts require %B >= this (1.0 = at the upper band)."),
                Param("pctb_lower", "%B lower", 0.10, "float", -0.5, 0.5, 0.01,
                      "Longs require %B <= this (0.0 = at the lower band)."),
            ]),
            ParamGroup("Candle", [
                Param("min_wick_ratio", "Min wick ratio", 0.15, "float", 0.0, 1.0, 0.01,
                      "Minimum rejection wick as a fraction of the bar range."),
                Param("min_close_recovery", "Min close recovery", 0.30, "float", 0.0, 1.0, 0.01,
                      "Minimum fraction the close recovers off the extreme "
                      "(1 = closed at the opposite end of the bar)."),
            ]),
            ParamGroup("Bias Filter", [
                Param("use_bias_filter", "Use bias filter", 0, "int", 0, 1, 1,
                      "0 = off, 1 = on. Require the bias EMA to slope with the "
                      "trade (up for longs, down for shorts)."),
                Param("bias_ema_length", "Bias EMA length", 50, "int", 2, 400, 1,
                      "Lookback for the bias EMA (on close)."),
                Param("bias_slope_bars", "Bias slope bars", 5, "int", 1, 100, 1,
                      "Bars back used to measure the EMA slope sign."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR length", 14, "int", 2, 200, 1,
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
                Param("use_trend_filter", "Use trend filter", 1, "int", 0, 1, 1,
                      "0 = off, 1 = on. Gate entries on price vs the trend MA. "
                      "On by default: the with-trend gate is the single biggest "
                      "robustness lever in backtests."),
                Param("trend_logic", "Trend logic", 0, "int", 0, 1, 1,
                      "0 = with trend (longs need price>MA, shorts price<MA); "
                      "1 = against trend (longs need price<MA, shorts price>MA)."),
                Param("ma_type", "MA type", 1, "int", 0, 4, 1,
                      "0=SMA, 1=EMA, 2=WMA, 3=RMA, 4=HMA."),
                Param("ma_length", "MA length", 200, "int", 2, 500, 1,
                      "Lookback for the trend MA."),
                Param("ma_source", "Source", 0, "int", 0, 6, 1,
                      "0=close, 1=open, 2=high, 3=low, 4=hl2, 5=hlc3, 6=ohlc4."),
            ]),
        ]

    def presets(self) -> dict:
        # Tuned on 5 regime-diverse years of 5m BTC (2021-2025). Two findings
        # drive every preset:
        #   * The WITH-TREND gate is essential: with the trend filter off the raw
        #     RSI+BB fade has NEGATIVE gross expectancy (it loses even at zero
        #     fees), so the trend filter is ON in all three presets.
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
                "rsi_overbought": 65, "rsi_oversold": 35,
                "bb_mult": 1.9, "pctb_upper": 0.85, "pctb_lower": 0.15,
                "min_wick_ratio": 0.10, "min_close_recovery": 0.25,
                "use_bias_filter": 0,
                "use_trend_filter": 1, "trend_logic": 0, "ma_type": 1,
                "ma_length": 200, "ma_source": 0,
                "atr_pct_min": 0.05, "atr_pct_max": 1.8,
                "tp_atr_mult": 1.2, "sl_atr_mult": 1.6, "max_hold_bars": 10,
                "fee_bps": 5,
            },
            # Recommended all-rounder (matches the param defaults). Moderate
            # frequency; solid gross edge (~+15%, 4/5 years) with less fee drag
            # than High-Frequency.
            "Balanced": {
                "rsi_overbought": 68, "rsi_oversold": 32,
                "bb_mult": 2.0, "pctb_upper": 0.90, "pctb_lower": 0.10,
                "min_wick_ratio": 0.15, "min_close_recovery": 0.30,
                "use_bias_filter": 0,
                "use_trend_filter": 1, "trend_logic": 0, "ma_type": 1,
                "ma_length": 200, "ma_source": 0,
                "atr_pct_min": 0.05, "atr_pct_max": 1.6,
                "tp_atr_mult": 1.5, "sl_atr_mult": 1.5, "max_hold_bars": 12,
                "fee_bps": 5,
            },
            # Fewest, highest-conviction fades: stricter RSI/%B, tight vol ceiling
            # and wider symmetric targets. Lowest gross edge but the least
            # fee-sensitive and smallest drawdowns -> the closest to break-even
            # once realistic taker fees are applied.
            "Selective": {
                "rsi_overbought": 70, "rsi_oversold": 30,
                "bb_mult": 2.0, "pctb_upper": 0.95, "pctb_lower": 0.05,
                "min_wick_ratio": 0.20, "min_close_recovery": 0.35,
                "use_bias_filter": 0,
                "use_trend_filter": 1, "trend_logic": 0, "ma_type": 1,
                "ma_length": 200, "ma_source": 0,
                "atr_pct_min": 0.05, "atr_pct_max": 1.2,
                "tp_atr_mult": 1.5, "sl_atr_mult": 1.5, "max_hold_bars": 12,
                "fee_bps": 5,
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

        use_bias = int(p["use_bias_filter"]) == 1
        bias_len = p["bias_ema_length"]
        slope_bars = p["bias_slope_bars"]

        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        use_trend = int(p["use_trend_filter"]) == 1
        trend_logic = int(p["trend_logic"])
        ma_type = int(p["ma_type"])
        ma_len = p["ma_length"]
        ma_src_code = int(p["ma_source"])

        closes = [c["close"] for c in candles]
        rsi = ind.rsi(candles, rsi_len)
        _, bb_hi, bb_lo = ind.bollinger(closes, bb_len, bb_mult)
        atr = ind.atr(candles, vol_len)
        bias = ind.ema(closes, bias_len) if use_bias else None
        if use_trend:
            src = ind.source(candles, ma_src_code)
            ma = ind.moving_average(src, ma_len, ma_type)
        else:
            src = ma = None

        signals: List[Signal] = []
        for i, c in enumerate(candles):
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
            if r <= os and pct_b <= pb_lo:              # oversold @ lower band -> LONG
                if dn_wick / rng < wick_min:
                    continue
                if (cl - l) / rng < rec_min:
                    continue
                side, wick_frac = "long", dn_wick / rng
            elif r >= ob and pct_b >= pb_up:            # overbought @ upper band -> SHORT
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
                with_trend = (sv > m) if side == "long" else (sv < m)
                ok = with_trend if trend_logic == 0 else (not with_trend)
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
