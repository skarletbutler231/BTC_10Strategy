"""Strategy #9 from the video: CCI Williams  ("two-oscillator exhaustion").

Idea
----
Two overbought/oversold oscillators that measure *different* things must agree
before we act:

  * **CCI** measures how far the typical price has stretched from its own mean,
    in units of that window's average deviation. |CCI| >= threshold says the
    move is statistically extended.
  * **Williams %R** measures where the close sits inside the window's high-low
    *range*. %R near 0 says the close is pinned at the top of the range; near
    -100, pinned at the bottom.

Either one alone fires constantly in a trend. Together they describe a specific
state — stretched from the mean *and* pinned at the range extreme — which is the
classic exhaustion setup. An optional candle filter then demands the bar show
rejection (a wick, and a close that has already backed off the extreme), and a
volatility band skips dead tape and violent regimes.

Parameter groups (match the config screen)
-------------------------------------------
Core        cci_length, cci_threshold, wr_length, wr_overbought, wr_oversold
Candle      use_wick_confirm, wick_min, close_recover_min
Volatility  vol_atr_length, atr_pct_min, atr_pct_max
Decision    predict_direction  (Reversion | Continuation)

Entry logic for an UP-exhaustion (mirror for a down-exhaustion)
---------------------------------------------------------------
  1. Stretched:   CCI(cci_length) >= +cci_threshold
  2. Pinned high: Williams %R(wr_length) >= wr_overbought  (%R is -100..0, so
     "overbought" is the *less negative* end, e.g. -20)
  3. Rejection (only if use_wick_confirm):
       upper wick / range >= wick_min           -- price traded higher and got sold
       (high - close) / range >= close_recover_min  -- the close already backed off
  4. Volatility regime: ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max]

All of 1-4 hold -> **Reversion** fades it (SHORT); **Continuation** rides it
(LONG). The down-exhaustion mirror uses CCI <= -threshold, %R <= wr_oversold,
the lower wick, and (close - low) / range.

The Vol ATR also sizes TP/SL for the TP/SL engine. In Polymarket up/down mode the
exit params are unused — each signal is just a directional bet on the next candle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup, Signal, Strategy

_DAYS = ["trade_mon", "trade_tue", "trade_wed", "trade_thu",
         "trade_fri", "trade_sat", "trade_sun"]  # index == datetime.weekday()

# Saturday + Sunday only; used by the weekend-gated Polymarket presets.
_WEEKEND = {k: (k in ("trade_sat", "trade_sun")) for k in _DAYS}


class CCIWilliams(Strategy):
    id = "cci_williams"
    name = "CCI Williams"
    description = ("Requires CCI (stretched from the mean) and Williams %R (pinned "
                   "at the range extreme) to agree on exhaustion, with optional "
                   "wick-rejection and volatility-regime filters.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Core", [
                Param("cci_length", "CCI Length", 20, "int", 2, 400, 1,
                      "Lookback for CCI (typical price vs its mean absolute deviation)."),
                Param("cci_threshold", "CCI Threshold", 100, "float", 10, 500, 5,
                      "|CCI| at/above this counts as stretched. 100 is the classic band."),
                Param("wr_length", "Williams %R Length", 14, "int", 2, 400, 1,
                      "Lookback for the high-low range Williams %R is measured against."),
                Param("wr_overbought", "WR Overbought", -20, "float", -100, 0, 1,
                      "%R at/above this is overbought (%R runs -100..0, so -20 is high)."),
                Param("wr_oversold", "WR Oversold", -80, "float", -100, 0, 1,
                      "%R at/below this is oversold."),
            ]),
            ParamGroup("Candle", [
                Param("use_wick_confirm", "Use Wick Confirmation", True, "bool",
                      help="Require the bar to show rejection before acting "
                           "(applies both Wick Min and Close Recover Min)."),
                Param("wick_min", "Wick Min", 0.25, "float", 0.0, 1.0, 0.01,
                      "Minimum rejection wick (beyond the body) as a fraction of bar range."),
                Param("close_recover_min", "Close Recover Min", 0.15, "float", 0.0, 1.0, 0.01,
                      "How far the close must have backed off the bar extreme, "
                      "as a fraction of bar range."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also sizes TP/SL (xATR) for this strategy."),
                Param("atr_pct_min", "ATR% Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR% Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent regime)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Reversion", "enum",
                      options=["Reversion", "Continuation"],
                      help="Reversion fades the exhaustion; Continuation rides it."),
            ]),
            ParamGroup("Day of Week (UTC)", [
                Param(_DAYS[i], lbl, True, "bool",
                      help=f"Allow entries on {lbl} (UTC). See the Polymarket "
                           f"presets for the measured weekend effect.")
                for i, lbl in enumerate(("Monday", "Tuesday", "Wednesday",
                                         "Thursday", "Friday", "Saturday",
                                         "Sunday"))
            ]),
        ]

    def presets(self) -> dict:
        # Filled in by the optimizer sweep -- see PRESETS below.
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        cci_len = p["cci_length"]
        cci_thr = p["cci_threshold"]
        wr_len = p["wr_length"]
        wr_ob, wr_os = p["wr_overbought"], p["wr_oversold"]
        use_wick = p["use_wick_confirm"]
        wick_min = p["wick_min"]
        recover_min = p["close_recover_min"]
        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        reversion = p["predict_direction"] == "Reversion"

        # Day gate (UTC). Index matches datetime.weekday(): Monday == 0.
        allowed_days = {i for i in range(7) if p[_DAYS[i]]}
        gate_days = len(allowed_days) < 7

        cci = ind.cci(candles, cci_len)
        wr = ind.williams_r(candles, wr_len)
        atr_vol = ind.atr(candles, vol_len)

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            if gate_days and datetime.fromtimestamp(
                    c["time"], timezone.utc).weekday() not in allowed_days:
                continue
            ci, w, a = cci[i], wr[i], atr_vol[i]
            if ci is None or w is None or a is None or a <= 0:
                continue

            o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
            rng = h - l
            if rng <= 0:
                continue

            # Volatility regime
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            # Both oscillators must agree on the same extreme
            if ci >= cci_thr and w >= wr_ob:
                extreme = "up"
            elif ci <= -cci_thr and w <= wr_os:
                extreme = "down"
            else:
                continue

            # Candle rejection
            if use_wick:
                if extreme == "up":
                    wick = h - max(o, cl)
                    recover = h - cl
                else:
                    wick = min(o, cl) - l
                    recover = cl - l
                if wick / rng < wick_min or recover / rng < recover_min:
                    continue

            if extreme == "up":
                side = "short" if reversion else "long"
            else:
                side = "long" if reversion else "short"

            mode = "reversion" if reversion else "continuation"
            reason = (f"{extreme.upper()}-exhaustion CCI {ci:+.0f} / %R {w:.0f} "
                      f"-> {mode} {side.upper()} (ATR% {atr_pct:.2f})")

            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"cci": round(ci, 1), "wr": round(w, 1),
                      "atr_pct": round(atr_pct, 3), "extreme": extreme, "mode": mode},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (set Mode = "Polymarket up/down",
# interval = 5m). Exit / Backtest params are unused in that mode.
#
# How these were found
# --------------------
# Grid sweep over the *entire* local DB: BTCUSDT 1m resampled to 5m,
# 2017-08-17 .. 2026-07-19 = 936,841 bars (~485k parameter combinations across a
# coarse pass and a refinement pass). Every combination was scored on next-candle
# direction, exactly as backend/polymarket.py resolves a bet.
#
# Selection was deliberately conservative, to ship an edge rather than a curve
# fit. A combination had to clear ALL of:
#   * a winning hit rate in EVERY calendar year it was active in (>= 30 bets),
#   * >= 53% over the 2024-2026 era on its own -- the era that most resembles
#     what you would be betting into now,
#   * binomial z >= 3 over the full span and >= 2 over the recent era.
# The five below are spread along the resulting bets-vs-hit-rate Pareto frontier,
# so you can trade volume against edge.
#
# Measured results (whole DB, flat $1 per bet)
# --------------------------------------------
#   preset      bets     hit     2024-26 bets   2024-26 hit   worst year
#   Volume     98,089   56.68%      32,230        54.01%        50.04%
#   Balanced   59,099   57.15%      18,008        55.26%        50.07%
#   Selective  24,553   58.60%       8,273        56.82%        50.10%
#   Hi Hit     13,518   59.48%       2,709        58.10%        50.27%
#   Max Hit     1,458   60.36%         285        63.51%        56.82%
#
# Read these with two caveats
# ---------------------------
# 1. THE EDGE DECAYS. Every preset scores several points lower in 2024-2026 than
#    in 2018-2023 (e.g. Volume: ~58% then, ~54% now). BTC 5m has grown more
#    efficient. Trust the "2024-26" column, not the headline number.
# 2. 2017 IS THE WEAK YEAR (~50%, and it is only a partial year: Aug-Dec). The
#    "every year must win" filter was binding almost exactly there, so a worst
#    year of ~50.1% reflects that constraint rather than a comfortable margin.
#    Max Hit is the exception -- it clears 56.8% even in its worst year.
#
# In this mode a bet is profitable only when hit rate > your odds, so e.g.
# Selective's 56.8% recent hit needs to be entered below ~0.568 to be +EV.
# Higher-hit presets fire far less often: Max Hit averages ~160 bets/year, and
# Hi Hit's ATR band makes it volatility-regime dependent (2,479 bets in 2021 vs
# 400 in 2023). Volume is the only one that fires on a daily cadence (~35/day).
# --- Polymarket 5m, day-aware sweep -----------------------------------------
# Whole DB (936,829 5m bars, 2017-08 .. 2026-07), Polymarket up/down mode. Two
# families of three tiers: all-days and weekend-gated (Sat+Sun, UTC). Admission:
# hit >50% every calendar year, overall z >= 2.5, and 2024-26 must still clear
# 52% so nothing already dead gets shipped.
#
#   preset             bets     hit    worst yr  2024-26  2025-26      z
#   Volume            28,767   58.51%     50.33%    55.73%    55.99%   28.9
#   Balanced          12,727   59.77%     52.78%    55.63%    55.67%   22.0
#   Hi Hit               793   62.42%     53.85%    62.26%    62.64%    7.0
#   Wknd Volume       13,361   59.65%     53.33%    57.71%    57.36%   22.3
#   Wknd Balanced      7,745   60.54%     50.44%    58.83%    58.41%   18.6
#   Wknd Hi Hit        1,164   63.23%     53.03%    57.45%    55.05%    9.0
#
# The weekend gate is the day finding: these exhaustion/fade setups resolve
# better on Sat+Sun than midweek. Tested as a single a-priori comparison (not
# best-of-7) on the pre-existing presets before any parameter was tuned on it.
#
# CAVEAT: selection used the FULL record with NO holdout, so these hit rates
# carry selection bias and the 2024-26 / 2025-26 columns are a recency check,
# not out-of-sample evidence. Days are UTC; a bar is stamped by its open time.
PRESETS: dict = {
    # Tighter %R band (-8/-92): only near-perfect range pins qualify.
    "PM 5m Selective": {
        "cci_length": 20, "cci_threshold": 190,
        "wr_length": 12, "wr_overbought": -8, "wr_oversold": -92,
        "use_wick_confirm": False, "wick_min": 0.0, "close_recover_min": 0.0,
        "vol_atr_length": 50, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "predict_direction": "Reversion",
    },
    # Extreme CCI (260) on a short window. Rare (~160 bets/yr) but the only
    # preset whose worst year is comfortably clear of 50%, and the only one that
    # scored HIGHER in 2024-2026 than over the full span.
    "PM 5m Max Hit": {
        "cci_length": 10, "cci_threshold": 260,
        "wr_length": 7, "wr_overbought": -10, "wr_oversold": -90,
        "use_wick_confirm": False, "wick_min": 0.0, "close_recover_min": 0.0,
        "vol_atr_length": 14, "atr_pct_min": 0.15, "atr_pct_max": 2.0,
        "predict_direction": "Reversion",
    },
    # 28,767 bets, 58.51% hit; 2024-26 55.73%, worst year 50.33%.
    "PM 5m Volume": {
        "cci_length": 16, "cci_threshold": 110, "wr_length": 10,
        "wr_overbought": -5, "wr_oversold": -95,
        "use_wick_confirm": True, "wick_min": 0.05,
        "close_recover_min": 0.0, "vol_atr_length": 14,
        "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "predict_direction": 'Reversion',
    },
    # 12,727 bets, 59.77% hit; 2024-26 55.63%, worst year 52.78%.
    "PM 5m Balanced": {
        "cci_length": 20, "cci_threshold": 150, "wr_length": 12,
        "wr_overbought": -5, "wr_oversold": -95,
        "use_wick_confirm": True, "wick_min": 0.05,
        "close_recover_min": 0.0, "vol_atr_length": 50,
        "atr_pct_min": 0.15, "atr_pct_max": 2.0,
        "predict_direction": 'Reversion',
    },
    # 793 bets, 62.42% hit; 2024-26 62.26%, worst year 53.85%.
    "PM 5m Hi Hit": {
        "cci_length": 10, "cci_threshold": 260, "wr_length": 7,
        "wr_overbought": -10, "wr_oversold": -90,
        "use_wick_confirm": True, "wick_min": 0.05,
        "close_recover_min": 0.0, "vol_atr_length": 14,
        "atr_pct_min": 0.15, "atr_pct_max": 2.0,
        "predict_direction": 'Reversion',
    },
    # 13,361 bets, 59.65% hit; 2024-26 57.71%, worst year 53.33%.
    "PM 5m Wknd Volume": {
        "cci_length": 16, "cci_threshold": 150, "wr_length": 10,
        "wr_overbought": -12, "wr_oversold": -88,
        "use_wick_confirm": True, "wick_min": 0.05,
        "close_recover_min": 0.0, "vol_atr_length": 14,
        "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": 'Reversion',
        **_WEEKEND,
    },
    # 7,745 bets, 60.54% hit; 2024-26 58.83%, worst year 50.44%.
    "PM 5m Wknd Balanced": {
        "cci_length": 20, "cci_threshold": 180, "wr_length": 10,
        "wr_overbought": -12, "wr_oversold": -88,
        "use_wick_confirm": True, "wick_min": 0.05,
        "close_recover_min": 0.0, "vol_atr_length": 14,
        "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": 'Reversion',
        **_WEEKEND,
    },
    # 1,164 bets, 63.23% hit; 2024-26 57.45%, worst year 53.03%.
    "PM 5m Wknd Hi Hit": {
        "cci_length": 24, "cci_threshold": 200, "wr_length": 21,
        "wr_overbought": -5, "wr_oversold": -95,
        "use_wick_confirm": True, "wick_min": 0.05,
        "close_recover_min": 0.0, "vol_atr_length": 14,
        "atr_pct_min": 0.15, "atr_pct_max": 2.0,
        "predict_direction": 'Reversion',
        **_WEEKEND,
    },
}
