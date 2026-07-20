"""Strategy #8 from the video: Jump Exhaustion  ("fade the overshoot").

Idea
----
An abnormal, discontinuous bar (a "jump" — a Levy-style gap) tends to overshoot.
When a jump pushes price to a local extreme *and* shows an intrabar rejection
wick *and* momentum is stretched (RSI), the move is often exhausted, so we fade
it (short an up-jump, long a down-jump) and target a small mean reversion.

Parameter groups (matching the config shown in the video)
---------------------------------------------------------
Core        atr_length, jump1_atr_mult, jump2_atr_mult
Candle      close_extreme_min, wick_min_ratio
RSI         rsi_length, rsi_overbought, rsi_oversold
Volatility  vol_atr_length, atr_pct_min, atr_pct_max

Entry logic for an UP-jump (mirror for a down-jump)
---------------------------------------------------
  1. Jump size:  bar_range / ATR(atr_length) must be in [jump1_atr_mult,
     jump2_atr_mult].  jump1 = "is this a jump?";  jump2 = "...but not a monster
     breakout we shouldn't fade."  (The upper bound encodes the lesson that on
     the biggest moves price keeps going instead of reverting.)
  2. Close extreme:  the close sits in the top `close_extreme_min` fraction of
     the last `atr_length` closes -> price pushed to a fresh local high.
  3. Rejection wick:  upper wick / bar range >= wick_min_ratio -> intrabar the
     spike went even higher and got sold, i.e. an overshoot.
  4. Momentum stretched:  RSI(rsi_length) >= rsi_overbought.
  5. Volatility regime:  ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max]
     -> skip dead tape (no follow-through) and violent regimes (jumps trend).

All of 1-5 must hold -> emit a SHORT signal (fade). Down-jump is the mirror:
close near local low, lower wick, RSI <= rsi_oversold -> LONG.
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup, Signal, Strategy


class JumpExhaustion(Strategy):
    id = "jump_exhaustion"
    name = "Jump Exhaustion"
    description = ("Fade abnormal (jump) candles that overshoot to a local "
                   "extreme with a rejection wick and stretched RSI.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Core", [
                Param("atr_length", "ATR length", 14, "int", 2, 200, 1,
                      "Lookback for the ATR used to size jumps."),
                Param("jump1_atr_mult", "Jump1 ATR mult (min)", 1.8, "float", 0.5, 10, 0.1,
                      "Minimum bar range in ATRs to count as a jump."),
                Param("jump2_atr_mult", "Jump2 ATR mult (max)", 4.0, "float", 1.0, 20, 0.1,
                      "Maximum bar range in ATRs; bigger moves are NOT faded."),
            ]),
            ParamGroup("Candle", [
                Param("close_extreme_min", "Close extreme min", 0.55, "float", 0.0, 1.0, 0.01,
                      "How far (0-1) the close must be toward the local high/low "
                      "over the ATR-length window."),
                Param("wick_min_ratio", "Wick min ratio", 0.30, "float", 0.0, 1.0, 0.01,
                      "Minimum rejection wick as a fraction of the bar range."),
            ]),
            ParamGroup("RSI", [
                Param("rsi_length", "RSI length", 14, "int", 2, 100, 1,
                      "Lookback for RSI."),
                Param("rsi_overbought", "RSI overbought", 68, "float", 50, 100, 1,
                      "Up-jumps only fade above this RSI."),
                Param("rsi_oversold", "RSI oversold", 32, "float", 0, 50, 1,
                      "Down-jumps only fade below this RSI."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR length", 20, "int", 2, 200, 1,
                      "Lookback for the regime ATR (as % of price)."),
                Param("atr_pct_min", "ATR% min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR% max", 1.2, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent, trending regime)."),
            ]),
        ]

    def presets(self) -> dict:
        return {
            "Aggressive": {
                "jump1_atr_mult": 1.3, "jump2_atr_mult": 5.0,
                "close_extreme_min": 0.45, "wick_min_ratio": 0.20,
                "rsi_overbought": 62, "rsi_oversold": 38,
            },
            "Conservative": {
                "jump1_atr_mult": 2.2, "jump2_atr_mult": 3.5,
                "close_extreme_min": 0.7, "wick_min_ratio": 0.4,
                "rsi_overbought": 72, "rsi_oversold": 28,
            },
        }

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        atr_len = p["atr_length"]
        jmin, jmax = p["jump1_atr_mult"], p["jump2_atr_mult"]
        ce_min = p["close_extreme_min"]
        wick_min = p["wick_min_ratio"]
        rsi_len = p["rsi_length"]
        ob, os = p["rsi_overbought"], p["rsi_oversold"]
        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        atr = ind.atr(candles, atr_len)
        atr_vol = ind.atr(candles, vol_len)
        rsi = ind.rsi(candles, rsi_len)
        clo, chi = ind.rolling_close_extremes(candles, atr_len)

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            a, av, r = atr[i], atr_vol[i], rsi[i]
            lo, hi = clo[i], chi[i]
            if None in (a, av, r, lo, hi) or a <= 0:
                continue

            o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
            rng = h - l
            if rng <= 0:
                continue

            jump = rng / a
            if jump < jmin or jump > jmax:
                continue

            atr_pct = av / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            span = hi - lo
            if span <= 0:
                continue
            pos = (cl - lo) / span  # 1 = fresh local high close, 0 = fresh low

            up_wick = h - max(o, cl)
            dn_wick = min(o, cl) - l

            if cl > o:  # bullish up-jump -> fade SHORT
                if pos < ce_min:
                    continue
                if up_wick / rng < wick_min:
                    continue
                if r < ob:
                    continue
                side = "short"
                reason = (f"Up-jump {jump:.1f}xATR faded "
                          f"(RSI {r:.0f}, wick {up_wick / rng:.0%}, ATR% {atr_pct:.2f})")
            elif cl < o:  # bearish down-jump -> fade LONG
                if (1.0 - pos) < ce_min:
                    continue
                if dn_wick / rng < wick_min:
                    continue
                if r > os:
                    continue
                side = "long"
                reason = (f"Down-jump {jump:.1f}xATR faded "
                          f"(RSI {r:.0f}, wick {dn_wick / rng:.0%}, ATR% {atr_pct:.2f})")
            else:
                continue

            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl,
                reason=reason, atr=a,
                meta={"jump_atr": round(jump, 2), "rsi": round(r, 1),
                      "atr_pct": round(atr_pct, 3), "close_pos": round(pos, 2)},
            ))
        return signals
