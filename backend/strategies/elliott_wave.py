"""Elliott Wave — trade the next leg of a mechanically-counted impulse.

Idea
----
Elliott Wave theory says a trend unfolds in a five-leg **impulse** (1-2-3-4-5)
in the direction of the larger trend, followed by a three-leg **correction**
(A-B-C) against it. Waves 1, 3 and 5 push; waves 2 and 4 give some of the push
back. Three rules are treated as inviolable — break one and the count is wrong:

    R1  Wave 2 never retraces more than 100% of wave 1.
    R2  Wave 3 is never the shortest of waves 1, 3 and 5.
    R3  Wave 4 never overlaps wave 1's price territory.

The tradeable claim is that once you can see waves 1 and 2, wave 3 is next (and
wave 3 is usually the longest); once you can see 1-2-3-4, wave 5 is next; and
once a five is complete, a correction is next. This strategy takes each of those
three claims and turns it into an entry.

Two honest notes, because they shaped the parameterisation
----------------------------------------------------------
* **Wave counting is normally discretionary**, which is exactly what makes the
  theory hard to falsify: a count that fails gets relabelled rather than marked
  wrong. Nothing here is discretionary. Swings come from an ATR-thresholded
  **zigzag** that only ever looks backwards (below), the count is read off the
  last few confirmed pivots, and a setup that does not match is simply skipped.
* **The three rules are a switch, not a law.** `enforce_impulse_rules` turns R1,
  R2 and R3 on and off together, so a sweep can ask the only question that
  matters — does an "Elliott-valid" count predict better than the same swing
  structure without the rules? (Answer at the bottom of this file. It is not
  the answer the theory wants.)

Finding swings without looking ahead
------------------------------------
A pivot is a leg extreme, and you only know it was an extreme once price has
turned away from it. That turn is the confirmation, so every pivot carries two
bar numbers: where it **happened** (`i`) and where it became **knowable** (`c`).
Signals may only use pivots whose `c` is at or before the current bar, which is
what keeps the count causal — an in-progress leg's extreme is never treated as a
pivot until the reversal that proves it has already printed.

The turn threshold is `pivot_atr_mult` x ATR **measured at the extreme bar**, so
the yardstick is the volatility that produced the swing and does not drift while
the market waits to reverse. A pivot also needs `min_pivot_bars` between the
extreme and its confirmation, which throws away one-bar spikes.

Parameter groups
----------------
Wave Detection  atr_length, pivot_atr_mult, min_pivot_bars, min_wave1_atr
Wave Rules      enforce_impulse_rules, wave2_min/max_retrace, wave4_min/max_retrace
Setup           trade_setup, entry_mode, max_setup_age_bars
Entry Timing    require_opposing_bar, opposing_bar_min_atr
Volatility      vol_atr_length, atr_pct_min, atr_pct_max
Decision        predict_direction  (Follow Count | Fade Count)
Trend Filter    use_trend_filter, trend_logic, ma_type, ma_length, source

The three setups (described for a BULLISH count; bearish is the mirror)
------------------------------------------------------------------------
**Wave 3** — the last three confirmed pivots read low -> high -> low. The first
two are wave 1; the drop into the third is wave 2. Forecast: LONG.

**Wave 5** — the last five read low -> high -> low -> high -> low: waves 1-4 are
complete and wave 4 has just bottomed. Forecast: LONG.

**Post-Impulse Reversal** — the last six complete a whole five-wave advance and
the wave-5 high is confirmed. Forecast: SHORT (wave A). This setup always waits
for pivot confirmation; `entry_mode` does not apply to it, since there is no
retracement to anticipate — the reversal *is* the trade.

Entry mode
----------
**Pivot Confirm** fires on the bar where the pivot that completes the setup is
confirmed. Unambiguous, and always late by the confirmation lag.

**Retrace Zone** fires earlier: with the setup's prior pivots confirmed and the
correction still in progress, it measures the retracement from the last pivot to
the current close and fires the first time it lands inside the zone
(`wave2_min/max_retrace` for a wave-3 setup, `wave4_min/max_retrace` for wave-5).
One signal per structure — a pullback that wobbles in and out of the zone still
bets once.

Both modes then apply, in order: leg size (`min_wave1_atr` x ATR), the
retracement zone, the rules if enabled, setup age (bars since the anchor pivot
was confirmed), the volatility regime (ATR% inside [atr_pct_min, atr_pct_max]),
the optional opposing-bar timing filter, and the optional trend filter.
`predict_direction` then either follows the count's forecast or fades it.
"""

from __future__ import annotations

from typing import List, Optional

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

SETUP_W3 = "Wave 3"
SETUP_W5 = "Wave 5"
SETUP_BOTH = "Wave 3 + 5"
SETUP_POST = "Post-Impulse Reversal"

MODE_CONFIRM = "Pivot Confirm"
MODE_ZONE = "Retrace Zone"


