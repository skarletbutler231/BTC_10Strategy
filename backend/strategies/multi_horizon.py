"""Strategy #10 from the video: Multi Horizon  ("agreement across timeframes").

Idea
----
One lookback window only ever tells you one story. A close can look wildly
stretched against the last hour and perfectly ordinary against the last twelve —
and a signal built on either window alone cannot tell those two situations
apart. This strategy measures the *same* quantity at three horizons at once and
only acts when enough of them agree.

The quantity is a **z-score**: how many standard deviations the close sits from
its own mean over that horizon.

    z(h) = (close - SMA(close, h)) / stdev(close, h)

Because it is expressed in that horizon's own sigmas, z is directly comparable
across horizons and across the 2017-2026 price range — a 2-sigma stretch means
the same thing at $4k and at $120k. Defaults of 12 / 48 / 144 bars are 1h / 4h /
12h on the 5m interval.

Agreement is the whole point. `min_agree` sets how many of the three horizons
must be stretched past `z_threshold`, and they must all lean the SAME way — if
any horizon is stretched the other way the bar is skipped, because that is a
conflict, not a signal. `require_fast` additionally insists the fast horizon be
one of the agreeing ones, which restricts entries to moves that are stretched
*right now* rather than merely stretched an hour ago.

Parameter groups
----------------
Horizons     h_fast, h_mid, h_slow
Signal       z_threshold, min_agree, require_fast
Volatility   vol_atr_length, atr_pct_min, atr_pct_max
Trend        use_trend_filter, trend_logic, ma_type, ma_length, source
Decision     predict_direction  (Reversion | Continuation)

Entry logic
-----------
  1. Count horizons with z >= +z_threshold (n_up) and z <= -z_threshold (n_dn).
  2. Require n_up >= min_agree and n_dn == 0  -> stretched UP (mirror for DOWN).
  3. If require_fast, the fast horizon must be among the stretched ones.
  4. Volatility regime: ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max].
  5. Optional trend-filter agreement.

**Reversion** fades the stretch (SHORT an up-stretch); **Continuation** rides it.
The Vol ATR sizes TP/SL; in Polymarket up/down mode the exit params are unused.
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup, Signal, Strategy


class MultiHorizon(Strategy):
    id = "multi_horizon"
    name = "Multi Horizon"
    description = ("Measures how stretched the close is at three horizons at once "
                   "(z-score vs each horizon's own mean) and acts only when enough "
                   "of them agree, with volatility and trend filters.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Horizons", [
                Param("h_fast", "Fast Horizon (bars)", 12, "int", 2, 500, 1,
                      "Shortest lookback for the z-score (12 bars = 1h on 5m)."),
                Param("h_mid", "Mid Horizon (bars)", 48, "int", 2, 1000, 1,
                      "Middle lookback (48 bars = 4h on 5m)."),
                Param("h_slow", "Slow Horizon (bars)", 144, "int", 2, 2000, 1,
                      "Longest lookback (144 bars = 12h on 5m)."),
            ]),
            ParamGroup("Signal", [
                Param("z_threshold", "Z Threshold", 2.0, "float", 0.5, 8.0, 0.1,
                      "A horizon counts as stretched when |z| reaches this many sigma."),
                Param("min_agree", "Min Horizons Agreeing", 2, "int", 1, 3, 1,
                      "How many of the three horizons must be stretched the same way. "
                      "Any horizon stretched the opposite way vetoes the bar."),
                Param("require_fast", "Require Fast Horizon", False, "bool",
                      help="Insist the fast horizon be one of the agreeing ones, so "
                           "the move is stretched now rather than an hour ago."),
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
                      help="Reversion fades the stretch; Continuation rides it."),
            ]),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        closes = [c["close"] for c in candles]
        horizons = [p["h_fast"], p["h_mid"], p["h_slow"]]
        zs = []
        for h in horizons:
            basis = ind.sma(closes, h)
            sd = ind.rolling_std(closes, h)
            z: List = [None] * n
            for i in range(n):
                if basis[i] is None or sd[i] is None or sd[i] <= 0:
                    continue
                z[i] = (closes[i] - basis[i]) / sd[i]
            zs.append(z)

        atr_vol = ind.atr(candles, p["vol_atr_length"])
        use_trend = p["use_trend_filter"]
        trend_ma = ind.ma(ind.price_source(candles, p["source"]),
                          p["ma_type"], p["ma_length"]) if use_trend else [None] * n

        thr = p["z_threshold"]
        min_agree = p["min_agree"]
        require_fast = p["require_fast"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        with_trend = p["trend_logic"] == "With Trend"
        reversion = p["predict_direction"] == "Reversion"

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            a = atr_vol[i]
            if a is None or a <= 0:
                continue
            zv = [z[i] for z in zs]
            if any(v is None for v in zv):
                continue

            n_up = sum(1 for v in zv if v >= thr)
            n_dn = sum(1 for v in zv if v <= -thr)
            if n_up >= min_agree and n_dn == 0:
                extreme = "up"
            elif n_dn >= min_agree and n_up == 0:
                extreme = "down"
            else:
                continue

            if require_fast:
                zf = zv[0]
                if extreme == "up" and zf < thr:
                    continue
                if extreme == "down" and zf > -thr:
                    continue

            cl = c["close"]
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            side = ("short" if extreme == "up" else "long") if reversion \
                else ("long" if extreme == "up" else "short")

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
            agreeing = n_up if extreme == "up" else n_dn
            reason = (f"{agreeing}/3 horizons {extreme.upper()}-stretched "
                      f"(z {zv[0]:+.1f}/{zv[1]:+.1f}/{zv[2]:+.1f}) "
                      f"-> {mode} {side.upper()} (ATR% {atr_pct:.2f})")

            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"z_fast": round(zv[0], 2), "z_mid": round(zv[1], 2),
                      "z_slow": round(zv[2], 2), "agree": agreeing,
                      "atr_pct": round(atr_pct, 3), "extreme": extreme, "mode": mode},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m). Exit / Backtest params are unused in that mode.
#
# Sweep: BTCUSDT 1m resampled to 5m over the entire DB — 936,841 bars,
# 2017-08-17 .. 2026-07-19, ~94k parameter combinations. Same admission rules as
# the other two Polymarket-tuned strategies: win EVERY calendar year, clear 53%
# over 2024-2026 on its own, be binomially significant on both spans.
#
# Three findings worth more than the numbers
# -------------------------------------------
#   * **Reversion only, again.** All 4,304 combinations that passed were
#     Reversion; zero Continuation. That now holds across three independent
#     strategies on this data — on BTC 5m, stretch reverts.
#   * **The veto matters more than the agreement.** The best configurations use
#     `min_agree = 1` together with `require_fast` — i.e. they do NOT demand that
#     several horizons line up. What earns the edge is the *other* half of the
#     rule: no horizon may be stretched the opposite way. Multi-horizon pays off
#     as a conflict filter, not as a confirmation stack.
#   * **"With Trend" here, unlike Volume Exhaustion.** Combined with Reversion it
#     resolves to buying a down-stretch while price is above the MA — buy the dip
#     in an uptrend. (Volume Exhaustion preferred Against Trend; these are
#     different setups and there is no contradiction.)
#
# Measured results (whole DB, flat $1 per bet)
# --------------------------------------------
#   preset      bets     hit    2024-26 bets  2024-26 hit  worst yr    z
#   Volume     55,277  55.12%      16,865       53.12%      50.30%    24.1
#   Balanced   40,342  57.48%      10,805       56.25%      50.36%    30.0
#   Selective  21,706  57.45%       3,105       57.65%      50.66%    22.0
#   Hi Hit      8,315  58.99%       2,027       58.26%      53.95%    16.4
#   Max Hit     3,798  60.80%         582       61.00%      55.24%    13.3
#
# This is the strongest of the four strategies here: Balanced holds 56.25% over
# 2024-2026 across 10,805 bets (z=30.0 overall), and unlike the other strategies'
# high-hit presets, Hi Hit and Max Hit are backed by real samples — every single
# year lands between 53.9% and 63.2%, so neither depends on one lucky regime.
#
# Caveats
# -------
# 1. **The edge still decays**, though less sharply than elsewhere: Balanced runs
#    ~57-60% in 2018-2023 against ~56% in 2024-2026. Read the 2024-26 column.
# 2. **2017 is the weak year** (~50%) for the first three; it is a partial year
#    (Aug-Dec) and the "every year wins" rule was binding there. Hi Hit and Max
#    Hit clear it comfortably at 53.9% / 55.2%.
# 3. Selective's ATR% floor makes it volatility-dependent — only 3,105 of its
#    21,706 bets fall in 2024-26, because recent tape is calmer.
#
# A bet pays only when hit rate > your odds: Balanced's 56.25% needs entry below
# ~0.5625.
PRESETS: dict = {
    # Loosest gate (1.5 sigma on the slow set) — the most bets that still won
    # every year, at a real cost in hit rate. Balanced beats it on quality.
    "PM 5m Volume": {
        "h_fast": 24, "h_mid": 96, "h_slow": 288,
        "z_threshold": 1.5, "min_agree": 1, "require_fast": False,
        "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": True, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Reversion",
    },
    # The standout: highest z in the repo (30.0) and 56.25% over 2024-26.
    # Fast horizon stretched 2.5 sigma, with neither slower horizon opposing it.
    "PM 5m Balanced": {
        "h_fast": 24, "h_mid": 72, "h_slow": 216,
        "z_threshold": 2.5, "min_agree": 1, "require_fast": True,
        "vol_atr_length": 50, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Reversion",
    },
    # Balanced plus a high-volatility floor. Fires far less often in calm tape.
    "PM 5m Selective": {
        "h_fast": 24, "h_mid": 72, "h_slow": 216,
        "z_threshold": 2.5, "min_agree": 1, "require_fast": True,
        "vol_atr_length": 50, "atr_pct_min": 0.2, "atr_pct_max": 3.0,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Reversion",
    },
    # Short horizons (1h/2h/4h) + buy-the-dip trend filter. Worst year 53.95%.
    "PM 5m Hi Hit": {
        "h_fast": 12, "h_mid": 24, "h_slow": 48,
        "z_threshold": 2.5, "min_agree": 1, "require_fast": False,
        "vol_atr_length": 14, "atr_pct_min": 0.1, "atr_pct_max": 1.0,
        "use_trend_filter": True, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Reversion",
    },
    # Best high-hit preset in this repo: 60.8% over 3,798 bets, z=13.3, and every
    # year from 2017 to 2026 lands between 55.2% and 63.2%. ~425 bets/year.
    "PM 5m Max Hit": {
        "h_fast": 6, "h_mid": 24, "h_slow": 72,
        "z_threshold": 2.5, "min_agree": 1, "require_fast": False,
        "vol_atr_length": 50, "atr_pct_min": 0.2, "atr_pct_max": 3.0,
        "use_trend_filter": True, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Reversion",
    },
}
