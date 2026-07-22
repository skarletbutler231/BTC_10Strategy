"""Strategy #3 from the video: ATR DevExh (ATR deviation exhaustion).

Idea
----
Price is pushed to the edge of its recent range (a Donchian extreme). The
question that decides the trade is *how much steam the push has left*, measured
as *velocity in ATR units* — how far price travelled over the lookback, divided
by ATR, so the reading is comparable across volatility regimes.

A thrust that reaches a new extreme while **decelerating** is an exhausted move:
the range keeps extending but each bar buys less distance. That is the classic
fade setup. A thrust still **accelerating** into the extreme is the opposite —
a move with follow-through. The Velocity Mode picks which of those you want, and
the Decision group picks whether to fade the extreme or ride it.

Parameter groups (matching the config screen)
---------------------------------------------
Velocity     velocity_lookback, velocity_mode  (Decelerating | Accelerating | Any)
Donchian     donchian_length, donchian_confirm
Volatility   vol_atr_length, atr_pct_min, atr_pct_max
Decision     predict_direction  (Reversion | Breakout)
Trend Filter use_trend_filter, trend_mode, ma_type, ma_length

Entry logic (a bar must clear every active gate)
------------------------------------------------
  1. Extreme:  the bar makes a new `donchian_length`-bar high (or low).
  2. Confirm:  it is the `donchian_confirm`-th consecutive bar to do so on that
               same side — 1 means "the moment it breaks", higher values demand a
               sustained push before acting.
  3. Velocity: v = (close - close[velocity_lookback]) / ATR, signed and in ATR
               units. `Decelerating` needs |v| to be shrinking versus the prior
               bar (exhaustion); `Accelerating` needs it growing; `Any` skips
               the gate.
  4. Vol:      ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max].
  5. Trend(opt): close vs MA(ma_type, ma_length), per trend_mode.

Side: a new HIGH is faded SHORT under Reversion and ridden LONG under Breakout;
a new LOW mirrors it. TP/SL sizing uses the volatility ATR. In Polymarket mode
the exit params are ignored and each signal is a next-candle UP/DOWN bet
(long = UP).
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

_VELOCITY_MODES = ["Decelerating", "Accelerating", "Any"]
_DIRECTIONS = ["Reversion", "Breakout"]
_TREND_MODES = ["With Trend", "Against Trend"]
# index == the integer code ind.moving_average expects
_MA_TYPES = ["SMA", "EMA", "WMA", "RMA", "HMA"]


class AtrDevExh(Strategy):
    id = "atr_devexh"
    name = "ATR DevExh"
    description = ("Donchian-extreme thrusts judged by their velocity in ATR "
                   "units: fade the decelerating (exhausted) ones or ride the "
                   "accelerating ones.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Velocity", [
                Param("velocity_lookback", "Velocity Lookback", 5, "int", 1, 200, 1,
                      "Bars over which travel is measured: "
                      "(close - close[n]) / ATR, i.e. distance in ATR units."),
                Param("velocity_mode", "Velocity Mode", "Decelerating", "enum",
                      options=_VELOCITY_MODES,
                      help="Decelerating: |velocity| must be shrinking — the "
                           "thrust is running out of steam (exhaustion). "
                           "Accelerating: it must be growing. Any: no gate."),
            ]),
            ParamGroup("Donchian", [
                Param("donchian_length", "Donchian Length", 20, "int", 2, 400, 1,
                      "Lookback for the channel whose high/low defines an extreme."),
                Param("donchian_confirm", "Donchian Confirm", 1, "int", 1, 20, 1,
                      "How many consecutive bars must make a new extreme on the "
                      "same side before a signal fires. 1 = the moment it breaks."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback — normalises velocity, gates the regime, and "
                      "sizes TP/SL (xATR) for this strategy."),
                Param("atr_pct_min", "ATR % Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR % Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (too violent)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Reversion", "enum",
                      options=_DIRECTIONS,
                      help="Reversion fades the extreme (new high -> short). "
                           "Breakout rides it (new high -> long)."),
            ]),
            ParamGroup("Trend Filter", [
                Param("use_trend_filter", "Use Trend Filter", False, "bool",
                      help="Require the close to agree with a moving-average trend."),
                Param("trend_mode", "Trend Mode", "With Trend", "enum",
                      options=_TREND_MODES,
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
        # Tuned on 6 months, validated on 7 disjoint months, then measured across
        # all 13 (2025-03 .. 2026-06). What the data says:
        #   * REVERSION wins outright — fading the Donchian extreme beats riding it.
        #   * A LONG channel is where the edge lives: 55 bars beats 20 (more bets,
        #     lower hit) and beats 80-150 (which fall to 8-11 profitable months).
        #   * The VELOCITY gate does not earn its keep. Leaving it off ("Any") is
        #     the most robust setting — 13/13 months at 54.3% versus 12/13 at 55.0%
        #     for "Decelerating", which also halves the sample. The exhaustion
        #     premise in the name simply is not what predicts the next candle; the
        #     extreme itself is. Only the Hi Hit preset uses a velocity gate, and
        #     the setting that wins there is Accelerating, not Decelerating.
        #   * The trend filter only shrinks the sample (With Trend: 227 bets, 10/13).
        # A ~50% base rate of up-candles makes these hit rates real edge. All three
        # were positive in 13/13 months. Breakeven odds ~= the hit rate.
        # --- Polymarket 5m, day-aware sweep -----------------------------------------
        # Whole DB (936,829 5m bars, 2017-08 .. 2026-07), Polymarket up/down mode. Two
        # families of three tiers: all-days and weekend-gated (Sat+Sun, UTC). Admission:
        # hit >50% every calendar year, overall z >= 2.5, and 2024-26 must still clear
        # 52% so nothing already dead gets shipped.
        #
        #   preset             bets     hit    worst yr  2024-26  2025-26      z
        #   Volume             6,269   54.19%     50.26%    52.46%    51.55%    6.6
        #   Balanced           3,337   55.74%     50.28%    53.55%    53.22%    6.6
        #   Hi Hit             2,009   57.89%     50.30%    57.78%    56.98%    7.1
        #   Wknd Volume        7,871   57.76%     50.19%    56.36%    57.16%   13.8
        #   Wknd Balanced      2,890   56.47%     50.35%    55.12%    53.31%    7.0
        #   Wknd Hi Hit        1,135   58.33%     51.85%    55.38%    55.28%    5.6
        #
        # The weekend gate is the day finding: these exhaustion/fade setups resolve
        # better on Sat+Sun than midweek. Tested as a single a-priori comparison (not
        # best-of-7) on the pre-existing presets before any parameter was tuned on it.
        #
        # CAVEAT: selection used the FULL record with NO holdout, so these hit rates
        # carry selection bias and the 2024-26 / 2025-26 columns are a recency check,
        # not out-of-sample evidence. Days are UTC; a bar is stamped by its open time.
        return {
            # Maximum action: fires the moment a new 55-bar extreme is confirmed.
            # 53.9% hit over 6,521 bets (~502/mo), worst month 51.2%, 13/13.
            "Polymarket 5m (Max Volume)": {
                "predict_direction": "Reversion",
                "velocity_mode": "Any", "velocity_lookback": 5,
                "donchian_length": 55, "donchian_confirm": 2,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "use_trend_filter": False,
            },
            # Recommended. One more bar of confirmation buys a better hit rate,
            # and the velocity gate stays OFF — so it leans only on the lever that
            # held up under isolation. Hi Hit edges it numerically, but does so via
            # a velocity gate that was the less stable of the two in testing; prefer
            # this one if you want the structurally safer bet.
            # 54.3% hit over 3,123 bets (~240/mo), worst month 51.2%, 13/13.
            "Polymarket 5m (Balanced)": {
                "predict_direction": "Reversion",
                "velocity_mode": "Any", "velocity_lookback": 5,
                "donchian_length": 55, "donchian_confirm": 3,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "use_trend_filter": False,
            },
            # Best hit rate that keeps a large sample: fade a two-bar extreme while
            # velocity is still ACCELERATING into it (the strongest thrusts revert
            # hardest over one candle).
            # 54.7% hit over 3,369 bets (~259/mo), worst month 51.2%, 13/13.
            "Polymarket 5m (Hi Hit)": {
                "predict_direction": "Reversion",
                "velocity_mode": "Accelerating", "velocity_lookback": 5,
                "donchian_length": 55, "donchian_confirm": 2,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "use_trend_filter": False,
            },
            # 6,269 bets, 54.19% hit; 2024-26 52.46%, worst year 50.26%.
            "PM 5m Volume": {
                "velocity_lookback": 1, "velocity_mode": 'Accelerating',
                "donchian_length": 100, "donchian_confirm": 3,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "predict_direction": 'Reversion', "use_trend_filter": True,
                "trend_mode": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200,
            },
            # 3,337 bets, 55.74% hit; 2024-26 53.55%, worst year 50.28%.
            "PM 5m Balanced": {
                "velocity_lookback": 5, "velocity_mode": 'Accelerating',
                "donchian_length": 80, "donchian_confirm": 4,
                "vol_atr_length": 14, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
                "predict_direction": 'Reversion', "use_trend_filter": True,
                "trend_mode": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200,
            },
            # 2,009 bets, 57.89% hit; 2024-26 57.78%, worst year 50.30%.
            "PM 5m Hi Hit": {
                "velocity_lookback": 1, "velocity_mode": 'Decelerating',
                "donchian_length": 30, "donchian_confirm": 3,
                "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
                "predict_direction": 'Reversion', "use_trend_filter": True,
                "trend_mode": 'With Trend', "ma_type": 'EMA', "ma_length": 200,
            },
            # 7,871 bets, 57.76% hit; 2024-26 56.36%, worst year 50.19%.
            "PM 5m Wknd Volume": {
                "velocity_lookback": 10, "velocity_mode": 'Accelerating',
                "donchian_length": 10, "donchian_confirm": 3,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "predict_direction": 'Reversion', "use_trend_filter": True,
                "trend_mode": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200,
                **_WEEKEND,
            },
            # 2,890 bets, 56.47% hit; 2024-26 55.12%, worst year 50.35%.
            "PM 5m Wknd Balanced": {
                "velocity_lookback": 1, "velocity_mode": 'Accelerating',
                "donchian_length": 20, "donchian_confirm": 3,
                "vol_atr_length": 14, "atr_pct_min": 0.1, "atr_pct_max": 1.0,
                "predict_direction": 'Reversion', "use_trend_filter": True,
                "trend_mode": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200,
                **_WEEKEND,
            },
            # 1,135 bets, 58.33% hit; 2024-26 55.38%, worst year 51.85%.
            "PM 5m Wknd Hi Hit": {
                "velocity_lookback": 3, "velocity_mode": 'Accelerating',
                "donchian_length": 100, "donchian_confirm": 3,
                "vol_atr_length": 14, "atr_pct_min": 0.03, "atr_pct_max": 5.0,
                "predict_direction": 'Reversion', "use_trend_filter": True,
                "trend_mode": 'Against Trend', "ma_type": 'EMA',
                "ma_length": 200,
                **_WEEKEND,
            },
        }

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        vel_n = p["velocity_lookback"]
        vel_mode = p["velocity_mode"]
        dc_len, dc_confirm = p["donchian_length"], p["donchian_confirm"]
        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        reversion = p["predict_direction"] == "Reversion"
        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_mode"] == "With Trend"
        ma_code = _MA_TYPES.index(p["ma_type"]) if p["ma_type"] in _MA_TYPES else 1
        ma_len = p["ma_length"]

        # Day gate (UTC). Index matches datetime.weekday(): Monday == 0.
        allowed_days = {i for i in range(7) if p[_DAYS[i]]}
        gate_days = len(allowed_days) < 7

        n = len(candles)
        closes = [c["close"] for c in candles]
        atr = ind.atr(candles, vol_len)
        up, lo = ind.donchian(candles, dc_len)
        ma = ind.moving_average(closes, ma_len, ma_code) if use_trend else None

        # signed velocity in ATR units, and its magnitude, per bar
        speed: List[float | None] = [None] * n
        for i in range(n):
            a = atr[i]
            if a is None or a <= 0 or i - vel_n < 0:
                continue
            speed[i] = abs(closes[i] - closes[i - vel_n]) / a

        # run-length of consecutive new-extreme bars, per side
        run_hi = [0] * n
        run_lo = [0] * n
        for i in range(n):
            if up[i] is None:
                continue
            is_hi = candles[i]["high"] >= up[i]
            is_lo = candles[i]["low"] <= lo[i]
            run_hi[i] = (run_hi[i - 1] + 1) if (is_hi and i > 0) else (1 if is_hi else 0)
            run_lo[i] = (run_lo[i - 1] + 1) if (is_lo and i > 0) else (1 if is_lo else 0)

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            if gate_days and datetime.fromtimestamp(
                    c["time"], timezone.utc).weekday() not in allowed_days:
                continue
            a = atr[i]
            if a is None or a <= 0 or up[i] is None:
                continue
            cl = c["close"]

            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            # --- Donchian extreme + confirmation run ----------------------
            if run_hi[i] >= dc_confirm and run_hi[i] > 0:
                edge = "high"
            elif run_lo[i] >= dc_confirm and run_lo[i] > 0:
                edge = "low"
            else:
                continue
            # a bar that is simultaneously both extremes is an inside-out bar; skip
            if run_hi[i] >= dc_confirm and run_lo[i] >= dc_confirm:
                continue

            # --- velocity gate --------------------------------------------
            if vel_mode != "Any":
                s_now, s_prev = speed[i], (speed[i - 1] if i > 0 else None)
                if s_now is None or s_prev is None:
                    continue
                if vel_mode == "Decelerating" and not (s_now < s_prev):
                    continue
                if vel_mode == "Accelerating" and not (s_now > s_prev):
                    continue

            if edge == "high":
                side = "short" if reversion else "long"
            else:
                side = "long" if reversion else "short"

            # --- optional trend filter ------------------------------------
            if use_trend:
                m = ma[i]
                if m is None:
                    continue
                agrees = (cl > m) if side == "long" else (cl < m)
                ok = agrees if trend_with else (not agrees)
                if not ok:
                    continue

            v = speed[i]
            mode = "reversion" if reversion else "breakout"
            reason = (f"New {dc_len}-bar {edge} x{run_hi[i] if edge == 'high' else run_lo[i]} "
                      f"-> {mode} {side.upper()} "
                      f"(vel {v:.2f} ATR, {vel_mode.lower()}, ATR% {atr_pct:.2f})"
                      if v is not None else
                      f"New {dc_len}-bar {edge} -> {mode} {side.upper()} (ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"edge": edge, "run": run_hi[i] if edge == "high" else run_lo[i],
                      "velocity_atr": (round(v, 3) if v is not None else None),
                      "atr_pct": round(atr_pct, 3), "mode": mode},
            ))
        return signals
