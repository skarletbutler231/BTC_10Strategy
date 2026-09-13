"""Fair Value Gap (FVG) — trade the retest of a 3-candle price imbalance.

Idea
----
A Fair Value Gap is a three-candle pattern where a strong "impulse" candle
leaves a zone of price that never traded in either direction: the middle
candle moves so fast that the wick of the candle before it and the wick of
the candle after it don't overlap. That untraded zone is read as an
*imbalance* — the market left in a hurry rather than auctioning fairly — and
is expected to often get revisited (a "fill") before price decides what to
do next.

  Bullish FVG:  low[i] > high[i-2]   -> gap = [high[i-2], low[i]]
  Bearish FVG:  high[i] < low[i-2]   -> gap = [high[i], low[i-2]]

This strategy watches every gap that forms, waits for price to retrace back
into it, and fires when the retracement reaches `entry_depth` of the way
through the gap (0 = just tags the near edge, 0.5 = the gap's midpoint —
the "CE"/equilibrium level ICT traders commonly watch, 1 = fully fills it).
`predict_direction` then decides whether to ride the original impulse
(Continuation: buy a bullish-gap retest, betting it bounces and continues
up) or fade it (Reversion: bet the gap fully fails and reverses through).

An optional reaction filter can require the retest bar itself to show a
rejection (close back away from the gap) before the signal counts, similar
to the wick/opposing-bar confirmations used elsewhere in this repo — and,
consistent with what those have shown on BTC 5m, don't be surprised if a
sweep sets it to "off".

Parameter groups
----------------
Gap Detection   atr_length, min_gap_atr_mult
Candle          min_impulse_body_ratio, require_reaction, reaction_min_atr
Retest          entry_depth, max_gap_age_bars
Volatility      vol_atr_length, atr_pct_min, atr_pct_max
Decision        predict_direction  (Continuation | Reversion)
Trend Filter    use_trend_filter, trend_logic, ma_type, ma_length, source

Entry logic
-----------
  1. Gap detection: for closed bars (i-2, i-1, i), a bullish gap forms when
     low[i] > high[i-2] and the gap size (low[i]-high[i-2]) is at least
     `min_gap_atr_mult` x ATR(atr_length); bearish is the mirror. The middle
     ("impulse") candle must also clear `min_impulse_body_ratio`.
  2. Each open gap is tracked until either a retest fires it or
     `max_gap_age_bars` passes with no retest (it's then abandoned).
  3. Retest trigger: price trades back into the gap to at least
     `entry_depth` of its width (bullish: bar low <= that level; bearish:
     bar high >= that level). Optional `require_reaction` demands the same
     bar close back away from that level by >= `reaction_min_atr` x ATR.
  4. Volatility regime: ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max].
  5. Optional trend-filter agreement.

A gap fires at most once (it's removed from tracking the moment it's
retested or expires), so this strategy naturally can't re-signal on the same
imbalance twice.
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy


class FairValueGap(Strategy):
    id = "fair_value_gap"
    name = "Fair Value Gap"
    description = ("Trades the retest of a 3-candle price imbalance (FVG): "
                    "ride the original impulse on the pullback, or fade a "
                    "gap that fails, with volatility and trend filters.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Gap Detection", [
                Param("atr_length", "ATR Length", 14, "int", 2, 200, 1,
                      "Lookback for the ATR used to size gaps and TP/SL."),
                Param("min_gap_atr_mult", "Min Gap Size (xATR)", 0.15, "float", 0.0, 3.0, 0.05,
                      "Minimum gap width, in ATRs, to count as a valid FVG."),
            ]),
            ParamGroup("Candle", [
                Param("min_impulse_body_ratio", "Min Impulse Body Ratio", 0.0, "float", 0.0, 1.0, 0.01,
                      "Minimum |close-open|/(high-low) for the middle (impulse) candle."),
                Param("require_reaction", "Require Reaction", False, "bool",
                      help="Only fire if the retest bar itself closes back away "
                           "from the gap (a rejection), not just touches it."),
                Param("reaction_min_atr", "Reaction Min (xATR)", 0.0, "float", 0.0, 3.0, 0.05,
                      "Minimum rejection size (in ATRs) when Require Reaction is on."),
            ]),
            ParamGroup("Retest", [
                Param("entry_depth", "Entry Depth (0=near edge, 1=far edge)", 0.5, "float", 0.0, 1.0, 0.05,
                      "How far into the gap price must retrace to trigger entry. "
                      "0.5 is the gap's midpoint (the ICT 'CE'/equilibrium level)."),
                Param("max_gap_age_bars", "Max Gap Age (bars)", 48, "int", 1, 500, 1,
                      "Abandon a gap if it hasn't been retested within this many bars."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 20, "int", 2, 200, 1,
                      "Lookback for the regime ATR (as % of price)."),
                Param("atr_pct_min", "ATR% Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR% Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent, trending regime)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Continuation", "enum",
                      options=["Continuation", "Reversion"],
                      help="Continuation rides the original impulse on the retest "
                           "(classic FVG usage). Reversion fades it, betting the "
                           "gap fully fails."),
            ]),
            common.trend_filter_group(),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        atr_len = p["atr_length"]
        min_gap_mult = p["min_gap_atr_mult"]
        min_body = p["min_impulse_body_ratio"]
        require_reaction = p["require_reaction"]
        reaction_min_atr = p["reaction_min_atr"]
        entry_depth = p["entry_depth"]
        max_age = p["max_gap_age_bars"]
        continuation = p["predict_direction"] == "Continuation"
        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        use_trend = p["use_trend_filter"]
        trend_with = p["trend_logic"] == "With Trend"

        n = len(candles)
        atr = ind.atr(candles, atr_len)
        atr_vol = ind.atr(candles, vol_len)
        trend_ma = (common.moving_average(common.source_values(candles, p["source"]),
                                           p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)

        signals: List[Signal] = []
        open_gaps: List[dict] = []   # {lo, hi, side, formed_at, gap_atr}

        for i, c in enumerate(candles):
            a = atr[i]

            # --- 1. check existing open gaps for a retest on this bar -----
            still_open = []
            for g in open_gaps:
                if i - g["formed_at"] > max_age:
                    continue  # stale -> drop, never fired
                lo, hi = g["lo"], g["hi"]
                width = hi - lo
                if g["side"] == "bull":
                    threshold = hi - entry_depth * width
                    touched = c["low"] <= threshold
                    reaction = c["close"] - c["low"]
                else:
                    threshold = lo + entry_depth * width
                    touched = c["high"] >= threshold
                    reaction = c["high"] - c["close"]

                if not touched:
                    still_open.append(g)
                    continue

                # gap consumed (fires at most once) regardless of outcome below
                av = atr_vol[i]
                if av is None or av <= 0 or a is None or a <= 0:
                    continue
                atr_pct = av / c["close"] * 100.0
                if atr_pct < ap_min or atr_pct > ap_max:
                    continue
                if require_reaction and reaction < reaction_min_atr * a:
                    continue

                bull = g["side"] == "bull"
                side = ("long" if bull else "short") if continuation else \
                       ("short" if bull else "long")

                if use_trend:
                    tm = trend_ma[i]
                    if tm is None:
                        continue
                    above = c["close"] > tm
                    agree = (side == "long" and above) or (side == "short" and not above)
                    if agree != trend_with:
                        continue

                mode = "Continuation" if continuation else "Reversion"
                reason = (f"{'Bullish' if bull else 'Bearish'} FVG retest ({mode}) "
                          f"at {entry_depth:.0%} depth, {g['gap_atr']:.2f}xATR gap, "
                          f"age {i - g['formed_at']} bars")
                signals.append(Signal(
                    index=i, time=c["time"], side=side, price=c["close"],
                    reason=reason, atr=a,
                    meta={"gap_side": g["side"], "gap_atr": round(g["gap_atr"], 2),
                          "entry_depth": entry_depth, "age_bars": i - g["formed_at"]},
                ))
                # do not re-add to still_open: this gap is spent

            open_gaps = still_open

            # --- 2. detect a NEW gap formed by bars (i-2, i-1, i) ----------
            if i >= 2 and a is not None and a > 0:
                c0, impulse, c2 = candles[i - 2], candles[i - 1], c
                rng = impulse["high"] - impulse["low"]
                body_ratio = (abs(impulse["close"] - impulse["open"]) / rng) if rng > 0 else 0.0
                if body_ratio >= min_body:
                    bull_gap = c2["low"] - c0["high"]
                    bear_gap = c0["low"] - c2["high"]
                    if bull_gap > 0 and bull_gap / a >= min_gap_mult:
                        open_gaps.append({"lo": c0["high"], "hi": c2["low"],
                                           "side": "bull", "formed_at": i,
                                           "gap_atr": bull_gap / a})
                    elif bear_gap > 0 and bear_gap / a >= min_gap_mult:
                        open_gaps.append({"lo": c2["high"], "hi": c0["low"],
                                           "side": "bear", "formed_at": i,
                                           "gap_atr": bear_gap / a})

        return signals


# ---------------------------------------------------------------------------
# Preset. 480-combination grid search over BTCUSDT 5m, trailing 2 years
# (2024-07-26 -> 2026-07-26), Polymarket up/down mode. Re-optimized from an
# earlier, volume-maximizing version of this same preset (14,883 bets,
# 51.88% hit) for HIGHER HIT RATE at a deliberately lower bet count (target:
# ~1 trade per 5-10 hours, ~1,750-3,500 total over 2yr). Admission: hit rate
# stable across BOTH halves of the window (>=50% each, >=15 bets/half) AND
# bet count inside the target band, then maximize hit rate within it.
#
# Swept: min_gap_atr_mult (widened up to 3.0xATR -- much stricter than the
# earlier version's 0.5) x entry_depth x max_gap_age_bars x
# predict_direction x require_reaction.
#
# Same two structural findings as before, reconfirmed at this stricter
# operating point: Continuation still sweeps every top result (Reversion
# never appears), and require_reaction=False still wins (the confirmation
# filter earns nothing here either).
#
# Result: 2,177 bets (~1 per 8 hours), 52.92% hit (53.3% / 52.5% by half --
# stable) -- a 6.8x cut in volume from the earlier 14,883-bet version for a
# +1.0pp hit-rate gain. The main levers were tightening min_gap_atr_mult
# from 0.5 to 1.25 (only trade genuinely large gaps) and waiting longer for
# a retest (max_gap_age_bars 96 -> 200).
# CAVEAT: at this bet count the improvement is real but modest relative to
# sampling noise (+1.0pp is within a rough +/-2pp 95% CI at n=2,177) --
# treat it as a reasonable, stability-checked pick, not a settled edge.
PRESETS: dict = {
    "PM 5m Volume - 2yr Train": {
        "atr_length": 14,
        "min_gap_atr_mult": 1.25, "min_impulse_body_ratio": 0.0,
        "require_reaction": False, "reaction_min_atr": 0.0,
        "entry_depth": 0.75, "max_gap_age_bars": 200,
        "vol_atr_length": 20, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "predict_direction": "Continuation",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # -------------------------------------------------------------------------
    # 15-MINUTE preset, fitted on the latest six months of BTCUSDT 15m under
    # the protocol the Reversal, Oscillators and Gann 15m presets use:
    # LOADED 2026-03-13 -> 2026-09-13 (train 03-13 -> 07-13 for selection,
    # holdout 07-13 -> 09-13 scored once after the pick was frozen);
    # UNLOADED 2017-08-17 -> 2026-03-13, never read by any sweep stage and
    # scored once at the end as the real out-of-sample check (4,939 bets
    # there against 482 in the window). The selection rule was fixed before
    # tuning: train bets >= 300, both train halves above 52%, every swept
    # parameter off its grid edge, then highest train hit — read against the
    # marginals, since at a few hundred bets a config the SE is 1.5-2.5pp
    # and the single best row is mostly noise.
    #
    # SWEEP. One stage of 960 configs raced Continuation/Reversion x
    # min_gap_atr_mult {0..1.5} x entry_depth {0..1} x max_gap_age_bars
    # {24..200} x reaction filter on/off x trend filter {off, With EMA200};
    # a second pass (~15) tried the impulse body, a non-zero reaction size
    # and the ATR band on the frozen pick.
    #
    # FOUND. Continuation is the family (pooled train 51.71% vs 47.15% for
    # Reversion — a gap retest that holds goes on with the impulse, as on
    # 5m). Within it the gap size is the lever: bigger gaps are better on
    # both windows (0: 51.29% train / 50.59% holdout; 0.75: 53.77 / 55.63;
    # 1.0: 55.36 / 58.18) at a steep cost in bets; the entry depth peaks at
    # the gap's midpoint (0.5: 53.10% train — ICT's 'consequent
    # encroachment' level, and the only depth that beats 52% on train); the
    # gap's age and the impulse body are inert; the trend filter loses
    # 1.5pp. The reaction filter is a no-op at reaction_min_atr = 0 (its
    # rows are byte-identical to 'off') and at 0.25 ATR it destroys the
    # unloaded years (52.58%, 2026 at 40.6%), which is the 5m verdict on
    # confirmation filters again. The preset takes the rule's row: a gap of
    # at least 0.75 ATR, entered at its midpoint within 48 bars (12 h).
    #
    # RESULTS — flat $1 per bet, next-candle direction
    #   preset             6m bets  6m hit   train   HOLDOUT   unloaded 8.5y             worst yr
    #   PM 15m Balanced       482  57.05%  56.48%   58.01%    54.16% (4,939, z +5.8)  50.76% (2022)
    #
    # Per year on the full record, none of it fitted except the last six months:
    #   2017  57.63% (177)       2018  53.17% (489)       2019  59.43% (387)       2020  55.30% (472)
    #   2021  53.12% (544)       2022  50.76% (593)       2023  56.85% (635)       2024  52.84% (687)
    #   2025  53.94% (799)       2026  55.17% (638)
    #
    # Train halves 57.89% / 55.36%. About 2.6 bets a day. The 18 months
    # right before the window (2024-09 -> 2026-03) score 53.04% on 1,186
    # bets; whole record 5,421 bets, 54.42%, z +6.5. Read the hit rates
    # against 49.9%: 0.13% of 15m candles close exactly at their open.
    # Checks after the pick was frozen: the prefix (no look-ahead) test
    # passes with 0 mismatches at three cut points; bets run 54% long in the
    # window and both sides win (window long 60.23% / short 53.36%; unloaded
    # 53.88% / 54.46%); the mirror on the same settings scores 42.95% in the
    # window and 45.76% unloaded.
    #
    # WHERE IT FAILS. The worst month in the window is 2026-05 at 41.9% on
    # 74 bets; the worst full year 2022 at 50.76%. THE WEAKEST 15m PRESET IN
    # THE REPO out of sample: 54.16% on 4,939 unloaded bets (z +5.8), with
    # 2022 at 50.8% and 2021, 2024 and 2025 at 52-54%, against 57% in a
    # fitted window that itself had a 41.9% month (2026-05). The window's
    # 57% is the regime; ~2.7 bets a day. Read it as the 5m note reads its
    # own preset — a stability-checked pick, not a settled edge. The
    # 0.50-odds EV the dashboard prints assumes a fill at even; a real 15m
    # book prices away from it. Hit rate is the finding.
    #
    # NOT SHIPPED. gap 0.5 (the volume dial): 952 window bets at 55.67%,
    # 53.79% unloaded. gap 1.0: 249 at 57.03%, 54.29% unloaded. The 5m
    # preset carried over as-is (gap 1.25, depth 0.75, age 200): 155 window
    # bets at 55.48%, 54.02% unloaded with 2021 at 48.4%. Nothing in this
    # family clears 55% on the unloaded years.
    # 482 bets, 57.05% hit on 2026-03..09; unloaded 2017-08..2026-03 only
    # 54.16% on 4,939 bets (z +5.8) — the weakest 15m preset here; see above.
    "PM 15m Balanced": {
        "atr_length": 14,
        "min_gap_atr_mult": 0.75, "min_impulse_body_ratio": 0.0,
        "require_reaction": False, "reaction_min_atr": 0.0,
        "entry_depth": 0.5, "max_gap_age_bars": 48,
        "vol_atr_length": 20, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "predict_direction": "Continuation",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
}
