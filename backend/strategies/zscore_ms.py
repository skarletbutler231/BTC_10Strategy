"""Zscore MS — Z-Score Mean-Shift (strategy #5).

Idea
----
A z-score says how many standard deviations price sits from its own mean:

    z = (close - SMA(close, z_sma_length)) / StdDev(close, z_std_length)

A large |z| means price is statistically stretched. On its own that is noisy, so
the stretch is optionally confirmed by a **Keltner Channel** break (an ATR-based
envelope): a genuine dislocation should be extended on *both* a statistical
(z-score) and a volatility (ATR) measure. The Decision group then picks whether
to fade the stretch (**Reversion**, the classic mean-shift play) or ride it
(**Momentum**).

The SMA and StdDev lookbacks are deliberately separate: a short mean with a
longer deviation window measures "far from recent price, relative to normal
volatility", which behaves differently from a single-window z-score.

Parameter groups
----------------
Z-Score           z_sma_length, z_std_length, z_upper, z_lower
Keltner Channel   kc_ema_length, kc_atr_length, kc_mult, require_kc_break
Bias MA           bias_ema_length, bias_slope_lookback, use_bias_ma
Volatility Filter vol_atr_length, vol_min_atr_pct, vol_max_atr_pct
Decision          predict_direction (Reversion | Momentum)
Trading Window    use_trading_window, trade_mon..trade_sun, start/end hour+minute
Trend Filter      use_trend_filter, trend_logic, ma_type, ma_length, source

Entry logic for a HIGH stretch (mirror for a low stretch)
---------------------------------------------------------
  1. z >= z_upper                                  (statistically stretched)
  2. if require_kc_break: close > Keltner upper    (volatility-confirmed)
  3. Volatility regime: ATR% within [min, max]
  4. if use_bias_ma: the bias EMA must slope the way the trade points
  5. if use_trend_filter: price must agree (or disagree) with the trend MA
  6. if use_trading_window: the bar must fall in an allowed weekday + UTC window
  -> Reversion: SHORT (fade)   |   Momentum: LONG (ride)

TP/SL sizing (TP/SL mode) uses the volatility-filter ATR, like the other
strategies. In Polymarket up/down mode only the direction is used.
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy


class ZScoreMS(Strategy):
    id = "zscore_ms"
    name = "Zscore MS"
    description = ("Z-score mean-shift: fade (or ride) statistically stretched price, "
                   "optionally confirmed by a Keltner Channel break and gated by bias, "
                   "volatility, trend and trading-window filters.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Z-Score", [
                Param("z_sma_length", "Z-Score SMA Length", 20, "int", 2, 500, 1,
                      "Lookback for the mean the z-score measures distance from."),
                Param("z_std_length", "Z-Score StdDev Length", 20, "int", 2, 500, 1,
                      "Lookback for the standard deviation that scales the z-score."),
                Param("z_upper", "Z-Score Upper Threshold", 2.0, "float", 0.1, 10.0, 0.1,
                      "z at/above this is a HIGH stretch."),
                Param("z_lower", "Z-Score Lower Threshold", -2.0, "float", -10.0, -0.1, 0.1,
                      "z at/below this is a LOW stretch."),
            ]),
            ParamGroup("Keltner Channel", [
                Param("kc_ema_length", "KC EMA Length", 20, "int", 2, 500, 1,
                      "EMA basis of the Keltner Channel."),
                Param("kc_atr_length", "KC ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback for the Keltner envelope width."),
                Param("kc_mult", "KC Multiplier", 1.5, "float", 0.1, 10.0, 0.1,
                      "Envelope width in ATRs from the EMA basis."),
                Param("require_kc_break", "Require KC Break", True, "bool",
                      help="Also require price to close outside the Keltner Channel, "
                           "confirming the z-score stretch on a volatility basis."),
            ]),
            ParamGroup("Bias MA", [
                Param("bias_ema_length", "Bias EMA Length", 50, "int", 2, 500, 1,
                      "EMA used to judge directional bias."),
                Param("bias_slope_lookback", "Bias Slope Lookback", 3, "int", 1, 100, 1,
                      "Compare the bias EMA to its value this many bars ago."),
                Param("use_bias_ma", "Use Bias MA", False, "bool",
                      help="Longs only when the bias EMA slopes up; shorts only when it slopes down."),
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
                Param("predict_direction", "Predict Direction", "Reversion", "enum",
                      options=["Reversion", "Momentum"],
                      help="Reversion fades the stretch; Momentum rides it."),
            ]),
            common.trading_window_group(),
            common.trend_filter_group(),
        ]

    def presets(self) -> dict:
        return {
            # Tuned for the Polymarket 5-min UP/DOWN mode (set Mode = "Polymarket
            # up/down", interval 5m). Chosen by next-candle directional hit rate
            # validated across Jul/Jun/May 2026: 55.6% / 56.3% / 55.6% (~400
            # bets/month) -- the most consistent config found, not a one-month
            # spike. Exit params are unused in that mode.
            "Polymarket 5m (Reversion)": {
                "predict_direction": "Reversion",
                "z_sma_length": 30, "z_std_length": 100,
                "z_upper": 2.0, "z_lower": -2.0,
                "require_kc_break": True, "kc_mult": 2.0,
                "use_bias_ma": False,
            },
            # Same signal, restricted to the best weekday. A day-of-week study
            # over the FULL history (39,735 bets, 2017-2026) found SATURDAY is
            # this strategy's strongest day in every era, and Monday the weakest:
            #        all 2017-26 | 2024-26 | 2025-26 | 2026
            #   Sat       58.08% |  58.60% |  58.38% | 57.43%   <- this preset
            #   Sun       56.87% |  56.07% |  55.35% | 58.28%
            #   all days  55.32% |  54.08% |  53.99% | 55.94%
            #   Mon       53.39% |  50.69% |  52.07% | 54.31%
            # Sat is ~3.7 sigma above the all-day average and holds in every era.
            # For ~2x the bets at ~1pp lower hit rate, also tick Sunday
            # (Sat+Sun: 57.38% all history, 10,756 bets).
            "Polymarket 5m (Best Day)": {
                "predict_direction": "Reversion",
                "z_sma_length": 30, "z_std_length": 100,
                "z_upper": 2.0, "z_lower": -2.0,
                "require_kc_break": True, "kc_mult": 2.0,
                "use_bias_ma": False,
                "use_trading_window": True,
                "trade_mon": False, "trade_tue": False, "trade_wed": False,
                "trade_thu": False, "trade_fri": False, "trade_sat": True,
                "trade_sun": False,
                "start_hour": 0, "start_minute": 0, "end_hour": 23, "end_minute": 59,
            },
            "Strict Reversion": {
                "predict_direction": "Reversion", "z_upper": 2.5, "z_lower": -2.5,
                "require_kc_break": True, "kc_mult": 1.5, "use_bias_ma": False,
            },
            "Loose Reversion": {
                "predict_direction": "Reversion", "z_upper": 1.5, "z_lower": -1.5,
                "require_kc_break": False, "use_bias_ma": False,
            },
            "Momentum": {
                "predict_direction": "Momentum", "z_upper": 2.0, "z_lower": -2.0,
                "require_kc_break": True, "kc_mult": 1.5, "use_bias_ma": True,
            },
        }

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        closes = [c["close"] for c in candles]

        # --- z-score ---------------------------------------------------------
        z_mean = ind.sma(closes, p["z_sma_length"])
        z_sd = ind.rolling_std(closes, p["z_std_length"])

        # --- Keltner Channel -------------------------------------------------
        need_kc = p["require_kc_break"]
        kc_basis = ind.ema(closes, p["kc_ema_length"]) if need_kc else [None] * n
        kc_atr = ind.atr(candles, p["kc_atr_length"]) if need_kc else [None] * n
        kc_mult = p["kc_mult"]

        # --- helper series ---------------------------------------------------
        atr_vol = ind.atr(candles, p["vol_atr_length"])
        use_bias = p["use_bias_ma"]
        bias_ema = ind.ema(closes, p["bias_ema_length"]) if use_bias else [None] * n
        slope_bars = p["bias_slope_lookback"]
        use_trend = p["use_trend_filter"]
        trend_ma = common.moving_average(
            common.source_values(candles, p["source"]), p["ma_type"], p["ma_length"]) \
            if use_trend else [None] * n

        # --- config ----------------------------------------------------------
        reversion = p["predict_direction"] == "Reversion"
        z_up, z_dn = p["z_upper"], p["z_lower"]
        vmin, vmax = p["vol_min_atr_pct"], p["vol_max_atr_pct"]
        use_window = p["use_trading_window"]
        allowed_days = common.allowed_days(p)
        start_min, end_min = common.window_minutes(p)
        with_trend = p["trend_logic"] == "With Trend"

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            m, sd, a = z_mean[i], z_sd[i], atr_vol[i]
            if m is None or sd is None or a is None or sd <= 0 or a <= 0:
                continue

            cl = c["close"]
            z = (cl - m) / sd

            # Stretch + direction
            if z >= z_up:
                side = "short" if reversion else "long"
                stretch = "high"
            elif z <= z_dn:
                side = "long" if reversion else "short"
                stretch = "low"
            else:
                continue

            # Keltner confirmation
            if need_kc:
                basis, katr = kc_basis[i], kc_atr[i]
                if basis is None or katr is None:
                    continue
                if stretch == "high" and cl <= basis + kc_mult * katr:
                    continue
                if stretch == "low" and cl >= basis - kc_mult * katr:
                    continue

            # Volatility regime
            atr_pct = a / cl * 100.0
            if atr_pct < vmin or atr_pct > vmax:
                continue

            # Bias MA slope
            if use_bias:
                e_now = bias_ema[i]
                e_prev = bias_ema[i - slope_bars] if i - slope_bars >= 0 else None
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

            # Trading window
            if use_window and not common.in_window(c["time"], allowed_days, start_min, end_min):
                continue

            mode = "reversion" if reversion else "momentum"
            reason = (f"z {z:+.2f} {stretch} stretch -> {mode} {side.upper()} "
                      f"(ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"z": round(z, 3), "stretch": stretch, "mode": mode,
                      "atr_pct": round(atr_pct, 3)},
            ))
        return signals