def zigzag(candles: List[dict], atr: List, mult: float, min_bars: int):
    """Causal ATR-thresholded zigzag -> (pivots, n_confirmed, leg_dir, leg_ext).

    ``pivots`` is a list of alternating swing points in confirmation order::

        {"i": extreme bar, "p": extreme price, "kind": "high"|"low",
         "c": bar the reversal confirmed it}

    ``n_confirmed[i]`` is how many pivots are knowable at bar ``i`` — always
    slice ``pivots[:n_confirmed[i]]`` rather than the whole list, or the count
    peeks at swings the market had not yet made.

    ``leg_dir[i]`` is +1 while the current (unconfirmed) leg is searching for a
    high, -1 while it is searching for a low, 0 before the first pivot;
    ``leg_ext[i]`` is that leg's running extreme price. Together they describe
    the correction that is still in progress, which is what Retrace Zone entries
    measure against.

    A pivot confirms when price trades ``mult`` x ATR(at the extreme bar) back
    from the extreme, at least ``min_bars`` bars after it. Two extremes are
    tracked at once — the leg's own (``ext``) and the counter-move away from it
    (``opp``) — so the pivot recorded is always the exact extreme of the bars it
    spans, and the counter-move's extreme becomes the next leg's candidate with
    nothing lost in between. ``opp`` is measured strictly AFTER the pivot bar,
    which is what keeps pivot indices strictly increasing: one bar cannot be
    both a swing high and the swing low that ends it.
    """
    n = len(candles)
    pivots: List[dict] = []
    n_conf = [0] * n
    leg_dir = [0] * n
    leg_ext: List[Optional[float]] = [None] * n

    d = 0                                     # 0 = no pivot yet, +1 = seeking a high
    ext_i, ext_p = -1, 0.0                    # the leg's running extreme (candidate pivot)
    opp_i, opp_p = -1, None                   # counter-move extreme since ext_i
    hi_i = lo_i = -1
    hi_p = lo_p = 0.0

    def rescan(frm: int, to: int, want_high: bool):
        """Extreme of candles(frm, to], as the seed for the next leg."""
        bi, bp = -1, None
        for j in range(frm + 1, to + 1):
            v = candles[j]["high"] if want_high else candles[j]["low"]
            if bp is None or (v >= bp if want_high else v <= bp):
                bi, bp = j, v
        return bi, bp

    for i, c in enumerate(candles):
        a = atr[i]
        if a is None or a <= 0:
            n_conf[i] = len(pivots)
            continue
        h, l = c["high"], c["low"]

        if d == 0:
            # Before the first pivot, track BOTH extremes and let whichever one
            # gets reversed first become the seed pivot.
            if hi_i < 0 or h >= hi_p:
                hi_i, hi_p = i, h
            if lo_i < 0 or l <= lo_p:
                lo_i, lo_p = i, l
            down_ok = (hi_p - l >= mult * atr[hi_i]) and (i - hi_i >= min_bars)
            up_ok = (h - lo_p >= mult * atr[lo_i]) and (i - lo_i >= min_bars)
            if down_ok and up_ok:             # tie -> the older extreme is the one reversed
                if hi_i <= lo_i:
                    up_ok = False
                else:
                    down_ok = False
            if down_ok:
                pivots.append({"i": hi_i, "p": hi_p, "kind": "high", "c": i})
                d = -1
                ext_i, ext_p = rescan(hi_i, i, want_high=False)
                opp_i, opp_p = rescan(ext_i, i, want_high=True)
            elif up_ok:
                pivots.append({"i": lo_i, "p": lo_p, "kind": "low", "c": i})
                d = 1
                ext_i, ext_p = rescan(lo_i, i, want_high=True)
                opp_i, opp_p = rescan(ext_i, i, want_high=False)
        elif d == 1:                          # seeking a swing HIGH
            if h >= ext_p:
                ext_i, ext_p = i, h
                opp_i, opp_p = -1, None       # no bar lies after the new extreme yet
            elif opp_p is None or l <= opp_p:
                opp_i, opp_p = i, l
            if opp_p is not None and ext_p - opp_p >= mult * atr[ext_i] \
                    and i - ext_i >= min_bars:
                pivots.append({"i": ext_i, "p": ext_p, "kind": "high", "c": i})
                d = -1
                ext_i, ext_p = opp_i, opp_p
                opp_i, opp_p = rescan(ext_i, i, want_high=True)
        else:                                 # seeking a swing LOW
            if l <= ext_p:
                ext_i, ext_p = i, l
                opp_i, opp_p = -1, None
            elif opp_p is None or h >= opp_p:
                opp_i, opp_p = i, h
            if opp_p is not None and opp_p - ext_p >= mult * atr[ext_i] \
                    and i - ext_i >= min_bars:
                pivots.append({"i": ext_i, "p": ext_p, "kind": "low", "c": i})
                d = 1
                ext_i, ext_p = opp_i, opp_p
                opp_i, opp_p = rescan(ext_i, i, want_high=False)

        n_conf[i] = len(pivots)
        leg_dir[i] = d
        leg_ext[i] = ext_p if d != 0 else None
    return pivots, n_conf, leg_dir, leg_ext


