"""Regime Switch — trade momentum in a trend, mean-reversion in a range (#6).

Idea
----
Most strategies pick one behaviour and lose money whenever the market changes
character. This one measures *what kind of market it is right now* and flips its
logic accordingly:

  * **Trending regime**  -> a channel break is real   -> trade WITH it (momentum)
  * **Ranging regime**   -> a channel break is noise  -> FADE it (reversion)

The trigger is the same in both cases — a Donchian break of the previous
`channel_length` bars — only the direction flips. That is the "switch".

Regime detector
---------------
Three interchangeable measures, all normalised to a 0-100 **trend score** so one
threshold works for any of them:

  * **ADX**               — Wilder's trend strength, used as-is (classic cut: 25).
  * **Efficiency Ratio**  — Kaufman net-move / total-path, x100 (30 ~= ER 0.30).
  * **Volatility Ratio**  — fast ATR / slow ATR x50, so 50 = flat, >50 expanding.

`trend_score >= regime_threshold` means trending, otherwise ranging. Either
regime can be switched off to trade only one of them.

Parameter groups
----------------
Regime Detector    regime_method, regime_length, regime_threshold,
                   trade_trend_regime, trade_range_regime
Entry Channel      channel_length, breakout_buffer_atr, min_body_ratio
Decision           regime_mapping
Volatility Filter  vol_atr_length, vol_min_atr_pct, vol_max_atr_pct
Trading Window     shared (see common.py)
Trend Filter       shared (see common.py)

Entry logic (per bar)
---------------------
  1. Break the PREVIOUS `channel_length` bars' high/low by `breakout_buffer_atr`
     ATRs (the current bar is excluded, so the break is meaningful).
  2. Classify the regime from the trend score; skip if that regime is disabled.
  3. Direction = regime_mapping applied to the break direction.
  4. Body, volatility, trend and trading-window filters must all pass.

TP/SL sizing uses the volatility-filter ATR; in Polymarket up/down mode only the
direction matters.
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_METHODS = ["ADX", "Efficiency Ratio", "Volatility Ratio"]
# The first two switch behaviour by regime; the last two keep one behaviour and
# let the regime detector act purely as a filter for WHICH bars to trade.
_MAPPINGS = ["Trend=Momentum, Range=Reversion", "Trend=Reversion, Range=Momentum",
             "Always Reversion", "Always Momentum"]


class RegimeSwitch(Strategy):
    id = "regime_switch"
    name = "Regime Switch"
    description = ("Detects a trending vs ranging market and switches logic: ride "
                   "channel breaks in a trend, fade them in a range.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Regime Detector", [
                Param("regime_method", "Regime Method", "ADX", "enum", options=_METHODS,
                      help="How to measure trendiness. All are scaled to a 0-100 trend score."),
                Param("regime_length", "Regime Length", 14, "int", 2, 200, 1,
                      "Lookback for the regime measure."),
                Param("regime_threshold", "Regime Threshold", 25, "float", 0, 100, 1,
                      "Trend score at/above this = trending; below = ranging. "
                      "ADX: ~25. Efficiency Ratio: ~30 (=0.30). Vol Ratio: ~55."),
                Param("trade_trend_regime", "Trade Trend Regime", True, "bool",
                      help="Take signals while the market is classified as trending."),
                Param("trade_range_regime", "Trade Range Regime", True, "bool",
                      help="Take signals while the market is classified as ranging."),
            ]),
            ParamGroup("Entry Channel", [
                Param("channel_length", "Channel Length", 20, "int", 2, 500, 1,
                      "Donchian lookback; a break of the PREVIOUS N bars triggers."),
                Param("breakout_buffer_atr", "Breakout Buffer (xATR)", 0.0, "float", 0.0, 5.0, 0.05,
                      "Require the close to clear the channel by this many ATRs."),
                Param("min_body_ratio", "Min Body Ratio", 0.0, "float", 0.0, 1.0, 0.01,
                      "Minimum |close-open| / (high-low): reject indecisive candles."),
            ]),
            ParamGroup("Decision", [
                Param("regime_mapping", "Regime Mapping", _MAPPINGS[0], "enum", options=_MAPPINGS,
                      help="How each regime maps to a trade direction. The default rides "
                           "breaks in a trend and fades them in a range."),
            ]),
            ParamGroup("Volatility Filter", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also sizes TP/SL (xATR) and the breakout buffer."),
                Param("vol_min_atr_pct", "Vol Min ATR%", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("vol_max_atr_pct", "Vol Max ATR%", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (too violent)."),
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
        #   Volume            18,914   58.01%     50.40%    55.85%    55.94%   22.0
        #   Balanced           9,794   60.15%     50.27%    57.50%    58.88%   20.1
        #   Hi Hit             1,006   61.83%     51.79%    63.32%    64.23%    7.5
        #   Wknd Volume        6,870   58.40%     50.61%    56.71%    56.22%   13.9
        #   Wknd Balanced      5,004   58.19%     50.62%    56.86%    56.00%   11.6
        #   Wknd Hi Hit        1,434   58.51%     52.29%    57.73%    59.85%    6.4
        #
        # The weekend gate is supported but moderate. On the old Polymarket 5m
        # (Reversion) preset with parameters held fixed: weekend +2.30pp (z=+2.79) over
        # the full record and +3.56pp (z=+2.53) over 2024-26, easing to +2.36pp
        # (z=+1.33) over 2025-26. The pre-existing "Best Days" preset already captures
        # some of this, which is why its own residual day spread is flat.
        #
        # CAVEAT: selection used the FULL record with NO holdout, so these hit rates
        # carry selection bias and the 2024-26 / 2025-26 columns are a recency check,
        # not out-of-sample evidence. Days are UTC; a bar is stamped by its open time.
        return {
            # Tuned for Polymarket 5-min UP/DOWN (Mode = "Polymarket up/down",
            # interval 5m). Counter-intuitive but robust across Jul/Jun/May 2026
            # (56.9% / 58.7% / 61.3%, ~230 bets/month): on 5m BTC a channel break
            # during a HIGH-efficiency (trending) stretch snaps back rather than
            # continuing, so the regime detector is used to *select* those bars
            # and the break is faded. Exit params unused in that mode.
            "Polymarket 5m (Reversion)": {
                "regime_method": "Efficiency Ratio", "regime_length": 30,
                "regime_threshold": 35,
                "trade_trend_regime": True, "trade_range_regime": False,
                "channel_length": 10, "breakout_buffer_atr": 0.0,
                "min_body_ratio": 0.0, "regime_mapping": "Always Reversion",
            },
            # Same signal restricted to the strongest weekdays. Day-of-week study
            # over the FULL history (20,316 bets, 2017-2026) -- Wed/Sat/Sun is the
            # most STABLE set, and Monday is the weakest day:
            #                     all | 2024-26 | 2025-26 | 2026   (worst / spread)
            #   Wed+Sat+Sun    58.55% |  57.65% |  57.38% | 57.40%  (57.38 / 1.17)
            #   Sat only       59.44% |  58.94% |  56.19% | 54.88%  (54.88 / 4.56)
            #   all days       57.21% |  55.27% |  55.07% | 57.71%  (55.07 / 2.64)
            # Note Sat-alone tops all-history but decays badly, so the wider,
            # far steadier Wed+Sat+Sun set is used instead.
            "Polymarket 5m (Best Days)": {
                "regime_method": "Efficiency Ratio", "regime_length": 30,
                "regime_threshold": 35,
                "trade_trend_regime": True, "trade_range_regime": False,
                "channel_length": 10, "breakout_buffer_atr": 0.0,
                "min_body_ratio": 0.0, "regime_mapping": "Always Reversion",
                "use_trading_window": True,
                "trade_mon": False, "trade_tue": False, "trade_wed": True,
                "trade_thu": False, "trade_fri": False, "trade_sat": True,
                "trade_sun": True,
                "start_hour": 0, "start_minute": 0, "end_hour": 23, "end_minute": 59,
            },
            "Adaptive (both regimes)": {
                "regime_method": "ADX", "regime_length": 14, "regime_threshold": 25,
                "trade_trend_regime": True, "trade_range_regime": True,
                "channel_length": 20, "regime_mapping": _MAPPINGS[0],
            },
            "Range Only (fade)": {
                "regime_method": "ADX", "regime_threshold": 25,
                "trade_trend_regime": False, "trade_range_regime": True,
                "channel_length": 20, "regime_mapping": _MAPPINGS[0],
            },
            "Trend Only (momentum)": {
                "regime_method": "ADX", "regime_threshold": 25,
                "trade_trend_regime": True, "trade_range_regime": False,
                "channel_length": 20, "regime_mapping": _MAPPINGS[0],
                "breakout_buffer_atr": 0.25, "min_body_ratio": 0.3,
            },
            "Efficiency Ratio": {
                "regime_method": "Efficiency Ratio", "regime_length": 20,
                "regime_threshold": 30, "channel_length": 20,
                "trade_trend_regime": True, "trade_range_regime": True,
            },
            # 18,914 bets, 58.01% hit; 2024-26 55.85%, worst year 50.40%.
            "PM 5m Volume": {
                "regime_method": 'ADX', "regime_length": 14,
                "regime_threshold": 20, "trade_trend_regime": True,
                "trade_range_regime": True, "channel_length": 10,
                "breakout_buffer_atr": 0.3, "min_body_ratio": 0.2,
                "regime_mapping": 'Always Reversion', "vol_atr_length": 50,
                "vol_min_atr_pct": 0.0, "vol_max_atr_pct": 20.0,
                "use_trading_window": False, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close',
            },
            # 9,794 bets, 60.15% hit; 2024-26 57.50%, worst year 50.27%.
            "PM 5m Balanced": {
                "regime_method": 'ADX', "regime_length": 14,
                "regime_threshold": 20, "trade_trend_regime": True,
                "trade_range_regime": True, "channel_length": 10,
                "breakout_buffer_atr": 0.3, "min_body_ratio": 0.2,
                "regime_mapping": 'Always Reversion', "vol_atr_length": 50,
                "vol_min_atr_pct": 0.2, "vol_max_atr_pct": 3.0,
                "use_trading_window": False, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close',
            },
            # 1,006 bets, 61.83% hit; 2024-26 63.32%, worst year 51.79%.
            "PM 5m Hi Hit": {
                "regime_method": 'ADX', "regime_length": 14,
                "regime_threshold": 20, "trade_trend_regime": True,
                "trade_range_regime": True, "channel_length": 50,
                "breakout_buffer_atr": 0.3, "min_body_ratio": 0.2,
                "regime_mapping": 'Always Reversion', "vol_atr_length": 50,
                "vol_min_atr_pct": 0.2, "vol_max_atr_pct": 3.0,
                "use_trading_window": False, "start_hour": 0, "start_minute": 0,
                "end_hour": 23, "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
                "source": 'close',
            },
            # 6,870 bets, 58.40% hit; 2024-26 56.71%, worst year 50.61%.
            "PM 5m Wknd Volume": {
                "regime_method": 'Efficiency Ratio', "regime_length": 20,
                "regime_threshold": 25, "trade_trend_regime": True,
                "trade_range_regime": True, "channel_length": 50,
                "breakout_buffer_atr": 0.3, "min_body_ratio": 0.0,
                "regime_mapping": 'Trend=Reversion, Range=Momentum',
                "vol_atr_length": 50, "vol_min_atr_pct": 0.0,
                "vol_max_atr_pct": 20.0, "use_trading_window": True,
                "start_hour": 0, "start_minute": 0, "end_hour": 23,
                "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200, "source": 'close', "trade_mon": False,
                "trade_tue": False, "trade_wed": False, "trade_thu": False,
                "trade_fri": False, "trade_sat": True, "trade_sun": True,
            },
            # 5,004 bets, 58.19% hit; 2024-26 56.86%, worst year 50.62%.
            "PM 5m Wknd Balanced": {
                "regime_method": 'Efficiency Ratio', "regime_length": 20,
                "regime_threshold": 35, "trade_trend_regime": True,
                "trade_range_regime": True, "channel_length": 50,
                "breakout_buffer_atr": 0.3, "min_body_ratio": 0.2,
                "regime_mapping": 'Trend=Reversion, Range=Momentum',
                "vol_atr_length": 50, "vol_min_atr_pct": 0.05,
                "vol_max_atr_pct": 1.5, "use_trading_window": True,
                "start_hour": 0, "start_minute": 0, "end_hour": 23,
                "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200, "source": 'close', "trade_mon": False,
                "trade_tue": False, "trade_wed": False, "trade_thu": False,
                "trade_fri": False, "trade_sat": True, "trade_sun": True,
            },
            # 1,434 bets, 58.51% hit; 2024-26 57.73%, worst year 52.29%.
            "PM 5m Wknd Hi Hit": {
                "regime_method": 'ADX', "regime_length": 20,
                "regime_threshold": 25, "trade_trend_regime": True,
                "trade_range_regime": True, "channel_length": 50,
                "breakout_buffer_atr": 0.3, "min_body_ratio": 0.2,
                "regime_mapping": 'Trend=Reversion, Range=Momentum',
                "vol_atr_length": 14, "vol_min_atr_pct": 0.2,
                "vol_max_atr_pct": 3.0, "use_trading_window": True,
                "start_hour": 0, "start_minute": 0, "end_hour": 23,
                "end_minute": 59, "use_trend_filter": True,
                "trend_logic": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200, "source": 'close', "trade_mon": False,
                "trade_tue": False, "trade_wed": False, "trade_thu": False,
                "trade_fri": False, "trade_sat": True, "trade_sun": True,
            },
        }

    # ---- regime score -------------------------------------------------------

    def _trend_score(self, candles: List[dict], method: str, length: int) -> List:
        """0-100 trend score for the chosen method (None during warm-up)."""
        n = len(candles)
        if method == "Efficiency Ratio":
            er = ind.efficiency_ratio(candles, length)
            return [None if v is None else v * 100.0 for v in er]
        if method == "Volatility Ratio":
            fast = ind.atr(candles, length)
            slow = ind.atr(candles, max(length * 4, length + 1))
            out = [None] * n
            for i in range(n):
                f, s = fast[i], slow[i]
                if f is None or s is None or s <= 0:
                    continue
                out[i] = max(0.0, min(100.0, (f / s) * 50.0))
            return out
        return ind.adx(candles, length)  # ADX (default), already 0-100

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        score = self._trend_score(candles, p["regime_method"], p["regime_length"])
        ch_len = p["channel_length"]
        ch_lo, ch_hi = ind.rolling_high_low(candles, ch_len)
        atr_vol = ind.atr(candles, p["vol_atr_length"])

        use_trend = p["use_trend_filter"]
        trend_ma = common.moving_average(
            common.source_values(candles, p["source"]), p["ma_type"], p["ma_length"]) \
            if use_trend else [None] * n

        thr = p["regime_threshold"]
        do_trend, do_range = p["trade_trend_regime"], p["trade_range_regime"]
        buf_mult = p["breakout_buffer_atr"]
        min_body = p["min_body_ratio"]
        vmin, vmax = p["vol_min_atr_pct"], p["vol_max_atr_pct"]
        mapping = p["regime_mapping"]
        if mapping == _MAPPINGS[1]:
            def mapping_fn(trending): return not trending
        elif mapping == _MAPPINGS[2]:
            def mapping_fn(_trending): return False   # always reversion
        elif mapping == _MAPPINGS[3]:
            def mapping_fn(_trending): return True    # always momentum
        else:
            def mapping_fn(trending): return trending  # default: switch by regime
        use_window = p["use_trading_window"]
        allowed_days = common.allowed_days(p)
        start_min, end_min = common.window_minutes(p)
        with_trend = p["trend_logic"] == "With Trend"

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            if i == 0:
                continue
            # channel of the PREVIOUS bars (exclude the current bar)
            hi, lo = ch_hi[i - 1], ch_lo[i - 1]
            s, a = score[i], atr_vol[i]
            if hi is None or lo is None or s is None or a is None or a <= 0:
                continue

            cl, o = c["close"], c["open"]
            rng = c["high"] - c["low"]
            if rng <= 0:
                continue
            if abs(cl - o) / rng < min_body:
                continue

            atr_pct = a / cl * 100.0
            if atr_pct < vmin or atr_pct > vmax:
                continue

            buf = buf_mult * a
            if cl > hi + buf:
                brk = "up"
            elif cl < lo - buf:
                brk = "down"
            else:
                continue

            trending = s >= thr
            if trending and not do_trend:
                continue
            if not trending and not do_range:
                continue

            # how this regime maps to a direction
            momentum = mapping_fn(trending)
            if brk == "up":
                side = "long" if momentum else "short"
            else:
                side = "short" if momentum else "long"

            if use_trend and not common.trend_ok(side, cl, trend_ma[i], with_trend):
                continue
            if use_window and not common.in_window(c["time"], allowed_days, start_min, end_min):
                continue

            regime = "trend" if trending else "range"
            mode = "momentum" if momentum else "reversion"
            reason = (f"{brk}-break in {regime} regime (score {s:.1f}) -> "
                      f"{mode} {side.upper()} (ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"regime": regime, "score": round(s, 2), "break": brk,
                      "mode": mode, "atr_pct": round(atr_pct, 3)},
            ))
        return signals
