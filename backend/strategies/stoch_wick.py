"""Strategy #2 from the video: Stoch Wick ("rejection at a stochastic extreme").

Idea
----
The stochastic oscillator says *where price sits inside its recent range*. At an
extreme (oversold / overbought) the range is stretched; if that bar also prints a
rejection wick and closes back off its extreme, buyers/sellers were absorbed
intrabar. That pairing — a stochastic extreme plus a rejection candle — is the
signal. What you do with it is up to the Decision group:

  * **Reversion** — oversold + lower-wick rejection goes LONG; overbought +
    upper-wick rejection goes SHORT. (Fade the extreme.)
  * **Breakout**  — the mirror: the extreme is read as continuation instead.

Because mean reversion only works in balanced tape, the headline filter here is
**ADX**: entries are gated to a *ranging* market (ADX at or below a ceiling).
A volatility band and an optional moving-average trend filter refine it further.

Parameter groups (matching the config screen)
---------------------------------------------
Stochastic   stoch_k_length, stoch_d_length, overbought, oversold
Candle       min_wick_ratio, min_close_recovery
ADX Filter   use_adx_filter, adx_length, adx_max
Volatility   vol_atr_length, atr_pct_min, atr_pct_max
Decision     predict_direction  (Reversion | Breakout)
Trend Filter use_trend_filter, trend_logic, ma_type, ma_length

Entry logic (a bar must clear every active gate)
------------------------------------------------
  1. Zone:      %K and %D both <= oversold (or both >= overbought).
  2. Wick:      rejection wick / bar range >= min_wick_ratio, measured on the
                zone's side (lower wick when oversold, upper when overbought).
  3. Recovery:  the close sits >= min_close_recovery of the way back off that
                extreme -> the reversal has already started on this bar.
  4. ADX (opt): ADX(adx_length) <= adx_max, i.e. the tape is ranging.
  5. Vol:       ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max].
  6. Trend(opt):close vs MA(ma_type, ma_length), per trend_logic.

TP/SL sizing uses the volatility ATR, fed to the shared Exit/Backtest group. In
Polymarket mode the exit params are ignored and each signal is a next-candle
UP/DOWN bet (long = UP).
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

_DIRECTIONS = ["Reversion", "Breakout"]
_TREND_LOGIC = ["With Trend", "Against Trend"]
# index == the integer code ind.moving_average expects
_MA_TYPES = ["SMA", "EMA", "WMA", "RMA", "HMA"]


class StochWick(Strategy):
    id = "stoch_wick"
    name = "Stoch Wick"
    description = ("Rejection candles at a stochastic extreme, gated to ranging "
                   "tape by ADX. Trade them as a fade (Reversion) or as "
                   "continuation (Breakout).")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Stochastic", [
                Param("stoch_k_length", "Stoch K Length", 14, "int", 2, 200, 1,
                      "Lookback for %K (the raw position inside the range)."),
                Param("stoch_d_length", "Stoch D Length", 3, "int", 1, 50, 1,
                      "Smoothing of %K into %D. Both must be in the zone."),
                Param("overbought", "Overbought Level", 80, "float", 50, 100, 1,
                      "%K and %D at/above this count as overbought."),
                Param("oversold", "Oversold Level", 20, "float", 0, 50, 1,
                      "%K and %D at/below this count as oversold."),
            ]),
            ParamGroup("Candle", [
                Param("min_wick_ratio", "Min Wick Ratio", 0.20, "float", 0.0, 1.0, 0.01,
                      "Minimum rejection wick as a fraction of the bar range, on "
                      "the zone's side (lower wick when oversold)."),
                Param("min_close_recovery", "Min Close Recovery", 0.30, "float", 0.0, 1.0, 0.01,
                      "How far the close must recover off the bar's extreme "
                      "(1 = closed at the opposite end of the bar)."),
            ]),
            ParamGroup("ADX Filter", [
                Param("use_adx_filter", "Use ADX Filter", True, "bool",
                      help="Only trade ranging tape. On by default: fading an "
                           "extreme inside a strong trend is what breaks this "
                           "family of strategies."),
                Param("adx_length", "ADX Length", 14, "int", 2, 100, 1,
                      "Lookback for Wilder's ADX."),
                Param("adx_max", "ADX Max (ranging)", 25, "float", 5, 100, 1,
                      "Skip entries when ADX is above this — the market is "
                      "trending rather than ranging."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also sizes TP/SL (xATR) for this strategy."),
                Param("atr_pct_min", "ATR % Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR % Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (too violent)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Reversion", "enum",
                      options=_DIRECTIONS,
                      help="Reversion fades the extreme (oversold -> long). "
                           "Breakout reads it as continuation (oversold -> short)."),
            ]),
            ParamGroup("Trend Filter", [
                Param("use_trend_filter", "Use Trend Filter", False, "bool",
                      help="Require the close to agree with a moving-average trend."),
                Param("trend_logic", "Trend Logic", "With Trend", "enum",
                      options=_TREND_LOGIC,
                      help="With Trend: longs need close>MA, shorts close<MA. "
                           "Against Trend: the opposite."),
                Param("ma_type", "MA Type", "EMA", "enum", options=_MA_TYPES,
                      help="Moving-average type for the trend filter."),
                Param("ma_length", "MA Length", 200, "int", 2, 500, 1,
                      "Lookback for the trend MA (on close)."),
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
        # --- Polymarket 5-minute UP/DOWN mode ---------------------------------
        # Run with Mode = "Polymarket up/down", interval 5m. Each signal is an
        # independent bet that the NEXT 5m candle closes in the predicted
        # direction; TP/SL and fees are ignored.
        #
        # Tuned on 6 months and validated on 7 disjoint months, then measured
        # across all 13 (2025-03 .. 2026-06). What the data says:
        #   * REVERSION wins outright — fading the extreme beats following it.
        #   * The deepest zone (90/10) is where the edge lives; 80/20 is too shallow.
        #   * The WICK/RECOVERY filters HURT here, monotonically: turning them off
        #     lifts hit rate from 52.4% -> 54.9% and months-positive from 7/13 ->
        #     13/13. They shrink the sample without adding prediction, so all three
        #     presets disable them. (They remain useful in TP/SL mode, where the
        #     objective is a multi-bar move rather than one candle.)
        #   * ADX earns its place only as a LOOSE ceiling (30) that removes the
        #     strongest trends; a tight 20 cuts the sample and gets worse.
        # A ~50% base rate of up-candles makes these hit rates real edge. All three
        # were positive in 13/13 months. Breakeven odds ~= the hit rate.
        # --- Polymarket 5m, day-aware sweep -----------------------------------------
        # Whole DB (936,829 5m bars, 2017-08 .. 2026-07), Polymarket up/down mode. Two
        # families of three tiers: all-days and weekend-gated (Sat+Sun, UTC).
        #
        # Admission: hit >50% in every calendar year THAT HAS AT LEAST 25 BETS, overall
        # z >= 2.5, and the 2024-26 span must still clear 52% so nothing already dead
        # gets shipped. The 25-bet floor matters: 2017 is a partial year (Aug-Dec) and
        # on the thinner presets it holds too few bets to be evidence either way, so it
        # is exempt. Where that lets a sub-50% year through it is called out below.
        #
        #   preset             bets     hit    worst yr  2024-26  2025-26      z
        #   Volume            22,492   58.26%     51.47%    55.77%    55.61%   24.8
        #   Balanced          11,548   59.22%     51.40%    55.95%    55.56%   19.8
        #   Hi Hit               908   60.24%     56.88%    58.50%    57.38%    6.2
        #   Wknd Volume       12,386   58.24%     52.78%    57.19%    56.85%   18.3
        #   Wknd Balanced      6,308   59.04%     54.93%    57.07%    56.48%   14.4
        #   Wknd Hi Hit          360   66.94%     55.88%    64.78%    60.40%    6.4
        #
        # The weekend gate is the day finding: these setups resolve better on Sat+Sun
        # than midweek. Tested as a single a-priori comparison (not best-of-7) on the
        # pre-existing presets before any parameter was tuned on it.
        #
        # CAVEAT: selection used the FULL record with NO holdout, so these hit rates
        # carry selection bias and the 2024-26 / 2025-26 columns are a recency check,
        # not out-of-sample evidence. Days are UTC; a bar is stamped by its open time.
        return {
            # Maximum action: no filters beyond the stochastic zone.
            # 55.0% hit over 10,706 bets (~823/mo), worst month 52.3%, 13/13.
            "Polymarket 5m (Max Volume)": {
                "predict_direction": "Reversion",
                "stoch_k_length": 14, "stoch_d_length": 3,
                "overbought": 90, "oversold": 10,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_adx_filter": False, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "use_trend_filter": False,
            },
            # Recommended. Same trigger, gated to non-trending tape by a loose ADX.
            # 55.2% hit over 7,520 bets (~578/mo), worst month 52.3%, 13/13.
            "Polymarket 5m (Balanced)": {
                "predict_direction": "Reversion",
                "stoch_k_length": 14, "stoch_d_length": 3,
                "overbought": 90, "oversold": 10,
                "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_adx_filter": True, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "use_trend_filter": False,
            },
            # Highest hit rate that still keeps a large sample: adds back a token
            # wick requirement only (recovery stays off).
            # 55.5% hit over 3,714 bets (~286/mo), worst month 51.9%, 13/13.
            "Polymarket 5m (Hi Hit)": {
                "predict_direction": "Reversion",
                "stoch_k_length": 14, "stoch_d_length": 3,
                "overbought": 90, "oversold": 10,
                "min_wick_ratio": 0.10, "min_close_recovery": 0.0,
                "use_adx_filter": True, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "use_trend_filter": False,
            },
            # 22,492 bets, 58.26% hit; 2024-26 55.77%, worst year 51.47%.
            "PM 5m Volume": {
                "stoch_k_length": 14, "stoch_d_length": 1, "overbought": 95,
                "oversold": 5, "min_wick_ratio": 0.1, "min_close_recovery": 0.0,
                "use_adx_filter": False, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
                "use_trend_filter": False, "trend_logic": 'With Trend',
                "ma_type": 'EMA', "ma_length": 200,
                "predict_direction": 'Reversion',
            },
            # 11,548 bets, 59.22% hit; 2024-26 55.95%, worst year 51.40%.
            "PM 5m Balanced": {
                "stoch_k_length": 14, "stoch_d_length": 1, "overbought": 95,
                "oversold": 5, "min_wick_ratio": 0.1, "min_close_recovery": 0.0,
                "use_adx_filter": False, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.2, "atr_pct_max": 3.0,
                "use_trend_filter": False, "trend_logic": 'With Trend',
                "ma_type": 'EMA', "ma_length": 200,
                "predict_direction": 'Reversion',
            },
            # 908 bets, 60.24% hit; 2024-26 58.50%, worst year 56.88%.
            "PM 5m Hi Hit": {
                "stoch_k_length": 14, "stoch_d_length": 3, "overbought": 95,
                "oversold": 5, "min_wick_ratio": 0.1, "min_close_recovery": 0.0,
                "use_adx_filter": True, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
                "use_trend_filter": True, "trend_logic": 'With Trend',
                "ma_type": 'EMA', "ma_length": 200,
                "predict_direction": 'Reversion',
            },
            # 12,386 bets, 58.24% hit; 2024-26 57.19%, worst year 52.78%.
            "PM 5m Wknd Volume": {
                "stoch_k_length": 7, "stoch_d_length": 5, "overbought": 80,
                "oversold": 20, "min_wick_ratio": 0.1, "min_close_recovery": 0.0,
                "use_adx_filter": False, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
                "use_trend_filter": True, "trend_logic": 'Against Trend',
                "ma_type": 'EMA', "ma_length": 200,
                "predict_direction": 'Reversion',
                **_WEEKEND,
            },
            # 6,308 bets, 59.04% hit; 2024-26 57.07%, worst year 54.93%.
            "PM 5m Wknd Balanced": {
                "stoch_k_length": 21, "stoch_d_length": 3, "overbought": 90,
                "oversold": 10, "min_wick_ratio": 0.1, "min_close_recovery": 0.0,
                "use_adx_filter": False, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
                "use_trend_filter": True, "trend_logic": 'Against Trend',
                "ma_type": 'EMA', "ma_length": 200,
                "predict_direction": 'Reversion',
                **_WEEKEND,
            },
            # 360 bets, 66.94% hit; 2024-26 64.78%, worst year 55.88%.
            "PM 5m Wknd Hi Hit": {
                "stoch_k_length": 7, "stoch_d_length": 3, "overbought": 95,
                "oversold": 5, "min_wick_ratio": 0.1, "min_close_recovery": 0.0,
                "use_adx_filter": True, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "use_trend_filter": True, "trend_logic": 'Against Trend',
                "ma_type": 'EMA', "ma_length": 200,
                "predict_direction": 'Reversion',
                **_WEEKEND,
            },
        }

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        k_len, d_len = p["stoch_k_length"], p["stoch_d_length"]
        ob, os = p["overbought"], p["oversold"]
        wick_min, rec_min = p["min_wick_ratio"], p["min_close_recovery"]

        use_adx = bool(p["use_adx_filter"])
        adx_len, adx_max = p["adx_length"], p["adx_max"]

        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        reversion = p["predict_direction"] == "Reversion"

        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_logic"] == "With Trend"
        ma_code = _MA_TYPES.index(p["ma_type"]) if p["ma_type"] in _MA_TYPES else 1
        ma_len = p["ma_length"]

        # Day gate (UTC). Index matches datetime.weekday(): Monday == 0.
        allowed_days = {i for i in range(7) if p[_DAYS[i]]}
        gate_days = len(allowed_days) < 7

        closes = [c["close"] for c in candles]
        k, d = ind.stochastic(candles, k_len, d_len)
        atr = ind.atr(candles, vol_len)
        adx = ind.adx(candles, adx_len) if use_adx else None
        ma = ind.moving_average(closes, ma_len, ma_code) if use_trend else None

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            if gate_days and datetime.fromtimestamp(
                    c["time"], timezone.utc).weekday() not in allowed_days:
                continue
            kk, dd, a = k[i], d[i], atr[i]
            if kk is None or dd is None or a is None or a <= 0:
                continue

            o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
            rng = h - l
            if rng <= 0:
                continue

            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            if use_adx:
                av = adx[i]
                if av is None or av > adx_max:
                    continue

            # --- stochastic zone ------------------------------------------
            if kk <= os and dd <= os:
                zone = "oversold"
                wick = (min(o, cl) - l) / rng      # lower wick
                recovery = (cl - l) / rng          # closed back up off the low
            elif kk >= ob and dd >= ob:
                zone = "overbought"
                wick = (h - max(o, cl)) / rng      # upper wick
                recovery = (h - cl) / rng          # closed back down off the high
            else:
                continue

            if wick < wick_min or recovery < rec_min:
                continue

            if reversion:
                side = "long" if zone == "oversold" else "short"
            else:
                side = "short" if zone == "oversold" else "long"

            # --- optional trend filter ------------------------------------
            if use_trend:
                m = ma[i]
                if m is None:
                    continue
                agrees = (cl > m) if side == "long" else (cl < m)
                ok = agrees if trend_with else (not agrees)
                if not ok:
                    continue

            mode = "reversion" if reversion else "breakout"
            reason = (f"{zone.capitalize()} rejection -> {mode} {side.upper()} "
                      f"(%K {kk:.0f}/%D {dd:.0f}, wick {wick:.0%}, "
                      f"ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"k": round(kk, 1), "d": round(dd, 1), "zone": zone,
                      "wick": round(wick, 2), "recovery": round(recovery, 2),
                      "atr_pct": round(atr_pct, 3),
                      "adx": (round(adx[i], 1) if use_adx and adx[i] is not None else None)},
            ))
        return signals