class ElliottWave(Strategy):
    id = "elliott_wave"
    name = "Elliott Wave"
    description = ("Counts impulse waves mechanically from an ATR zigzag and bets "
                   "the next leg — wave 3 after a wave-2 pullback, wave 5 after "
                   "wave 4, or the correction after a completed five — with "
                   "Elliott's three rules as a switch you can measure.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Wave Detection", [
                Param("atr_length", "ATR Length", 14, "int", 2, 200, 1,
                      "Lookback for the ATR that sets the zigzag threshold, measures "
                      "wave sizes, and sizes TP/SL."),
                Param("pivot_atr_mult", "Pivot Threshold (xATR)", 1.5, "float", 0.1, 20.0, 0.1,
                      "A swing pivot confirms once price reverses this many ATRs from "
                      "the leg's extreme. Larger = fewer, bigger waves."),
                Param("min_pivot_bars", "Min Bars To Confirm", 2, "int", 1, 200, 1,
                      "Minimum bars between a leg extreme and its confirmation, so a "
                      "single spike bar cannot become a wave."),
                Param("min_wave1_atr", "Min Wave 1 Size (xATR)", 2.0, "float", 0.0, 50.0, 0.5,
                      "Wave 1 must be at least this many ATRs tall, so a flat stretch "
                      "of tape is not counted as an impulse."),
            ]),
            ParamGroup("Wave Rules", [
                Param("enforce_impulse_rules", "Enforce Elliott's Rules", True, "bool",
                      help="R1 wave 2 retraces < 100% of wave 1; R2 wave 3 is not the "
                           "shortest impulse leg; R3 wave 4 does not overlap wave 1. "
                           "Off = the same swing structure with no rules — the control "
                           "condition for whether the theory adds anything."),
                Param("wave2_min_retrace", "Wave 2 Min Retrace", 0.236, "float", 0.0, 3.0, 0.01,
                      "Wave 2 must give back at least this fraction of wave 1."),
                Param("wave2_max_retrace", "Wave 2 Max Retrace", 1.0, "float", 0.05, 3.0, 0.01,
                      "…and at most this fraction. Values above 1.0 only bite with the "
                      "rules switched off (R1 caps it at 1.0)."),
                Param("wave4_min_retrace", "Wave 4 Min Retrace", 0.146, "float", 0.0, 3.0, 0.01,
                      "Wave 4 must give back at least this fraction of wave 3."),
                Param("wave4_max_retrace", "Wave 4 Max Retrace", 0.618, "float", 0.05, 3.0, 0.01,
                      "…and at most this fraction. The textbook wave-4 zone is "
                      "0.236-0.382; 0.618 is deliberately loose."),
            ]),
            ParamGroup("Setup", [
                Param("trade_setup", "Setup", SETUP_W3, "enum",
                      options=[SETUP_W3, SETUP_W5, SETUP_BOTH, SETUP_POST],
                      help="Which forecast to trade. 'Wave 3 + 5' takes both and "
                           "prefers the wave-5 reading when a bar satisfies each."),
                Param("entry_mode", "Entry Mode", MODE_CONFIRM, "enum",
                      options=[MODE_CONFIRM, MODE_ZONE],
                      help="Pivot Confirm waits for the correction's low/high to be "
                           "confirmed by the zigzag. Retrace Zone enters earlier, while "
                           "the correction is still running, once it reaches the zone. "
                           "Ignored by Post-Impulse Reversal."),
                Param("max_setup_age_bars", "Max Setup Age (bars)", 96, "int", 1, 2000, 1,
                      "Drop a setup if this many bars have passed since its anchor pivot "
                      "was confirmed (wave 1's end for a wave-3 setup, wave 3's end for "
                      "a wave-5 one)."),
            ]),
            ParamGroup("Entry Timing", [
                Param("require_opposing_bar", "Signal Bar Opposes Bet", False, "bool",
                      help="Only take the bet when the signal bar itself closed against "
                           "it — i.e. the correction is still under way. Skips entries "
                           "where the turn already happened."),
                Param("opposing_bar_min_atr", "Min Opposing Body (xATR)", 0.0, "float",
                      0.0, 3.0, 0.05,
                      "Also require that opposing bar's body to be at least this multiple "
                      "of ATR. 0 accepts any opposing bar. Needs the box above."),
            ]),
            ParamGroup("Volatility Filter", [
                Param("vol_atr_length", "Vol ATR Length", 20, "int", 2, 200, 1,
                      "Lookback for the regime ATR (as % of price)."),
                Param("atr_pct_min", "ATR% Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR% Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent regime)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Follow Count", "enum",
                      options=["Follow Count", "Fade Count"],
                      help="Follow Count trades the wave the count forecasts (the "
                           "textbook use). Fade Count bets the count fails."),
            ]),
            common.trend_filter_group(),
        ]

    def presets(self) -> dict:
        return PRESETS

    # -- setup readers --------------------------------------------------------
    # Each returns (forecast_side, meta) or None. `pv` is already sliced to the
    # pivots knowable at bar `i`.

    def _wave3(self, pv, k, i, atr, p, anchor_only=False, close=None, leg_dir=0):
        """Waves 1-2 complete -> forecast wave 3.

        `pv[:k]` are the pivots knowable at bar `i` (passed as a list plus a
        count rather than a slice — this runs once per bar over ~1M bars).

        `anchor_only` switches to Retrace Zone reading: wave 2's low is not yet a
        pivot, so the retracement is measured to `close` and the leg must still
        be running in the corrective direction.
        """
        need = 2 if anchor_only else 3
        if k < need:
            return None
        if anchor_only:
            a_pv, b_pv = pv[k - 2], pv[k - 1]
        else:
            a_pv, b_pv, c_pv = pv[k - 3], pv[k - 2], pv[k - 1]

        if a_pv["kind"] == "low" and b_pv["kind"] == "high":
            bull = True
            w1 = b_pv["p"] - a_pv["p"]
            if anchor_only:
                if leg_dir != -1:
                    return None
                low_p = close
            else:
                if c_pv["kind"] != "low":
                    return None
                low_p = c_pv["p"]
            if w1 <= 0:
                return None
            retr = (b_pv["p"] - low_p) / w1
        elif a_pv["kind"] == "high" and b_pv["kind"] == "low":
            bull = False
            w1 = a_pv["p"] - b_pv["p"]
            if anchor_only:
                if leg_dir != 1:
                    return None
                high_p = close
            else:
                if c_pv["kind"] != "high":
                    return None
                high_p = c_pv["p"]
            if w1 <= 0:
                return None
            retr = (high_p - b_pv["p"]) / w1
        else:
            return None

        aw = atr[b_pv["i"]]
        if aw is None or aw <= 0 or w1 < p["min_wave1_atr"] * aw:
            return None
        if not (p["wave2_min_retrace"] <= retr <= p["wave2_max_retrace"]):
            return None
        if p["enforce_impulse_rules"] and retr >= 1.0:   # R1
            return None
        if i - b_pv["c"] > p["max_setup_age_bars"]:
            return None
        return ("long" if bull else "short",
                {"setup": "wave3", "wave2_retrace": round(retr, 4),
                 "wave1_atr": round(w1 / aw, 2), "anchor_bar": b_pv["i"]})

    def _wave5(self, pv, k, i, atr, p, anchor_only=False, close=None, leg_dir=0):
        """Waves 1-4 complete -> forecast wave 5."""
        need = 4 if anchor_only else 5
        if k < need:
            return None
        seq = pv[k - need:k]
        kinds = [x["kind"] for x in seq]
        if kinds[:4] == ["low", "high", "low", "high"]:
            bull = True
        elif kinds[:4] == ["high", "low", "high", "low"]:
            bull = False
        else:
            return None
        p0, p1, p2, p3 = (x["p"] for x in seq[:4])
        if anchor_only:
            if leg_dir != (-1 if bull else 1):
                return None
            p4 = close
        else:
            if kinds[4] != ("low" if bull else "high"):
                return None
            p4 = seq[4]["p"]

        if bull:
            w1, w3, w4 = p1 - p0, p3 - p2, p3 - p4
            rules_ok = p2 > p0 and p3 > p1 and p4 > p1 and w3 >= w1
        else:
            w1, w3, w4 = p0 - p1, p2 - p3, p4 - p3
            rules_ok = p2 < p0 and p3 < p1 and p4 < p1 and w3 >= w1
        if w1 <= 0 or w3 <= 0:
            return None
        if p["enforce_impulse_rules"] and not rules_ok:   # R1 + R2(partial) + R3
            return None
        r4 = w4 / w3
        if not (p["wave4_min_retrace"] <= r4 <= p["wave4_max_retrace"]):
            return None
        aw = atr[seq[1]["i"]]
        if aw is None or aw <= 0 or w1 < p["min_wave1_atr"] * aw:
            return None
        if i - seq[3]["c"] > p["max_setup_age_bars"]:
            return None
        return ("long" if bull else "short",
                {"setup": "wave5", "wave4_retrace": round(r4, 4),
                 "wave1_atr": round(w1 / aw, 2), "wave3_atr": round(w3 / aw, 2),
                 "anchor_bar": seq[3]["i"]})

    def _post_impulse(self, pv, k, i, atr, p):
        """A whole five-wave advance is complete -> forecast the correction."""
        if k < 6:
            return None
        seq = pv[k - 6:k]
        kinds = [x["kind"] for x in seq]
        if kinds == ["low", "high", "low", "high", "low", "high"]:
            bull = True                      # five UP -> correction is DOWN
        elif kinds == ["high", "low", "high", "low", "high", "low"]:
            bull = False
        else:
            return None
        p0, p1, p2, p3, p4, p5 = (x["p"] for x in seq)
        if bull:
            w1, w3, w5, w2, w4 = p1 - p0, p3 - p2, p5 - p4, p1 - p2, p3 - p4
            rules_ok = p2 > p0 and p3 > p1 and p4 > p1 and p5 > p3 \
                and w3 >= w1 and w3 >= w5
        else:
            w1, w3, w5, w2, w4 = p0 - p1, p2 - p3, p4 - p5, p2 - p1, p4 - p3
            rules_ok = p2 < p0 and p3 < p1 and p4 < p1 and p5 < p3 \
                and w3 >= w1 and w3 >= w5
        if w1 <= 0 or w3 <= 0 or w5 <= 0:
            return None
        if p["enforce_impulse_rules"] and not rules_ok:
            return None
        r2, r4 = w2 / w1, w4 / w3
        if not (p["wave2_min_retrace"] <= r2 <= p["wave2_max_retrace"]):
            return None
        if not (p["wave4_min_retrace"] <= r4 <= p["wave4_max_retrace"]):
            return None
        aw = atr[seq[1]["i"]]
        if aw is None or aw <= 0 or w1 < p["min_wave1_atr"] * aw:
            return None
        if i - seq[5]["c"] > p["max_setup_age_bars"]:
            return None
        # The impulse ran UP, so the forecast leg (wave A) runs DOWN.
        return ("short" if bull else "long",
                {"setup": "post5", "wave2_retrace": round(r2, 4),
                 "wave4_retrace": round(r4, 4), "wave1_atr": round(w1 / aw, 2),
                 "wave3_atr": round(w3 / aw, 2), "wave5_atr": round(w5 / aw, 2),
                 "anchor_bar": seq[5]["i"]})

    # -- signal generation ----------------------------------------------------

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        atr = ind.atr(candles, p["atr_length"])
        atr_vol = ind.atr(candles, p["vol_atr_length"])
        pivots, n_conf, leg_dir, _leg_ext = zigzag(
            candles, atr, p["pivot_atr_mult"], p["min_pivot_bars"])
        if not pivots:
            return []

        use_trend = p["use_trend_filter"]
        trend_ma = (ind.ma(ind.price_source(candles, p["source"]),
                           p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)

        setup = p["trade_setup"]
        zone_mode = p["entry_mode"] == MODE_ZONE and setup != SETUP_POST
        want5 = setup in (SETUP_W5, SETUP_BOTH)
        want3 = setup in (SETUP_W3, SETUP_BOTH)
        follow = p["predict_direction"] == "Follow Count"
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        with_trend = p["trend_logic"] == "With Trend"
        opposing_bar = p["require_opposing_bar"]
        opposing_min = p["opposing_bar_min_atr"]

        fired = set()          # Retrace Zone: (setup, anchor bar) already bet once
        signals: List[Signal] = []

        for i, c in enumerate(candles):
            k = n_conf[i]
            if k < 2:
                continue
            if not zone_mode and pivots[k - 1]["c"] != i:
                continue           # Pivot Confirm fires only on a confirmation bar
            a, av = atr[i], atr_vol[i]
            if a is None or a <= 0 or av is None or av <= 0:
                continue

            cl = c["close"]
            found = None
            if setup == SETUP_POST:
                found = self._post_impulse(pivots, k, i, atr, p)
            elif zone_mode:
                # Wave 5 first: a wave-5 structure also reads as a wave-3 one a
                # degree down, and the more specific count is the better label.
                if want5:
                    found = self._wave5(pivots, k, i, atr, p, anchor_only=True,
                                        close=cl, leg_dir=leg_dir[i])
                if found is None and want3:
                    found = self._wave3(pivots, k, i, atr, p, anchor_only=True,
                                        close=cl, leg_dir=leg_dir[i])
            else:
                if want5:
                    found = self._wave5(pivots, k, i, atr, p)
                if found is None and want3:
                    found = self._wave3(pivots, k, i, atr, p)
            if found is None:
                continue
            forecast, meta = found

            if zone_mode:
                # One bet per structure, not one per bar spent inside the zone.
                # Marked BEFORE the filters below on purpose: the tradeable event
                # is the first touch, so a first touch that fails the volatility
                # or timing filter is skipped, not deferred to the next bar. Same
                # convention as `require_fresh_touch` in fib_retracement.py.
                key = (meta["setup"], meta["anchor_bar"])
                if key in fired:
                    continue
                fired.add(key)

            atr_pct = av / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            side = forecast if follow else ("short" if forecast == "long" else "long")

            body = cl - c["open"]
            if opposing_bar:
                if side == "long" and body >= 0:
                    continue
                if side == "short" and body <= 0:
                    continue
                if opposing_min > 0 and abs(body) < opposing_min * a:
                    continue

            if use_trend:
                tm = trend_ma[i]
                if tm is None:
                    continue
                agree = (side == "long") == (cl > tm)
                if agree != with_trend:
                    continue

            # The retracement that gated this setup: wave 2 for a wave-3 count
            # (and for post-impulse, which reports both), wave 4 for a wave-5 one.
            label = {"wave3": "wave 3", "wave5": "wave 5",
                     "post5": "correction after 5"}[meta["setup"]]
            retr = meta["wave4_retrace"] if meta["setup"] == "wave5" \
                else meta["wave2_retrace"]
            reason = (f"Count forecasts {label} "
                      f"{'UP' if forecast == 'long' else 'DOWN'} "
                      f"(retrace {retr * 100:.1f}%, wave1 {meta['wave1_atr']:.1f}xATR) "
                      f"-> {'follow' if follow else 'fade'} {side.upper()} "
                      f"(ATR% {atr_pct:.2f})")
            meta = {**meta, "forecast": forecast, "atr_pct": round(atr_pct, 3),
                    "mode": "follow" if follow else "fade",
                    "bar_body_atr": round(body / a, 2),
                    "pivots": k}
            signals.append(Signal(index=i, time=c["time"], side=side, price=cl,
                                  reason=reason, atr=a, meta=meta))
        return signals


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m). Exit / Backtest params are unused in that mode.
#
# Sweep: BTCUSDT 1m resampled to 5m over the entire DB — 939,433 bars,
# 2017-08-17 .. 2026-07-28 — in three stages totalling 8,340 combinations
# (420 structural + 4,320 geometric + 3,600 filter). Every combination was
# scored by calling this module's own `generate_signals`, so a preset cannot
# drift from what the dashboard runs.
#
# Parameters were selected on **2017-2023 only**; 2024-2026 was scored
# afterwards and never consulted while choosing anything. Admission: binomial
# z >= 2.5 on train, both halves of train (2017-2020, 2021-2023) independently
# >= 52%, and every TRAIN calendar year carrying >= 25 bets above 50%. Of 7,836
# scored configurations 1,558 were admissible; each preset below is the highest
# TRAIN hit rate inside its bet-count band.
#
# Measured results (whole DB, flat $1 per bet)
# --------------------------------------------
#   preset      bets     hit   train 17-23   TEST 24-26  2025-26  worst yr    z
#   Volume     8,182  57.14%      59.00%       53.43%     52.91%   52.63%   13.3
#   Balanced   5,285  58.81%      60.59%       55.39%     54.48%   52.38%   12.5
#   Selective  1,488  60.95%      62.34%       58.04%     56.19%   53.07%    7.8
#   Hi Hit       384  67.45%      67.66%       67.11%     64.89%   58.06%    5.4
#
# **Balanced is the preset to use**, on volume-vs-margin grounds: 5,285 bets is
# ~1 per 15 hours, it keeps 55.39% across 1,809 out-of-sample bets, and no
# calendar year falls below 52.4%. Selective is the better *rate* out of sample
# (58.04%) on a third of the volume, and Hi Hit is the outlier discussed below.
#
# ELLIOTT'S THREE RULES EARN NOTHING — EXCEPT ON A COMPLETE COUNT
# ---------------------------------------------------------------
# `enforce_impulse_rules` was swept as an explicit A/B: 2,016 pairs of configs
# identical in every other parameter, one with R1/R2/R3 on and one with them
# off. Pooled over the training span:
#
#   setup / entry mode          rules ON            rules OFF          delta
#   Wave 3   Pivot Confirm   1,448,881  54.75%   1,771,303  54.77%    -0.02pp
#   Wave 3   Retrace Zone    2,545,238  54.96%   2,567,167  55.10%    -0.15pp
#   Wave 3+5 Pivot Confirm   4,443,684  54.81%   6,561,539  54.78%    +0.03pp
#   Wave 3+5 Retrace Zone    8,390,683  54.63%  11,681,785  54.17%    +0.46pp
#   Wave 5   Retrace Zone      356,306  53.42%   2,580,450  53.75%    -0.33pp
#   Wave 5   Pivot Confirm     126,160  57.18%   1,309,607  54.65%    +2.53pp
#
# Five of six cells are indistinguishable from zero. The rules pay in exactly
# one place: a **complete, confirmed 1-2-3-4 count** (+2.53pp), where they cost
# 90% of the volume to get it. That is the only cell in which R2 (wave 3 is not
# the shortest) and R3 (wave 4 does not overlap wave 1) can even be evaluated —
# both need the whole structure. R1 on its own, which is all a wave-3 setup can
# test, is worth nothing: -0.02pp. So the theory's constraints are not a
# general-purpose filter; one specific conjunction of them is a rare-setup
# detector. Hi Hit is that detector, and it is the only preset with the rules on.
#
# THE FIBONACCI-SHAPED RETRACEMENT ZONES EARN NOTHING EITHER
# ----------------------------------------------------------
# Same story as Fib Retracement, one strategy over. Pooled over train, the
# wave-4 zone (whose textbook value is 0.236-0.382) does best when it is simply
# switched OFF: wide (0.0-3.0) 54.80%, the loose textbook band (0.236-0.786)
# 54.44%, the tight one (0.146-0.618) 54.15%. What DOES pay is depth on wave 2:
# 0.5-1.0 gives 54.82%, 0.236-1.0 gives 54.68%, wide gives 54.37% — the
# monotone-in-depth result the fib sweep found, again, with no bump at any
# canonical ratio. Every preset here sits at 0.618-1.2 on wave 2 for that
# reason and not for a numerological one.
#
# Other findings
# --------------
#   * **Mean reversion, in both entry modes.** Pooled over stage 1: Pivot
#     Confirm + Fade Count 51.73% vs Follow 47.78%; Retrace Zone + Follow Count
#     51.13% vs Fade 48.39%. Those two winners have OPPOSITE `predict_direction`
#     settings and are the same trade: Retrace Zone + Follow buys while the
#     wave-2 pullback is still falling, and Pivot Confirm + Fade sells after the
#     zigzag has confirmed the bounce off that low. Both fade the most recent
#     move — the same place every other strategy in this repo has landed.
#   * **`require_opposing_bar` is again the single most valuable filter**, as in
#     Multi Horizon and Fib Retracement — the third independent confirmation.
#     Lane by lane: 58.55% vs 57.76%, 57.43% vs 56.45%, 61.99% vs 59.68%, and
#     the minimum body helps monotonically (0.0 / 0.25 / 0.5 / 0.75 xATR give
#     58.06 / 58.51 / 58.66 / 58.73% in the volume lane).
#   * **Bigger swings, better bets.** Pooled over stage 2, `pivot_atr_mult`
#     2.5 / 4.0 / 6.0 / 9.0 gives 53.83 / 55.58 / 55.97 / 55.49% — rising to a
#     plateau around 4-6 ATRs and turning over past it.
#   * **The trend filter barely matters here** (unlike Fib Retracement, where it
#     was worth ~0.7pp). Against Trend 58.40% vs filter-off 58.32% in the volume
#     lane; three of the four presets use it anyway because it won on train, but
#     do not expect much from it.
#
# Caveats
# -------
# 1. **Do not push this for volume.** The widest admissible net — 28,618 bets,
#    train 54.58% — collapses to 50.74% on the holdout and 49.76% over 2025-26,
#    i.e. to nothing. Elliott's edge here lives entirely in selectivity, which
#    is why no preset above runs wider than ~8,000 bets.
# 2. **Shrinkage, as everywhere in this repo.** Volume and Balanced lose 5.6 and
#    5.2 points from train to holdout. Treat in-sample numbers as upper bounds.
# 3. **Hi Hit is the exception that should still be treated carefully.** It is
#    the only tier that does NOT shrink (67.66% train, 67.11% holdout) and it
#    clears every year, but it fires ~43 times a year, so its 149 holdout bets
#    carry a +/-7.6pp 95% interval. It is genuinely out-of-sample evidence and
#    it is genuinely thin. It also cannot be run alone as a strategy — at that
#    rate you would place under one bet a week.
# 4. Days are UTC and a bar is stamped by its open time.
#
# A bet pays only when hit rate > your odds: Balanced's 55.39% needs entry below
# ~0.5539 to be +EV.
#
# ---------------------------------------------------------------------------
# THE "- 2yr Train" PRESETS: refitted on the trailing two years alone
# ---------------------------------------------------------------------------
# The four presets above were fitted on 2017-2023 and they have decayed: over
# the trailing 2 years (2024-07-28 .. 2026-07-28, 210,528 bars) *PM 5m Volume*
# runs at 52.55% against the 59.00% it showed on its own training span. So the
# same three sweep stages were re-run over that window alone.
#
# There is NO holdout here — the window IS the fit — so admission could only use
# in-window stability: z >= 2.5, both one-year halves independently >= 52%, and
# >= 60 bets per half. 2,983 of 7,777 configurations passed; each preset is the
# highest hit rate in its bet-count band (bands scaled by 210,528/939,433).
#
# What makes these trustworthy is not the in-window number, which is fitted, but
# the 2017-2023 column — years these configurations never saw:
#
#   preset                      last 2yr (fitted)      2017-2023 (unseen)   halves
#   Volume - 2yr Train          1,898  56.38%          4,771  56.89%     56.19/56.56
#   Balanced - 2yr Train          803  58.53%          2,020  57.38%     55.37/61.98
#
# Both hold their rate on the years they were not fitted to, which is what
# separates a durable setting that happens to be current from a regime call. And
# on the trailing 2 years the refit beats the full-record fit at matched volume:
# 1,898 bets at 56.38% against 2,135 at 52.55%, a gain of +3.8pp.
#
# Run over the WHOLE record, *Volume - 2yr Train* is also the flattest preset in
# this file year to year — every year from 2018 on lands between 54.4% and 58.6%:
#
#   2017   2018   2019   2020   2021   2022   2023   2024   2025   2026
#   46.9%  54.4%  58.6%  57.1%  57.8%  58.2%  58.0%  57.9%  55.3%  58.1%
#   (226)  (719)  (696)  (699)  (822)  (763)  (846)  (902)  (961)  (534)
#
# The only year below 50% is the partial 2017 on 226 bets. Compare the
# full-record *PM 5m Volume*, which runs 57-61% through 2018-2023 and then 54.3
# / 52.6 / 53.4% in 2024-26. Fitting on the recent window did not chase the
# recent regime; it found a setting that was always there.
#
# The refit also relocated the strategy. Both 2yr presets are **Wave 5 +
# Pivot Confirm + Fade Count** — the structure the full-record fit used only for
# its thinnest tier — and *Balanced - 2yr Train* is the second preset in this
# file to turn Elliott's rules ON. Recent tape rewards waiting for a complete
# 1-2-3-4 count and fading its wave-4 confirmation. Neither uses the
# opposing-bar, volatility or trend filters: over 2 years none of them earned
# their place on train, which is itself a warning that 2 years is not much data
# to choose filters with.
#
# Caveats specific to these two
# ------------------------------
# 1. **No out-of-sample evidence for the fit itself.** The 2017-2023 column is
#    reassuring but it is the PAST, not the future; the full-record presets
#    looked just as good before they decayed.
# 2. **Balanced - 2yr Train is the less stable of the two** — 55.37% in the
#    first year against 61.98% in the second. A 6.6pp spread on ~400 bets a half
#    is within noise, but Volume's 56.19/56.56 is the steadier pick.
# 3. **No Selective or Hi Hit tier is shipped for this window.** The best
#    candidates were 257 bets at 61.48% and 138 at 64.49% — about 1.5 bets a
#    week, with a +/-6pp interval and no holdout. That is not evidence; use the
#    full-record *PM 5m Hi Hit* if you want a thin, high-rate tier, since it at
#    least has a real holdout behind it.
PRESETS: dict = {
    # The widest net that still holds an edge out of sample — 8,182 bets
    # (~1 per 9.5 hours), 53.43% across 2,729 holdout bets, 52.91% over 2025-26.
    # The margin is thin enough that entry price matters a lot.
    "PM 5m Volume": {
        "atr_length": 14, "pivot_atr_mult": 4.0, "min_pivot_bars": 3,
        "min_wave1_atr": 6.0,
        "enforce_impulse_rules": False,
        "wave2_min_retrace": 0.618, "wave2_max_retrace": 1.0,
        "wave4_min_retrace": 0.0, "wave4_max_retrace": 3.0,
        "trade_setup": SETUP_W3, "entry_mode": MODE_ZONE,
        "max_setup_age_bars": 48,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.0,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": "Follow Count",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # *** THE PICK. *** 5,285 bets, 58.81% hit; 55.39% across 1,809 holdout
    # bets, 54.48% over 2025-26, worst year 52.38%. Same lane as Volume with a
    # real opposing-bar body required and the trend filter on.
    "PM 5m Balanced": {
        "atr_length": 14, "pivot_atr_mult": 4.0, "min_pivot_bars": 3,
        "min_wave1_atr": 6.0,
        "enforce_impulse_rules": False,
        "wave2_min_retrace": 0.618, "wave2_max_retrace": 1.0,
        "wave4_min_retrace": 0.0, "wave4_max_retrace": 3.0,
        "trade_setup": SETUP_W3, "entry_mode": MODE_ZONE,
        "max_setup_age_bars": 48,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.75,
        "vol_atr_length": 20, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "predict_direction": "Follow Count",
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "SMA", "ma_length": 50, "source": "close",
    },
    # 1,488 bets, 60.95% hit; 58.04% on 479 holdout bets — the best holdout RATE
    # of the four, on a third of Balanced's volume. This is the other entry
    # mode: wait for the wave-2 low to be confirmed, then fade the bounce.
    "PM 5m Selective": {
        "atr_length": 14, "pivot_atr_mult": 6.0, "min_pivot_bars": 1,
        "min_wave1_atr": 0.0,
        "enforce_impulse_rules": False,
        "wave2_min_retrace": 0.7, "wave2_max_retrace": 1.2,
        "wave4_min_retrace": 0.0, "wave4_max_retrace": 3.0,
        "trade_setup": SETUP_W3, "entry_mode": MODE_CONFIRM,
        "max_setup_age_bars": 288,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.75,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": "Fade Count",
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "SMA", "ma_length": 100, "source": "close",
    },
    # 384 bets, 67.45% hit — 67.66% train against 67.11% holdout, the only tier
    # in this file that does not shrink. The one place Elliott's rules pay: a
    # complete, rules-valid 1-2-3-4 whose wave-4 low has just confirmed, faded.
    # ~43 bets/year, so read caveat 3 before using it.
    "PM 5m Hi Hit": {
        "atr_length": 14, "pivot_atr_mult": 2.5, "min_pivot_bars": 1,
        "min_wave1_atr": 6.0,
        "enforce_impulse_rules": True,
        "wave2_min_retrace": 0.0, "wave2_max_retrace": 3.0,
        "wave4_min_retrace": 0.146, "wave4_max_retrace": 0.618,
        "trade_setup": SETUP_W5, "entry_mode": MODE_CONFIRM,
        "max_setup_age_bars": 48,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.75,
        "vol_atr_length": 20, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "predict_direction": "Fade Count",
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },

    # ---- refitted on the trailing 2 years only (see the block above) --------

    # 1,898 bets over 2024-07-28..2026-07-28 (~1 per 9.2 hours), 56.38% hit,
    # halves 56.19/56.56 — and 56.89% over the 4,771 bets it makes across
    # 2017-2023, years it was never fitted to. The steadier of the two.
    "PM 5m Volume - 2yr Train": {
        "atr_length": 14, "pivot_atr_mult": 6.0, "min_pivot_bars": 1,
        "min_wave1_atr": 6.0,
        "enforce_impulse_rules": False,
        "wave2_min_retrace": 0.0, "wave2_max_retrace": 3.0,
        "wave4_min_retrace": 0.0, "wave4_max_retrace": 3.0,
        "trade_setup": SETUP_W5, "entry_mode": MODE_CONFIRM,
        "max_setup_age_bars": 288,
        "require_opposing_bar": False, "opposing_bar_min_atr": 0.0,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": "Fade Count",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # 803 bets over the window (~1 per 22 hours), 58.53% hit, and 57.38% across
    # 2017-2023. Elliott's rules ON, on a tighter zigzag. Read caveat 2: its
    # two one-year halves are 55.37% and 61.98%.
    "PM 5m Balanced - 2yr Train": {
        "atr_length": 14, "pivot_atr_mult": 2.5, "min_pivot_bars": 1,
        "min_wave1_atr": 3.0,
        "enforce_impulse_rules": True,
        "wave2_min_retrace": 0.0, "wave2_max_retrace": 3.0,
        "wave4_min_retrace": 0.0, "wave4_max_retrace": 3.0,
        "trade_setup": SETUP_W5, "entry_mode": MODE_CONFIRM,
        "max_setup_age_bars": 48,
        "require_opposing_bar": False, "opposing_bar_min_atr": 0.0,
        "vol_atr_length": 20, "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "predict_direction": "Fade Count",
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
}
