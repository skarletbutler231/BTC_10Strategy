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
        #   Volume            20,689   58.59%     51.20%    57.57%    57.49%   24.7
        #   Balanced          10,546   59.12%     51.56%    56.15%    55.54%   18.7
        #   Hi Hit               871   63.15%     57.14%    67.89%    65.14%    7.8
        #   Wknd Volume        6,048   59.06%     50.45%    59.31%    59.24%   14.1
        #   Wknd Balanced      3,068   58.87%     50.44%    57.23%    56.42%    9.8
        #   Wknd Hi Hit          738   63.28%     57.25%    70.59%    66.40%    7.2
        #
        # Strongest weekend evidence in the repo. Holding parameters fixed on the old
        # Polymarket 5m (Reversion) preset and splitting by day: weekend +2.85pp
        # (z=+5.08) over the full record, +3.91pp (z=+3.87) over 2024-26 and +3.19pp
        # (z=+2.45) over 2025-26 -- it is strengthening, not fading.
        #
        # CAVEAT: selection used the FULL record with NO holdout, so these hit rates
        # carry selection bias and the 2024-26 / 2025-26 columns are a recency check,
        # not out-of-sample evidence. Days are UTC; a bar is stamped by its open time.
        return {
            # 20,689 bets, 58.59% hit; 2024-26 57.57%, worst year 51.20%.
            "PM 5m Volume": {
                "z_sma_length": 20, "z_std_length": 20, "z_upper": 2.0,
                "z_lower": -2.0, "require_kc_break": True, "kc_ema_length": 20,
                "kc_atr_length": 20, "kc_mult": 1.5, "use_bias_ma": True,
                "bias_ema_length": 200, "bias_slope_lookback": 5,
                "vol_atr_length": 50, "vol_min_atr_pct": 0.05,
                "vol_max_atr_pct": 1.5, "predict_direction": 'Reversion',
                "use_trading_window": False, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": False,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close',
            },
            # 10,546 bets, 59.12% hit; 2024-26 56.15%, worst year 51.56%.
            "PM 5m Balanced": {
                "z_sma_length": 20, "z_std_length": 20, "z_upper": 2.0,
                "z_lower": -2.0, "require_kc_break": True, "kc_ema_length": 20,
                "kc_atr_length": 20, "kc_mult": 1.0, "use_bias_ma": False,
                "bias_ema_length": 50, "bias_slope_lookback": 5,
                "vol_atr_length": 50, "vol_min_atr_pct": 0.2,
                "vol_max_atr_pct": 3.0, "predict_direction": 'Reversion',
                "use_trading_window": False, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close',
            },
            # 871 bets, 63.15% hit; 2024-26 67.89%, worst year 57.14%.
            "PM 5m Hi Hit": {
                "z_sma_length": 50, "z_std_length": 50, "z_upper": 3.0,
                "z_lower": -3.0, "require_kc_break": True, "kc_ema_length": 20,
                "kc_atr_length": 20, "kc_mult": 1.5, "use_bias_ma": False,
                "bias_ema_length": 50, "bias_slope_lookback": 5,
                "vol_atr_length": 14, "vol_min_atr_pct": 0.05,
                "vol_max_atr_pct": 1.5, "predict_direction": 'Reversion',
                "use_trading_window": False, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close',
            },
            # 6,048 bets, 59.06% hit; 2024-26 59.31%, worst year 50.45%.
            "PM 5m Wknd Volume": {
                "z_sma_length": 50, "z_std_length": 50, "z_upper": 3.0,
                "z_lower": -3.0, "require_kc_break": True, "kc_ema_length": 20,
                "kc_atr_length": 20, "kc_mult": 1.5, "use_bias_ma": False,
                "bias_ema_length": 50, "bias_slope_lookback": 5,
                "vol_atr_length": 50, "vol_min_atr_pct": 0.05,
                "vol_max_atr_pct": 1.5, "predict_direction": 'Reversion',
                "use_trading_window": True, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": False,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close', "trade_mon": False, "trade_tue": False,
                "trade_wed": False, "trade_thu": False, "trade_fri": False,
                "trade_sat": True, "trade_sun": True,
            },
            # 3,068 bets, 58.87% hit; 2024-26 57.23%, worst year 50.44%.
            "PM 5m Wknd Balanced": {
                "z_sma_length": 50, "z_std_length": 50, "z_upper": 1.5,
                "z_lower": -1.5, "require_kc_break": True, "kc_ema_length": 20,
                "kc_atr_length": 20, "kc_mult": 1.5, "use_bias_ma": True,
                "bias_ema_length": 200, "bias_slope_lookback": 5,
                "vol_atr_length": 14, "vol_min_atr_pct": 0.0,
                "vol_max_atr_pct": 20.0, "predict_direction": 'Reversion',
                "use_trading_window": True, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200, "source": 'close', "trade_mon": False,
                "trade_tue": False, "trade_wed": False, "trade_thu": False,
                "trade_fri": False, "trade_sat": True, "trade_sun": True,
            },
            # 738 bets, 63.28% hit; 2024-26 70.59%, worst year 57.25%.
            "PM 5m Wknd Hi Hit": {
                "z_sma_length": 50, "z_std_length": 50, "z_upper": 3.0,
                "z_lower": -3.0, "require_kc_break": True, "kc_ema_length": 20,
                "kc_atr_length": 20, "kc_mult": 1.5, "use_bias_ma": True,
                "bias_ema_length": 200, "bias_slope_lookback": 5,
                "vol_atr_length": 14, "vol_min_atr_pct": 0.0,
                "vol_max_atr_pct": 20.0, "predict_direction": 'Reversion',
                "use_trading_window": True, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": False,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close', "trade_mon": False, "trade_tue": False,
                "trade_wed": False, "trade_thu": False, "trade_fri": False,
                "trade_sat": True, "trade_sun": True,
            },
            # --- Re-optimized for HIT RATE at similar volume, 2yr window ------
            # Same coarse grid as before (z_sma_length x z_upper/lower
            # magnitude x kc_mult x require_kc_break; 96 combos), trailing 2
            # years only (2024-07-19 -> 2026-07-19), objective changed to:
            # closest bet count to the full-history "PM 5m Volume" preset's
            # own trailing-2yr count (5,499 bets) while maximizing hit rate,
            # subject to both halves of the window individually clearing 50%.
            #
            # RESULT: NO COMBINATION IN THE GRID BEAT "PM 5m Volume"'s OWN HIT
            # RATE (57.03%) at any bet-count band, including the full "any n"
            # search. This preset is therefore set IDENTICAL to "PM 5m
            # Volume" -- i.e. the existing preset already sits at (or above)
            # what this grid can find, so there is nothing to gain from a
            # separate "2yr Train" variant here. A finer/wider grid might
            # still find something; this one didn't.
            "PM 5m Volume - 2yr Train": {
                "z_sma_length": 20, "z_std_length": 20, "z_upper": 2.0,
                "z_lower": -2.0, "require_kc_break": True, "kc_ema_length": 20,
                "kc_atr_length": 20, "kc_mult": 1.5, "use_bias_ma": True,
                "bias_ema_length": 200, "bias_slope_lookback": 5,
                "vol_atr_length": 50, "vol_min_atr_pct": 0.05,
                "vol_max_atr_pct": 1.5, "predict_direction": "Reversion",
                "use_trading_window": False, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": False,
                "trend_logic": "With Trend", "ma_type": "EMA", "ma_length": 200,
                "source": "close",
            },
            # -------------------------------------------------------------------------
            # 15-MINUTE preset, fitted on the latest six months of BTCUSDT
            # 15m under the protocol the Reversal, Oscillators and Gann 15m
            # presets use: LOADED 2026-03-13 -> 2026-09-13 (train 03-13 ->
            # 07-13 for selection, holdout 07-13 -> 09-13 scored once after
            # the pick was frozen); UNLOADED 2017-08-17 -> 2026-03-13, never
            # read by any sweep stage and scored once at the end as the real
            # out-of-sample check (25,481 bets there against 1,645 in the
            # window). The selection rule was fixed before tuning: train
            # bets >= 300, both train halves above 52%, every swept
            # parameter off its grid edge, then highest train hit — read
            # against the marginals, since at a few hundred bets a config
            # the SE is 1.5-2.5pp and the single best row is mostly noise.
            #
            # SWEEP. One stage of 960 configs raced Reversion/Momentum x
            # lookback {10..50} (SMA, StdDev and Keltner lengths tied) x z
            # threshold {1.5..3.0} x Keltner gate on/off x kc_mult
            # {1.0..2.0} x bias MA on/off x trend filter {off, With EMA200};
            # a second pass (~25) tried the trend filter at other lengths
            # and against the Keltner gate, the ATR band and length, a
            # separate StdDev length and a weekend gate.
            #
            # FOUND. Reversion is the family and Momentum its mirror (pooled
            # train 56.45% vs 43.85%). A z-score of 2 on SMA20 / StdDev20 IS
            # %B = 1 on a 20/2.0 Bollinger band by construction — the un-
            # gated z=2 config reproduces BB Squeeze's body-0 config bet for
            # bet (2,081 at 56.80% in the window, 34,98x at 56.86% unloaded)
            # — so what this strategy adds is the Keltner confirmation, and
            # that is what the preset carries: z >= 2 on a 20-bar mean,
            # confirmed by a close outside a 1.5-ATR Keltner channel.
            # Marginals: lookback 20 is best on train and 30-50 lose the
            # holdout (52.95 / 51.32); the z threshold is monotone on train
            # (1.5: 55.66 -> 3.0: 59.30) at a steep cost in bets; the
            # Keltner gate adds about a point (57.04 vs 56.09) for 40% fewer
            # bets; kc_mult is flat 1.0-2.0. The preset is PM 5m Volume's
            # geometry without its bias MA, which on 15m halves the bets for
            # a holdout that is no better. The With-Trend EMA200 filter is
            # the strong result here and it did NOT make the preset: on this
            # family it scores 60.1-60.9% on 2,700-6,200 unloaded bets with
            # train, holdout and unloaded all within a point of each other,
            # but on a quarter of the bets (see NOT SHIPPED).
            #
            # RESULTS — flat $1 per bet, next-candle direction
            #   preset             6m bets  6m hit   train   HOLDOUT   unloaded 8.5y             worst yr
            #   PM 15m Balanced     1,645  57.33%  57.63%   56.73%    56.54% (25,481, z +20.9)  54.81% (2022)
            #
            # Per year on the full record, none of it fitted except the last six months:
            #   2017  50.65% (924)       2018  56.45% (2,776)     2019  58.10% (2,611)     2020  58.45% (2,835)
            #   2021  55.91% (3,003)     2022  54.81% (2,972)     2023  57.85% (3,051)     2024  56.88% (3,402)
            #   2025  55.37% (3,276)     2026  57.91% (2,276)
            #
            # Train halves 59.89% / 55.50%. About 8.9 bets a day. The 18
            # months right before the window (2024-09 -> 2026-03) score
            # 56.01% on 4,924 bets; whole record 27,126 bets, 56.58%, z
            # +21.7. Read the hit rates against 49.9%: 0.13% of 15m candles
            # close exactly at their open. Checks after the pick was frozen:
            # the prefix (no look-ahead) test passes with 0 mismatches at
            # three cut points; bets run 49% long in the window and both
            # sides win (window long 55.20% / short 59.38%; unloaded 57.51%
            # / 55.60%); the mirror on the same settings scores 42.61% in
            # the window and 43.40% unloaded.
            #
            # WHERE IT FAILS. The worst month in the window is 2026-08 at
            # 55.4% on 271 bets; the worst full year 2022 at 54.81%. Train
            # halves are 59.9 / 55.5%. The Keltner gate does not change WHEN
            # it fails: the same weeks that hurt BB Squeeze hurt this. The
            # 0.50-odds EV the dashboard prints assumes a fill at even; a
            # real 15m book prices away from it. Hit rate is the finding.
            #
            # NOT SHIPPED. PM 5m Balanced carried over unchanged except for
            # the ATR band (z 2.0, KC 1.0, With Trend EMA200): 390 window
            # bets at 60.26% with train 60.38 / holdout 60.00, and 60.13% on
            # 6,245 unloaded bets (z +16.0), worst year 56.51% — the most
            # consistent number across every window in this sweep, at 2 bets
            # a day. It is under the 300-train-bet floor (260) and that is
            # the only reason it is not the preset; anyone who wants hit
            # rate over volume should use it. The rule's own top row (z 2.5,
            # no Keltner): 773 bets at 57.96%, 56.74% unloaded. Against
            # Trend: 1,383 at 56.91%, 55.94% unloaded. Weekend-only: 490 at
            # 58.16%.
            "PM 15m Balanced": {
                "z_sma_length": 20, "z_std_length": 20, "z_upper": 2.0,
                "z_lower": -2.0, "require_kc_break": True, "kc_ema_length": 20,
                "kc_atr_length": 20, "kc_mult": 1.5, "use_bias_ma": False,
                "bias_ema_length": 200, "bias_slope_lookback": 5,
                "vol_atr_length": 14, "vol_min_atr_pct": 0.05,
                "vol_max_atr_pct": 1.5, "predict_direction": "Reversion",
                "use_trading_window": False, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": False,
                "trend_logic": "With Trend", "ma_type": "EMA", "ma_length": 200,
                "source": "close",
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
