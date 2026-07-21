"""Strategy #7 from the video: Volume Exhaustion  ("fade the climax bar").

Idea
----
A directional bar printed on *abnormally heavy* volume is often the end of a
move rather than the start of one: the crowd that wanted in has just piled in,
and there is no one left to keep pushing. That is a volume climax, and the
strategy fades it.

"Abnormal" has to be measured relatively — BTC's raw volume rises by orders of
magnitude across the 2017-2026 history and swings intraday — so volume is judged
two independent ways, both scale-free:

  * **Relative volume** — this bar's volume divided by its own rolling mean
    (`vol_spike_mult`). Simple and responsive.
  * **Volume percentile** — where the bar's volume ranks inside a longer window
    (`vol_rank_min`). Slower but robust to a single outlier dragging the mean.

Parameter groups
----------------
Volume       vol_ma_length, vol_spike_mult, vol_rank_lookback, vol_rank_min
Candle       min_body_ratio, wick_min
Volatility   vol_atr_length, atr_pct_min, atr_pct_max
Trend        use_trend_filter, trend_logic, ma_type, ma_length, source
Decision     predict_direction  (Reversion | Continuation)

Entry logic for an UP-climax (mirror for a down-climax)
-------------------------------------------------------
  1. Volume spike:   volume / SMA(volume, vol_ma_length) >= vol_spike_mult
  2. Volume rank:    percentile of volume over vol_rank_lookback >= vol_rank_min
                     (set vol_rank_min to 0 to disable this gate)
  3. Decisive bar:   |close - open| / range >= min_body_ratio, and close > open
  4. Rejection:      upper wick / range >= wick_min  (0 disables)
  5. Volatility:     ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max]
  6. Trend filter:   optional agreement with a moving average

All of 1-6 hold -> **Reversion** fades it (SHORT); **Continuation** rides it
(LONG). The Vol ATR also sizes TP/SL; in Polymarket up/down mode the exit params
are unused and each signal is simply a bet on the next candle's direction.
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup, Signal, Strategy


class VolumeExhaustion(Strategy):
    id = "volume_exhaustion"
    name = "Volume Exhaustion"
    description = ("Fade the volume climax: a decisive bar printed on abnormally "
                   "heavy volume (by both relative volume and percentile rank), "
                   "filtered by volatility regime and an optional trend filter.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Volume", [
                Param("vol_ma_length", "Volume MA Length", 20, "int", 2, 500, 1,
                      "Lookback for the average volume a spike is measured against."),
                Param("vol_spike_mult", "Volume Spike (xAvg)", 2.5, "float", 1.0, 20.0, 0.1,
                      "Bar volume must be at least this many times its rolling average."),
                Param("vol_rank_lookback", "Volume Rank Lookback", 200, "int", 10, 2000, 10,
                      "Window over which the bar's volume is ranked into a percentile."),
                Param("vol_rank_min", "Volume Rank Min (pct)", 90, "float", 0, 100, 1,
                      "Volume percentile must be at least this. 0 disables the gate."),
            ]),
            ParamGroup("Candle", [
                Param("min_body_ratio", "Min Body Ratio", 0.4, "float", 0.0, 1.0, 0.01,
                      "Minimum |close-open| / range: the climax bar must be decisive."),
                Param("wick_min", "Wick Min", 0.0, "float", 0.0, 1.0, 0.01,
                      "Minimum rejection wick as a fraction of range. 0 disables."),
            ]),
            ParamGroup("Volatility Filter", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also sizes TP/SL (xATR) for this strategy."),
                Param("atr_pct_min", "ATR% Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR% Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent regime)."),
            ]),
            ParamGroup("Trend Filter", [
                Param("use_trend_filter", "Use Trend Filter?", False, "bool",
                      help="Require price to agree with a moving-average trend."),
                Param("trend_logic", "Trend Logic", "With Trend", "enum",
                      options=["With Trend", "Against Trend"],
                      help="With Trend: long above / short below the MA. Against Trend: the opposite."),
                Param("ma_type", "MA Type", "EMA", "enum", options=ind.MA_TYPES,
                      help="Moving-average type for the trend filter."),
                Param("ma_length", "MA Length", 200, "int", 2, 1000, 1,
                      "Lookback for the trend MA."),
                Param("source", "Source", "close", "enum", options=ind.SOURCES,
                      help="Price source for the trend MA."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Reversion", "enum",
                      options=["Reversion", "Continuation"],
                      help="Reversion fades the climax bar; Continuation rides it."),
            ]),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        vols = [c["volume"] for c in candles]
        vol_avg = ind.sma(vols, p["vol_ma_length"])
        rank_min = p["vol_rank_min"]
        vol_rank = ind.rolling_percentile_rank(vols, p["vol_rank_lookback"]) \
            if rank_min > 0 else [None] * n
        atr_vol = ind.atr(candles, p["vol_atr_length"])
        use_trend = p["use_trend_filter"]
        trend_ma = ind.ma(ind.price_source(candles, p["source"]),
                          p["ma_type"], p["ma_length"]) if use_trend else [None] * n

        spike_mult = p["vol_spike_mult"]
        min_body = p["min_body_ratio"]
        wick_min = p["wick_min"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        with_trend = p["trend_logic"] == "With Trend"
        reversion = p["predict_direction"] == "Reversion"

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            va, a = vol_avg[i], atr_vol[i]
            if va is None or a is None or a <= 0 or va <= 0:
                continue

            o, h, l, cl, v = c["open"], c["high"], c["low"], c["close"], c["volume"]
            rng = h - l
            if rng <= 0:
                continue

            # Volume spike (relative to its own rolling mean)
            rel_vol = v / va
            if rel_vol < spike_mult:
                continue

            # Volume percentile rank
            if rank_min > 0:
                r = vol_rank[i]
                if r is None or r < rank_min:
                    continue

            # Decisive, directional bar
            if abs(cl - o) / rng < min_body:
                continue
            if cl > o:
                climax = "up"
            elif cl < o:
                climax = "down"
            else:
                continue

            # Rejection wick on the climax side
            if wick_min > 0:
                wick = (h - max(o, cl)) if climax == "up" else (min(o, cl) - l)
                if wick / rng < wick_min:
                    continue

            # Volatility regime
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            side = ("short" if climax == "up" else "long") if reversion \
                else ("long" if climax == "up" else "short")

            # Trend filter
            if use_trend:
                tm = trend_ma[i]
                if tm is None:
                    continue
                above = cl > tm
                agree = (side == "long" and above) or (side == "short" and not above)
                if with_trend and not agree:
                    continue
                if not with_trend and agree:
                    continue

            mode = "reversion" if reversion else "continuation"
            reason = (f"{climax.upper()}-climax {rel_vol:.1f}x avg volume "
                      f"-> {mode} {side.upper()} (ATR% {atr_pct:.2f})")

            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"rel_vol": round(rel_vol, 2),
                      "vol_rank": round(vol_rank[i], 1) if vol_rank[i] is not None else None,
                      "atr_pct": round(atr_pct, 3), "climax": climax, "mode": mode},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m). Exit / Backtest params are unused in that mode.
#
# Sweep: BTCUSDT 1m resampled to 5m over the entire DB — 936,841 bars,
# 2017-08-17 .. 2026-07-19, ~242k parameter combinations scored on next-candle
# direction. Same admission rules as the CCI Williams presets: a combination had
# to win in EVERY calendar year it was active in, clear 53% over 2024-2026 on
# its own, and be binomially significant over both spans.
#
# Two structural findings came out of the sweep, and they shape every preset:
#
#   * **Reversion only.** Of 9,221 combinations that passed, 9,221 were
#     Reversion and 0 were Continuation. Fading the volume climax is the edge;
#     riding it is the same edge with the sign flipped, and it loses.
#   * **"Against Trend" helps.** Most of the frontier sets the trend filter to
#     Against Trend — i.e. only fade an up-climax while price is ABOVE the MA,
#     and a down-climax while BELOW it. That stacks a second, slower mean-
#     reversion condition on top of the volume one, and it is worth roughly a
#     full point of hit rate at equal volume.
#
# Measured results (whole DB, flat $1 per bet)
# --------------------------------------------
#   preset      bets     hit    2024-26 bets  2024-26 hit  worst yr    z
#   Volume     64,894  54.52%      19,494       53.07%      50.11%    23.0
#   Balanced   38,149  55.82%      11,244       54.70%      50.52%    22.7
#   Selective  24,513  56.28%       7,825       55.19%      51.36%    19.7
#   Hi Hit      9,415  56.40%       1,772       57.51%      52.22%    12.4
#   Max Hit     1,062  57.16%         230       66.09%      51.69%     4.7
#
# Caveats, in order of how much they should worry you
# ----------------------------------------------------
# 1. **Max Hit is the thinnest result here and may be noise.** Its z of 4.7 is
#    an order of magnitude weaker than the others', it fires ~120 times a year,
#    and its edge is concentrated in 2023-2026 (its 2020-2022 years are barely
#    above 50%). The recent 66% stands up on its own terms (95% CI ~60-72%), but
#    treat it as a lead to validate, not a settled edge.
# 2. **The edge decays**, as it does for CCI Williams: ~55-56% in 2018-2023
#    against ~53-55% in 2024-2026 for the higher-volume presets. Read the
#    2024-26 column.
# 3. **2017 is the weak year** (~50%) for the first three, and the "every year
#    wins" rule was binding almost exactly there.
#
# A bet is profitable only when hit rate > your odds, so Balanced's 54.7% recent
# hit needs entry below ~0.547.
PRESETS: dict = {
    # Loosest volume gate that still won every year. ~20 bets/day.
    "PM 5m Volume": {
        "vol_ma_length": 50, "vol_spike_mult": 1.5,
        "vol_rank_lookback": 500, "vol_rank_min": 90,
        "min_body_ratio": 0.2, "wick_min": 0.0,
        "vol_atr_length": 14, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Reversion",
    },
    # Adds the Against-Trend filter: ~40% fewer bets, ~1.3pt better hit rate.
    "PM 5m Balanced": {
        "vol_ma_length": 20, "vol_spike_mult": 1.5,
        "vol_rank_lookback": 500, "vol_rank_min": 90,
        "min_body_ratio": 0.2, "wick_min": 0.0,
        "vol_atr_length": 50, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "SMA", "ma_length": 100, "source": "close",
        "predict_direction": "Reversion",
    },
    # Same shape, a genuine 2.5x volume spike required.
    "PM 5m Selective": {
        "vol_ma_length": 50, "vol_spike_mult": 2.5,
        "vol_rank_lookback": 500, "vol_rank_min": 90,
        "min_body_ratio": 0.2, "wick_min": 0.0,
        "vol_atr_length": 50, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "SMA", "ma_length": 100, "source": "close",
        "predict_direction": "Reversion",
    },
    # Top-5% volume, a big decisive body, and a high-volatility floor.
    # Best risk-adjusted pick of the five: worst year 52.2% with z=12.4.
    "PM 5m Hi Hit": {
        "vol_ma_length": 20, "vol_spike_mult": 1.5,
        "vol_rank_lookback": 200, "vol_rank_min": 95,
        "min_body_ratio": 0.6, "wick_min": 0.0,
        "vol_atr_length": 50, "atr_pct_min": 0.2, "atr_pct_max": 3.0,
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "EMA", "ma_length": 50, "source": "close",
        "predict_direction": "Reversion",
    },
    # 4x volume spike AND a 35% rejection wick. Highest hit rate on record here,
    # but see caveat 1 above -- ~120 bets/year and z=4.7. Unproven, not settled.
    "PM 5m Max Hit": {
        "vol_ma_length": 10, "vol_spike_mult": 4.0,
        "vol_rank_lookback": 500, "vol_rank_min": 90,
        "min_body_ratio": 0.0, "wick_min": 0.35,
        "vol_atr_length": 14, "atr_pct_min": 0.15, "atr_pct_max": 2.0,
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "SMA", "ma_length": 100, "source": "close",
        "predict_direction": "Reversion",
    },
}
