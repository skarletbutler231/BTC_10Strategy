"""BB Squeeze — Bollinger Band squeeze breakout / reversion.

Idea
----
When Bollinger Bands contract (a "squeeze"), volatility is coiled and a sharp
expansion often follows. This strategy watches %B (where price sits inside the
bands) while the band *bandwidth* is in a low percentile of its recent range,
then fires in the direction chosen by the Decision group:

  * **Breakout**  — price pushing through the upper band (%B >= upper) goes LONG;
    through the lower band (%B <= lower) goes SHORT. (Trade the expansion.)
  * **Reversion** — the mirror: %B >= upper fades SHORT, %B <= lower fades LONG.

A stack of optional filters refines entries — a squeeze gate, an EMA-slope bias,
a candle-body filter, a volatility band, an allowed trading-window (weekday +
UTC time-of-day), and an MA trend filter.

Parameter groups (match the config screen)
-------------------------------------------
Bollinger Bands   bb_length, bb_mult, pctb_upper, pctb_lower
Squeeze           bw_lookback, bw_squeeze_pct, require_squeeze
EMA Bias          ema_bias_length, ema_bias_slope_bars, use_ema_bias
Body Filter       min_body_ratio
Volatility Filter vol_atr_length, vol_min_atr_pct, vol_max_atr_pct
Decision          predict_direction  (Breakout | Reversion)
Trading Window    use_trading_window, trade_mon..trade_sun, start/end hour+minute
Trend Filter      use_trend_filter, trend_logic, ma_type, ma_length, source

TP/SL sizing uses the volatility-filter ATR (Vol ATR Length), fed to the shared
Exit/Backtest group like every other strategy.
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy


class BBSqueeze(Strategy):
    id = "bb_squeeze"
    name = "BB Squeeze"
    description = ("Bollinger Band squeeze: trade the expansion (breakout) or fade "
                   "the band tag (reversion), gated by squeeze percentile, EMA bias, "
                   "body, volatility, trend and a trading-window filter.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Bollinger Bands", [
                Param("bb_length", "BB Length", 20, "int", 2, 400, 1,
                      "Lookback for the Bollinger basis (SMA) and standard deviation."),
                Param("bb_mult", "BB Multiplier", 2.0, "float", 0.5, 6.0, 0.1,
                      "Band width in standard deviations from the basis."),
                Param("pctb_upper", "%B Upper Threshold", 1.0, "float", 0.0, 1.5, 0.01,
                      "%B at/above this tags the upper band (%B = 1 is the band itself)."),
                Param("pctb_lower", "%B Lower Threshold", 0.0, "float", -0.5, 1.0, 0.01,
                      "%B at/below this tags the lower band (%B = 0 is the band itself)."),
            ]),
            ParamGroup("Squeeze", [
                Param("bw_lookback", "BW Percentile Lookback", 100, "int", 10, 1000, 1,
                      "Window over which bandwidth is ranked into a percentile."),
                Param("bw_squeeze_pct", "BW Squeeze Percentile", 20, "float", 1, 100, 1,
                      "In-squeeze when current bandwidth percentile <= this."),
                Param("require_squeeze", "Require Squeeze", True, "bool",
                      help="Only take entries while bandwidth is in a squeeze."),
            ]),
            ParamGroup("EMA Bias", [
                Param("ema_bias_length", "EMA Bias Length", 50, "int", 2, 400, 1,
                      "EMA used to judge directional bias."),
                Param("ema_bias_slope_bars", "EMA Bias Slope Bars", 3, "int", 1, 100, 1,
                      "Compare the EMA to its value this many bars ago for slope."),
                Param("use_ema_bias", "Use EMA Bias", False, "bool",
                      help="Longs only when the EMA slopes up; shorts only when it slopes down."),
            ]),
            ParamGroup("Body Filter", [
                Param("min_body_ratio", "Min Body Ratio", 0.3, "float", 0.0, 1.0, 0.01,
                      "Minimum |close-open| / (high-low): reject indecisive candles."),
            ]),
            ParamGroup("Volatility Filter", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also sizes TP/SL (xATR) for this strategy."),
                Param("vol_min_atr_pct", "Vol Min ATR%", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("vol_max_atr_pct", "Vol Max ATR%", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (too violent)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Breakout", "enum",
                      options=["Breakout", "Reversion"],
                      help="Breakout trades the band push; Reversion fades the band tag."),
            ]),
            common.trading_window_group(),
            common.trend_filter_group(),
        ]

    def presets(self) -> dict:
        # --- Polymarket 5m, day-aware sweep -----------------------------------------
        # Whole DB (936,829 5m bars, 2017-08 .. 2026-07), Polymarket up/down mode. Two
        # families of three tiers: all-days and weekend-gated (Sat+Sun, UTC) via the
        # shared Allowed Trading Window group.
        #
        # Admission: hit >50% in every calendar year THAT HAS AT LEAST 25 BETS, overall
        # z >= 2.5, and 2024-26 must still clear 52%. The 25-bet floor matters: 2017 is
        # a partial year (Aug-Dec) and on thin presets holds too little to be evidence.
        #
        #   preset             bets     hit    worst yr  2024-26  2025-26      z
        #   Volume            18,619   58.04%     50.43%    55.42%    54.97%   21.9
        #   Balanced           6,737   59.54%     50.86%    57.94%    57.76%   15.7
        #   Hi Hit             1,612   62.22%     52.91%    61.88%    63.06%    9.8
        #   Wknd Volume        2,960   60.30%     50.83%    58.59%    62.22%   11.2
        #   Wknd Balanced      1,119   61.04%     52.38%    66.27%    64.25%    7.4
        #   Wknd Hi Hit          684   63.16%     56.63%    68.90%    68.64%    6.9
        #
        # The weekend gate is real but MILD here, and weaker recently. Parameters held
        # fixed on the old Polymarket 5m (Reversion) / (Hi Hit) presets: weekend
        # +1.33pp (z=+2.18) and +1.31pp (z=+2.50) over the full record, but only
        # +1.34pp (z=+1.13) and +0.98pp (z=+0.96) over 2024-26 -- no longer significant.
        # The Wknd tiers below are consequently part day effect, part parameters fitted
        # to weekend bars; do not read their gap over the all-days tiers as pure edge.
        #
        # CAVEAT: selection used the FULL record with NO holdout, so these hit rates
        # carry selection bias and the 2024-26 / 2025-26 columns are a recency check,
        # not out-of-sample evidence. Days are UTC; a bar is stamped by its open time.
        return {
            # --- Tuned on BTCUSDT 15m, 2026-06-21..07-21 (set the interval to 15m
            #     to reproduce). These use a tight TP / wide SL, which lifts win
            #     rate but is fragile out-of-sample — validate before trusting.
            "Hi Win-Rate (15m)": {
                "predict_direction": "Breakout", "require_squeeze": True,
                "bb_length": 20, "bb_mult": 2.5, "pctb_upper": 1.0, "pctb_lower": 0.0,
                "min_body_ratio": 0.3, "bw_squeeze_pct": 50, "use_ema_bias": False,
                "tp_atr_mult": 0.5, "sl_atr_mult": 3.0, "max_hold_bars": 12,
            },
            "Robust (15m)": {
                "predict_direction": "Breakout", "require_squeeze": False,
                "bb_length": 20, "bb_mult": 2.5, "pctb_upper": 1.0, "pctb_lower": 0.0,
                "min_body_ratio": 0.3, "bw_squeeze_pct": 50, "use_ema_bias": False,
                "tp_atr_mult": 0.5, "sl_atr_mult": 3.0, "max_hold_bars": 48,
            },
            # Tuned on BTCUSDT 5m, 2026-06-21..07-21 for a balance of high win rate
            # AND high PnL (set the interval to 5m). ~83% win, ~+5% month, PF ~1.7.
            "Hi Win + PnL (5m)": {
                "predict_direction": "Breakout", "require_squeeze": True,
                "bb_length": 14, "bb_mult": 2.5, "pctb_upper": 0.85, "pctb_lower": 0.15,
                "min_body_ratio": 0.3, "bw_squeeze_pct": 25, "use_ema_bias": True,
                "tp_atr_mult": 1.0, "sl_atr_mult": 3.0, "max_hold_bars": 48,
            },
            # For the Polymarket 5-min UP/DOWN mode (set Mode = "Polymarket up/down",
            # interval 5m). Reversion had the most robust next-candle directional edge
            # across Jun/Jul/May 2026 (~54-55% hit vs ~50% base). Exit params are
            # unused in that mode. Edge is thin -- profitable only below ~0.545 odds.
            "Polymarket 5m (Reversion)": {
                "predict_direction": "Reversion", "require_squeeze": False,
                "bb_length": 20, "bb_mult": 2.5, "pctb_upper": 1.0, "pctb_lower": 0.0,
                "min_body_ratio": 0.3, "use_ema_bias": False,
            },
            # Higher-hit-rate variant: fades only when price closes BEYOND the bands
            # (%B > 1.1 / < -0.1) -- a stronger reversion signal. Validated across
            # Jul/Jun/May 2026: ~55%/54.7%/58.5% hit (vs the base ~54.6/54.9/57.5),
            # ~400 bets/month. ~55-56% is about the robust ceiling for BTC 5m.
            "Polymarket 5m (Hi Hit)": {
                "predict_direction": "Reversion", "require_squeeze": False,
                "bb_length": 20, "bb_mult": 2.0, "pctb_upper": 1.1, "pctb_lower": -0.1,
                "min_body_ratio": 0.0, "use_ema_bias": False,
            },
            # Hi Hit restricted to the best weekdays. A day-of-week study over the
            # FULL history (45,251 bets, 2017-2026) found the reversion edge is
            # strongest Wed/Thu/Sat and weakest Monday -- consistent across
            # 2017-26, 2024-26 and 2025-26 (thin weekend/midweek liquidity mean-
            # reverts more). Hit rate by day-set (all history | last 12 months):
            #   all days     57.5% | 56.6%
            #   Wed+Thu+Sat  58.1% | 58.2%   <- this preset
            #   Mon (worst)  56.2% | ~52% recently
            "Polymarket 5m (Best Days)": {
                "predict_direction": "Reversion", "require_squeeze": False,
                "bb_length": 20, "bb_mult": 2.0, "pctb_upper": 1.1, "pctb_lower": -0.1,
                "min_body_ratio": 0.0, "use_ema_bias": False,
                "use_trading_window": True,
                "trade_mon": False, "trade_tue": False, "trade_wed": True,
                "trade_thu": True, "trade_fri": False, "trade_sat": True,
                "trade_sun": False,
                "start_hour": 0, "start_minute": 0, "end_hour": 23, "end_minute": 59,
            },
            "Squeeze Breakout": {
                "predict_direction": "Breakout", "require_squeeze": True,
                "bw_squeeze_pct": 20, "pctb_upper": 1.0, "pctb_lower": 0.0,
                "use_ema_bias": True, "min_body_ratio": 0.4,
            },
            "Mean Reversion": {
                "predict_direction": "Reversion", "require_squeeze": False,
                "pctb_upper": 1.0, "pctb_lower": 0.0, "use_ema_bias": False,
                "min_body_ratio": 0.1, "vol_max_atr_pct": 1.0,
            },
            "Trend-Filtered Breakout": {
                "predict_direction": "Breakout", "require_squeeze": True,
                "bw_squeeze_pct": 25, "use_ema_bias": True,
                "use_trend_filter": True, "trend_logic": "With Trend",
                "ma_type": "EMA", "ma_length": 200, "min_body_ratio": 0.45,
            },
            # 18,619 bets, 58.04% hit; 2024-26 55.42%, worst year 50.43%.
            "PM 5m Volume": {
                "bb_length": 20, "bb_mult": 2.0, "pctb_upper": 1.0,
                "pctb_lower": 0.0, "bw_lookback": 100, "bw_squeeze_pct": 20,
                "require_squeeze": False, "use_ema_bias": False,
                "ema_bias_length": 50, "ema_bias_slope_bars": 5,
                "min_body_ratio": 0.2, "vol_atr_length": 14,
                "vol_min_atr_pct": 0.05, "vol_max_atr_pct": 1.5,
                "predict_direction": 'Reversion', "use_trading_window": False,
                "start_hour": 0, "start_minute": 0, "end_hour": 23,
                "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close',
            },
            # 6,737 bets, 59.54% hit; 2024-26 57.94%, worst year 50.86%.
            "PM 5m Balanced": {
                "bb_length": 20, "bb_mult": 2.0, "pctb_upper": 1.05,
                "pctb_lower": -0.05, "bw_lookback": 100, "bw_squeeze_pct": 20,
                "require_squeeze": False, "use_ema_bias": False,
                "ema_bias_length": 50, "ema_bias_slope_bars": 5,
                "min_body_ratio": 0.2, "vol_atr_length": 50,
                "vol_min_atr_pct": 0.2, "vol_max_atr_pct": 3.0,
                "predict_direction": 'Reversion', "use_trading_window": False,
                "start_hour": 0, "start_minute": 0, "end_hour": 23,
                "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close',
            },
            # 1,612 bets, 62.22% hit; 2024-26 61.88%, worst year 52.91%.
            "PM 5m Hi Hit": {
                "bb_length": 20, "bb_mult": 2.5, "pctb_upper": 1.05,
                "pctb_lower": -0.05, "bw_lookback": 100, "bw_squeeze_pct": 20,
                "require_squeeze": False, "use_ema_bias": False,
                "ema_bias_length": 50, "ema_bias_slope_bars": 5,
                "min_body_ratio": 0.2, "vol_atr_length": 50,
                "vol_min_atr_pct": 0.2, "vol_max_atr_pct": 3.0,
                "predict_direction": 'Reversion', "use_trading_window": False,
                "start_hour": 0, "start_minute": 0, "end_hour": 23,
                "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close',
            },
            # 2,960 bets, 60.30% hit; 2024-26 58.59%, worst year 50.83%.
            "PM 5m Wknd Volume": {
                "bb_length": 50, "bb_mult": 2.5, "pctb_upper": 1.05,
                "pctb_lower": -0.05, "bw_lookback": 200, "bw_squeeze_pct": 40,
                "require_squeeze": True, "use_ema_bias": False,
                "ema_bias_length": 50, "ema_bias_slope_bars": 5,
                "min_body_ratio": 0.0, "vol_atr_length": 14,
                "vol_min_atr_pct": 0.05, "vol_max_atr_pct": 1.5,
                "predict_direction": 'Reversion', "use_trading_window": True,
                "start_hour": 0, "start_minute": 0, "end_hour": 23,
                "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200, "source": 'close', "trade_mon": False,
                "trade_tue": False, "trade_wed": False, "trade_thu": False,
                "trade_fri": False, "trade_sat": True, "trade_sun": True,
            },
            # 1,119 bets, 61.04% hit; 2024-26 66.27%, worst year 52.38%.
            "PM 5m Wknd Balanced": {
                "bb_length": 50, "bb_mult": 2.5, "pctb_upper": 1.05,
                "pctb_lower": -0.05, "bw_lookback": 100, "bw_squeeze_pct": 20,
                "require_squeeze": False, "use_ema_bias": True,
                "ema_bias_length": 200, "ema_bias_slope_bars": 5,
                "min_body_ratio": 0.0, "vol_atr_length": 50,
                "vol_min_atr_pct": 0.05, "vol_max_atr_pct": 1.5,
                "predict_direction": 'Reversion', "use_trading_window": True,
                "start_hour": 0, "start_minute": 0, "end_hour": 23,
                "end_minute": 59, "use_trend_filter": False,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close', "trade_mon": False, "trade_tue": False,
                "trade_wed": False, "trade_thu": False, "trade_fri": False,
                "trade_sat": True, "trade_sun": True,
            },
            # 684 bets, 63.16% hit; 2024-26 68.90%, worst year 56.63%.
            "PM 5m Wknd Hi Hit": {
                "bb_length": 50, "bb_mult": 2.5, "pctb_upper": 1.05,
                "pctb_lower": -0.05, "bw_lookback": 100, "bw_squeeze_pct": 20,
                "require_squeeze": False, "use_ema_bias": True,
                "ema_bias_length": 200, "ema_bias_slope_bars": 5,
                "min_body_ratio": 0.2, "vol_atr_length": 14,
                "vol_min_atr_pct": 0.0, "vol_max_atr_pct": 20.0,
                "predict_direction": 'Reversion', "use_trading_window": True,
                "start_hour": 0, "start_minute": 0, "end_hour": 23,
                "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200, "source": 'close', "trade_mon": False,
                "trade_tue": False, "trade_wed": False, "trade_thu": False,
                "trade_fri": False, "trade_sat": True, "trade_sun": True,
            },
        }

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        closes = [c["close"] for c in candles]

        # --- Bollinger Bands, %B and bandwidth -------------------------------
        bb_len = p["bb_length"]
        bb_mult = p["bb_mult"]
        basis = ind.sma(closes, bb_len)
        std = ind.rolling_std(closes, bb_len)
        pctb: List = [None] * n
        bandwidth: List = [None] * n
        for i in range(n):
            if basis[i] is None or std[i] is None:
                continue
            dev = bb_mult * std[i]
            upper, lower = basis[i] + dev, basis[i] - dev
            width = upper - lower
            if width > 0:
                pctb[i] = (closes[i] - lower) / width
            if basis[i] != 0:
                bandwidth[i] = width / basis[i]
        bw_rank = ind.rolling_percentile_rank(bandwidth, p["bw_lookback"])

        # --- helper series ---------------------------------------------------
        atr_vol = ind.atr(candles, p["vol_atr_length"])
        ema_bias = ind.ema(closes, p["ema_bias_length"])
        slope_bars = p["ema_bias_slope_bars"]
        trend_ma = common.moving_average(
            common.source_values(candles, p["source"]), p["ma_type"], p["ma_length"]) \
            if p["use_trend_filter"] else [None] * n

        # --- decision / filter config ---------------------------------------
        breakout = p["predict_direction"] == "Breakout"
        pctb_up, pctb_dn = p["pctb_upper"], p["pctb_lower"]
        require_sq = p["require_squeeze"]
        sq_pct = p["bw_squeeze_pct"]
        use_bias = p["use_ema_bias"]
        min_body = p["min_body_ratio"]
        vmin, vmax = p["vol_min_atr_pct"], p["vol_max_atr_pct"]
        use_window = p["use_trading_window"]
        allowed_days = common.allowed_days(p)
        start_min, end_min = common.window_minutes(p)
        use_trend = p["use_trend_filter"]
        with_trend = p["trend_logic"] == "With Trend"

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            b, a = pctb[i], atr_vol[i]
            if b is None or a is None or a <= 0:
                continue

            # Squeeze gate
            if require_sq:
                r = bw_rank[i]
                if r is None or r > sq_pct:
                    continue

            o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
            rng = h - l
            if rng <= 0:
                continue

            # Body filter
            if abs(cl - o) / rng < min_body:
                continue

            # Volatility band
            atr_pct = a / cl * 100.0
            if atr_pct < vmin or atr_pct > vmax:
                continue

            # Raw direction from %B + decision mode
            if b >= pctb_up:
                side = "long" if breakout else "short"
                edge = "upper"
            elif b <= pctb_dn:
                side = "short" if breakout else "long"
                edge = "lower"
            else:
                continue

            # EMA-slope bias
            if use_bias:
                e_now = ema_bias[i]
                e_prev = ema_bias[i - slope_bars] if i - slope_bars >= 0 else None
                if e_now is None or e_prev is None:
                    continue
                slope = e_now - e_prev
                if side == "long" and slope <= 0:
                    continue
                if side == "short" and slope >= 0:
                    continue

            # Trend filter
            if use_trend and not common.trend_ok(side, cl, trend_ma[i], with_trend):
                continue

            # Trading window (weekday + UTC time-of-day, wrap-aware)
            if use_window and not common.in_window(c["time"], allowed_days, start_min, end_min):
                continue

            mode = "breakout" if breakout else "reversion"
            reason = (f"%B {b:.2f} @ {edge} band -> {mode} {side.upper()} "
                      f"(BW pct {bw_rank[i]:.0f}, ATR% {atr_pct:.2f})"
                      if bw_rank[i] is not None else
                      f"%B {b:.2f} @ {edge} band -> {mode} {side.upper()} (ATR% {atr_pct:.2f})")

            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"pctb": round(b, 3),
                      "bw_pct": round(bw_rank[i], 1) if bw_rank[i] is not None else None,
                      "atr_pct": round(atr_pct, 3), "edge": edge, "mode": mode},
            ))
        return signals
