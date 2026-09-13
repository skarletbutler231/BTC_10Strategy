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
            # --- Re-optimized for HIT RATE at similar volume, 2yr window ------
            # Same coarse grid as before (stoch_k_length x overbought x
            # oversold x min_wick_ratio; 150 combos), trailing 2 years only
            # (2024-07-19 -> 2026-07-19). CHANGED OBJECTIVE from the previous
            # version of this preset: instead of maximizing bet count, this
            # picks the combo whose bet count stays close to the full-history
            # "PM 5m Volume" preset's own trailing-2yr count (7,075 bets) while
            # maximizing hit rate, subject to both halves of the window
            # individually clearing 50%. Result: 5,192 bets (73% of baseline
            # -- needed a +/-40% band) at 55.59% hit (56.3% / 54.8% by half)
            # vs. baseline's 7,075 bets at 55.53%.
            # CAVEAT: that's a +0.06pp edge over baseline -- noise, not a real
            # improvement (standard error at this n is roughly +/-1pp). Kept
            # as the best available, but don't read this as meaningfully
            # better than just using "PM 5m Volume" directly.
            "PM 5m Volume - 2yr Train": {
                "stoch_k_length": 7, "stoch_d_length": 1, "overbought": 95,
                "oversold": 5, "min_wick_ratio": 0.1, "min_close_recovery": 0.0,
                "use_adx_filter": False, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
                "use_trend_filter": False, "trend_logic": "With Trend",
                "ma_type": "EMA", "ma_length": 200,
                "predict_direction": "Reversion",
            },
            # -------------------------------------------------------------------------
            # 15-MINUTE preset, fitted on the latest six months of BTCUSDT
            # 15m under the protocol the Reversal, Oscillators and Gann 15m
            # presets use: LOADED 2026-03-13 -> 2026-09-13 (train 03-13 ->
            # 07-13 for selection, holdout 07-13 -> 09-13 scored once after
            # the pick was frozen); UNLOADED 2017-08-17 -> 2026-03-13, never
            # read by any sweep stage and scored once at the end as the real
            # out-of-sample check (33,116 bets there against 2,457 in the
            # window). The selection rule was fixed before tuning: train
            # bets >= 300, both train halves above 52%, every swept
            # parameter off its grid edge, then highest train hit — read
            # against the marginals, since at a few hundred bets a config
            # the SE is 1.5-2.5pp and the single best row is mostly noise.
            #
            # SWEEP. One stage of 720 configs raced Reversion/Breakout x
            # stoch_k_length {5..28} x stoch_d_length {1, 3} x band
            # {5/95..25/75} x min_wick_ratio {0, 0.1, 0.2} x ADX gate
            # on/off; a second pass (~20) tried %D 1-5, the recovery filter,
            # the ATR band, ADX ceilings 20-40, the trend filter and a
            # weekend gate on the frozen pick. Recovery at 0 throughout, as
            # every 5m preset has it.
            #
            # FOUND. Reversion is the family and Breakout its exact mirror
            # (pooled train 55.56% vs 44.40%). Inside Reversion the
            # marginals say: the band is monotone — tighter is better (5/95:
            # 58.64% train, 25/75: 54.64%) at a steep cost in bets (187 vs
            # 2,991 a config); k=14 is the best length on both train and
            # holdout (56.01 / 53.95) and 28 loses the holdout; %D 3 beats 1
            # on train and loses on the holdout; and the rejection WICK, the
            # setup's namesake, is monotone against itself on the holdout
            # (0.0: 54.41%, 0.1: 52.68%, 0.2: 50.80%), with the recovery
            # filter worse still (0.2 drops the pick to 55.11%, holdout
            # 52.51%). The stochastic extreme carries the edge; the candle
            # does not — the same verdict RSI+BB's candle filters got. The
            # rule's own best row (k14 %D3, band 10/90, wick 0.1) has 616
            # window bets at 59.09% and 58.01% unloaded, but 2025 at 52.0%.
            # Opening the band to 15/85 and switching the wick off puts it
            # on the same plateau with four times the bets, the most even
            # train halves in the sweep (57.13 / 57.83) and a better worst
            # year, so that is the preset. The ADX gate is a hit-for-volume
            # dial (ADX <= 20: 701 bets at 58.06%; off: 2,457 at 56.86%) and
            # is left off; the ATR band and trend filter are inert or only
            # remove bets.
            #
            # RESULTS — flat $1 per bet, next-candle direction
            #   preset             6m bets  6m hit   train   HOLDOUT   unloaded 8.5y             worst yr
            #   PM 15m Balanced     2,457  56.86%  57.49%   55.83%    57.39% (33,116, z +26.9)  54.21% (2025)
            #
            # Per year on the full record, none of it fitted except the last six months:
            #   2017  52.28% (1,754)     2018  60.67% (2,911)     2019  60.16% (2,525)     2020  61.72% (3,054)
            #   2021  57.69% (4,448)     2022  56.33% (3,437)     2023  58.02% (3,204)     2024  56.74% (4,871)
            #   2025  54.21% (6,069)     2026  57.55% (3,300)
            #
            # Train halves 57.12% / 57.82%. About 13.3 bets a day. The 18
            # months right before the window (2024-09 -> 2026-03) score
            # 55.10% on 8,588 bets; whole record 35,573 bets, 57.35%, z
            # +27.7. Read the hit rates against 49.9%: 0.13% of 15m candles
            # close exactly at their open. Checks after the pick was frozen:
            # the prefix (no look-ahead) test passes with 0 mismatches at
            # three cut points; bets run 47% long in the window and both
            # sides win (window long 57.01% / short 56.73%; unloaded 57.85%
            # / 57.10%); the mirror on the same settings scores 43.14% in
            # the window and 42.51% unloaded.
            #
            # WHERE IT FAILS. The worst month in the window is 2026-08 at
            # 53.8% on 444 bets; the worst full year 2025 at 54.21%. The
            # edge DECAYS: 2018-2020 run 60-62%, 2025 is the worst full year
            # of every config tried (52-54%) and 2026-08 ran at 53.8% on 444
            # bets. The recent numbers are the live estimate. The 0.50-odds
            # EV the dashboard prints assumes a fill at even; a real 15m
            # book prices away from it. Hit rate is the finding.
            #
            # NOT SHIPPED. PM 5m Volume carried over unchanged (band 5/95,
            # wick 0.1) prints 60.91% on 440 window bets and 59.63% on 5,534
            # unloaded — the highest hit rate here, on a sixth of the bets;
            # a Selective tier if one is wanted. With Trend EMA200: 798 bets
            # at 58.90% (train 60.81, holdout 55.78). ADX <= 20: 701 at
            # 58.06%. %D 5: 1,625 at 57.66% with the strongest train (58.97)
            # but a holdout under the pick's.
            "PM 15m Balanced": {
                "stoch_k_length": 14, "stoch_d_length": 3, "overbought": 85,
                "oversold": 15, "min_wick_ratio": 0.0, "min_close_recovery": 0.0,
                "use_adx_filter": False, "adx_length": 14, "adx_max": 30,
                "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
                "use_trend_filter": False, "trend_logic": "With Trend",
                "ma_type": "EMA", "ma_length": 200,
                "predict_direction": "Reversion",
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
