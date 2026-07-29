"""Harmonic Patterns — XABCD geometry, entered at the completion zone.

Idea
----
Harmonic pattern theory (Gartley 1935; formalised with Fibonacci ratios by Scott
Carney) says a reversal can be anticipated from the *proportions* of the last
four swing legs rather than from an oscillator. Label five alternating swing
points X-A-B-C-D and measure each leg against the previous one:

    AB/XA   how much of the impulse leg XA was retraced
    BC/AB   how much of AB was retraced
    CD/BC   how far D projects beyond C
    AD/XA   where D sits relative to the whole XA leg

Each named pattern is one box in that four-dimensional space:

    pattern      AB/XA          BC/AB          CD/BC          D
    Gartley      0.618          0.382-0.886    1.13-1.618     0.786 of XA
    Bat          0.382-0.50     0.382-0.886    1.618-2.618    0.886 of XA
    Butterfly    0.786          0.382-0.886    1.618-2.618    1.27-1.618 of XA
    Crab         0.382-0.618    0.382-0.886    2.618-3.618    1.618 of XA
    Cypher       0.382-0.618    1.13-1.414*    1.272-2.0      0.786 of XC
    Shark        (free)         1.13-1.618*    1.618-2.24     0.886-1.13 of XA
    AB=CD        (free)         0.382-0.886    1.13-2.618     CD = AB
                                (* measured against XA, not AB — as defined)

X-A-B-C are known; **D is a forecast**. Its price is projected two independent
ways — from the pattern's D ratio and from the CD/BC extension — and the overlap
of those two bands is the Potential Reversal Zone (PRZ). The trade is to buy the
PRZ of a bullish pattern (X low, A high, B low, C high, D low) or sell the PRZ of
a bearish one, betting the swing turns there.

Why this is implementable without look-ahead
--------------------------------------------
The usual complaint about harmonic patterns is that they are drawn after the
fact: pick different swing points and every ratio changes. Two rules remove the
discretion here.

  * Swing points are **fractal pivots** (`pivot_left` / `pivot_right`), cleaned
    into a strictly alternating high-low sequence, and a pivot is admitted only
    once the scan reaches bar `j + pivot_right` — the same confirmation cursor
    used in reversal.py. A pattern is built from the **last four confirmed
    pivots and nothing else**, so there is no choice of anchor to make.
  * The PRZ is computed at C, before D exists. The signal fires on the bar that
    first trades into that zone. Everything a signal reads is available in real
    time; a truncation test (re-run the strategy on the series cut at each
    signal bar) reproduces every signal with zero future bars.

An armed PRZ is a standing order. It survives until one of four things happens:
price first trades into it (which consumes the pattern and produces at most one
bet, whether or not the filters then admit it); price blows through the far side
by more than `prz_overshoot_atr` (the pattern failed, and a failed pattern is not
a signal); `max_bars_to_d` bars pass untouched; or the pivot C it hangs off is
overwritten by a more extreme one, which means that swing extended and the
geometry no longer exists. Several patterns can be armed at once — that is how
harmonic scanners work — and if a bullish and a bearish zone complete on the same
bar the bar is discarded as a genuine disagreement.

Parameter groups
----------------
Pivots       pivot_left, pivot_right
Patterns     use_gartley .. use_abcd, ratio_tolerance, require_cd_zone
Geometry     min_xa_atr, max_pattern_bars, max_bars_to_d, max_prz_atr
PRZ Entry    prz_entry, prz_overshoot_atr
Entry Timing require_opposing_bar, opposing_bar_min_atr
Volatility   vol_atr_length, atr_pct_min, atr_pct_max
Decision     predict_direction  (Reversal | Continuation)
Trend Filter (shared) use_trend_filter, trend_logic, ma_type, ma_length, source
Window       (shared) use_trading_window, trade_*, start/end hh:mm

`ratio_tolerance` pads every window above on both sides, in ratio units — it is
the single knob that decides how strict "a Gartley" is. `predict_direction`
takes the pattern at face value (**Reversal**: bullish pattern -> LONG) or fades
it (**Continuation**: the swing that reached D keeps going).

In Polymarket up/down mode the Exit/Backtest params are unused and each signal is
simply a bet on the next candle's direction.
"""

from __future__ import annotations

from typing import List, Optional

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

# Canonical harmonic ratio boxes. Each window is (lo, hi) and is padded by
# `ratio_tolerance` on both sides at run time; lo == hi marks a point ratio that
# only the tolerance gives width to.
#
#   ab     AB/XA, the retracement of the impulse leg
#   bc     retracement of AB — or of XA where `bc_ref` says so (Cypher, Shark)
#   cd     CD/BC, the extension that projects D beyond C
#   d      where D lands, as a ratio of the leg named by `d_ref`:
#            "XA" measured from A, "XC" and "AB" measured from C
#
# (0.0, 9.9) means "not part of this pattern's definition" — left free rather
# than dropped so every pattern runs through the same code path.
_PATTERNS = {
    "Gartley":   {"ab": (0.618, 0.618), "bc": (0.382, 0.886), "bc_ref": "AB",
                  "cd": (1.130, 1.618), "d": (0.786, 0.786), "d_ref": "XA"},
    "Bat":       {"ab": (0.382, 0.500), "bc": (0.382, 0.886), "bc_ref": "AB",
                  "cd": (1.618, 2.618), "d": (0.886, 0.886), "d_ref": "XA"},
    "Butterfly": {"ab": (0.786, 0.786), "bc": (0.382, 0.886), "bc_ref": "AB",
                  "cd": (1.618, 2.618), "d": (1.270, 1.618), "d_ref": "XA"},
    "Crab":      {"ab": (0.382, 0.618), "bc": (0.382, 0.886), "bc_ref": "AB",
                  "cd": (2.618, 3.618), "d": (1.618, 1.618), "d_ref": "XA"},
    "Cypher":    {"ab": (0.382, 0.618), "bc": (1.130, 1.414), "bc_ref": "XA",
                  "cd": (1.272, 2.000), "d": (0.786, 0.786), "d_ref": "XC"},
    "Shark":     {"ab": (0.000, 9.900), "bc": (1.130, 1.618), "bc_ref": "XA",
                  "cd": (1.618, 2.240), "d": (0.886, 1.130), "d_ref": "XA"},
    "AB=CD":     {"ab": (0.000, 9.900), "bc": (0.382, 0.886), "bc_ref": "AB",
                  "cd": (1.130, 2.618), "d": (1.000, 1.000), "d_ref": "AB"},
}

# (param key, pattern name) in the order they are offered in the UI.
_PATTERN_KEYS = [
    ("use_gartley", "Gartley"), ("use_bat", "Bat"),
    ("use_butterfly", "Butterfly"), ("use_crab", "Crab"),
    ("use_cypher", "Cypher"), ("use_shark", "Shark"),
    ("use_abcd", "AB=CD"),
]

_ENTRY_MODES = ["Wick Touch", "Close Inside"]
_DIRECTIONS = ["Reversal", "Continuation"]

# Only the last four pivots are ever read, so the confirmed-pivot list is
# trimmed to this length to stay flat in memory over ~1M bars.
_PIVOT_KEEP = 4


def _push_pivot(pivs: List[dict], kind: str, i: int, price: float):
    """Add a pivot to the alternating (zigzag) sequence.

    Two pivots of the same kind in a row are not a swing: the more extreme one
    wins and replaces the other, which is what keeps X-A-B-C strictly
    alternating without any look-ahead.

    Returns ``(changed, replaced_key)`` — ``replaced_key`` identifies the pivot
    that was overwritten, so patterns anchored on it can be retired.
    """
    if pivs and pivs[-1]["kind"] == kind:
        last = pivs[-1]
        if (price >= last["price"]) if kind == "H" else (price <= last["price"]):
            old = (last["kind"], last["i"], last["price"])
            pivs[-1] = {"kind": kind, "i": i, "price": price}
            return True, old
        return False, None
    pivs.append({"kind": kind, "i": i, "price": price})
    del pivs[:-_PIVOT_KEEP]
    return True, None


def _overlap(a: tuple, b: tuple) -> Optional[tuple]:
    """Intersection of two price bands, or None if they do not overlap."""
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return (lo, hi) if lo <= hi else None


def _build_patterns(pivs: List[dict], enabled: List[tuple], tol: float,
                    sgn: int, atr0: float, p: dict) -> List[dict]:
    """Every enabled pattern the last four confirmed pivots satisfy.

    `sgn` is -1 for a bullish setup (D is a low, projected below C) and +1 for a
    bearish one, which is all that separates the two cases arithmetically.
    """
    X, A, B, C = pivs[-4], pivs[-3], pivs[-2], pivs[-1]

    # Leg lengths, signed so that a well-formed zigzag gives all positives.
    xa = sgn * (X["price"] - A["price"])
    ab = sgn * (B["price"] - A["price"])
    bc = sgn * (B["price"] - C["price"])
    xc = sgn * (X["price"] - C["price"])
    if xa <= 0 or ab <= 0 or bc <= 0:
        return []                       # not a clean impulse-retrace-retrace
    if xa < p["min_xa_atr"] * atr0:
        return []                       # XA is noise, not a swing
    if C["i"] - X["i"] > p["max_pattern_bars"]:
        return []

    ab_ratio = ab / xa
    bc_over_ab = bc / ab          # the usual BC reference
    bc_over_xa = bc / xa          # Cypher and Shark measure BC against XA instead
    use_cd = bool(p["require_cd_zone"])
    max_prz = p["max_prz_atr"] * atr0
    a_price, c_price = A["price"], C["price"]

    out: List[dict] = []
    for name, spec in enabled:
        lo, hi = spec["ab"]
        if not (lo - tol <= ab_ratio <= hi + tol):
            continue
        lo, hi = spec["bc"]
        r = bc_over_ab if spec["bc_ref"] == "AB" else bc_over_xa
        if not (lo - tol <= r <= hi + tol):
            continue

        # D projected from the pattern's own D ratio...
        d_ref = spec["d_ref"]
        leg, anchor = ((xa, a_price) if d_ref == "XA"
                       else (xc, c_price) if d_ref == "XC" else (ab, c_price))
        if leg <= 0:
            continue          # degenerate reference leg (e.g. C behind X on XC)
        lo, hi = spec["d"]
        band = (anchor + sgn * (hi + tol) * leg, anchor + sgn * (lo - tol) * leg)
        band = (min(band), max(band))

        # ...and, if asked, from the CD/BC extension. The overlap is the PRZ.
        if use_cd:
            lo, hi = spec["cd"]
            cd_band = (c_price + sgn * (hi + tol) * bc, c_price + sgn * (lo - tol) * bc)
            band = _overlap(band, (min(cd_band), max(cd_band)))
            if band is None:
                continue

        if band[1] - band[0] > max_prz:
            continue                     # a zone this wide is not a forecast
        # The zone must still be ahead of price in the pattern's direction.
        if sgn < 0 and band[0] >= c_price:
            continue
        if sgn > 0 and band[1] <= c_price:
            continue

        out.append({
            "name": name, "sgn": sgn, "lo": band[0], "hi": band[1],
            "c_i": C["i"], "x_i": X["i"], "atr0": atr0,
            "ab": ab_ratio, "bc": r, "xa_atr": xa / atr0,
            # Identity, so the same X-A-B-C is not armed twice, and the pivot it
            # hangs off, so it can be retired if that pivot is overwritten.
            "key": (name, X["i"], A["i"], B["i"], C["i"], C["price"]),
            "c_key": (C["kind"], C["i"], C["price"]),
        })
    return out


class Harmonic(Strategy):
    id = "harmonic"
    name = "Harmonic Patterns"
    description = ("Gartley / Bat / Butterfly / Crab / Cypher / Shark / AB=CD "
                   "measured on confirmed fractal pivots, entered when price "
                   "first trades into the projected completion zone (PRZ) — "
                   "taking the reversal there or fading it.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Pivots", [
                Param("pivot_left", "Pivot Left Bars", 5, "int", 1, 100, 1,
                      "Bars before a swing point that it must dominate. Larger "
                      "means fewer, more significant swings — and fewer patterns."),
                Param("pivot_right", "Pivot Right Bars", 2, "int", 1, 50, 1,
                      "Bars after a swing point that it must dominate, and so "
                      "also the delay before it may be used. Larger is more "
                      "reliable but later."),
            ]),
            ParamGroup("Patterns", [
                Param("use_gartley", "Gartley", True, "bool",
                      help="AB 0.618 of XA, D at 0.786 of XA. The original."),
                Param("use_bat", "Bat", True, "bool",
                      help="Shallow AB (0.382-0.50), deep D at 0.886 of XA."),
                Param("use_butterfly", "Butterfly", True, "bool",
                      help="AB 0.786, D *beyond* X at 1.27-1.618 of XA."),
                Param("use_crab", "Crab", True, "bool",
                      help="The most extreme: D at 1.618 of XA on a 2.618-3.618 "
                           "CD extension."),
                Param("use_cypher", "Cypher", True, "bool",
                      help="BC overshoots A (1.13-1.414 of XA); D at 0.786 of XC."),
                Param("use_shark", "Shark", True, "bool",
                      help="AB unconstrained, BC 1.13-1.618 of XA, D at "
                           "0.886-1.13 of XA."),
                Param("use_abcd", "AB=CD", True, "bool",
                      help="The four-point base case: CD equals AB in length, "
                           "with BC a 0.382-0.886 retracement. X is ignored "
                           "beyond being the point AB is measured from."),
                Param("ratio_tolerance", "Ratio Tolerance", 0.05, "float", 0.0, 0.5, 0.005,
                      "Pads every ratio window on both sides, in ratio units. "
                      "0.05 turns the Gartley's 0.618 into 0.568-0.668. This is "
                      "the strictness knob: raise it for more patterns, lower it "
                      "for purer ones."),
                Param("require_cd_zone", "Require CD Confluence", True, "bool",
                      help="Project D twice — from the pattern's D ratio and "
                           "from the CD/BC extension — and use only the overlap "
                           "as the PRZ, discarding patterns where the two "
                           "projections disagree. Off uses the D ratio alone."),
            ]),
            ParamGroup("Geometry", [
                Param("min_xa_atr", "Min XA Leg (xATR)", 3.0, "float", 0.0, 50.0, 0.5,
                      "The impulse leg must be at least this many ATRs tall, so "
                      "a flat stretch of tape is not measured as a pattern."),
                Param("max_pattern_bars", "Max Pattern Span (bars)", 500, "int", 5, 5000, 5,
                      "Maximum bars from X to C. Caps how stale the geometry may be."),
                Param("max_bars_to_d", "Max Bars C->D", 100, "int", 1, 2000, 1,
                      "How long the PRZ stays armed after C. A pattern that is "
                      "not reached within this many bars is dropped."),
                Param("max_prz_atr", "Max PRZ Width (xATR)", 3.0, "float", 0.1, 50.0, 0.1,
                      "Discard patterns whose completion zone is wider than this. "
                      "A very wide zone is easy to touch and forecasts nothing."),
            ]),
            ParamGroup("PRZ Entry", [
                Param("prz_entry", "Entry Trigger", "Wick Touch", "enum",
                      options=_ENTRY_MODES,
                      help="Wick Touch fires on the bar whose low (bullish) or "
                           "high (bearish) first reaches the zone. Close Inside "
                           "waits for a close inside it — later, but no longer "
                           "counts a one-tick stab."),
                Param("prz_overshoot_atr", "Max Overshoot (xATR)", 0.50, "float", 0.0, 10.0, 0.05,
                      "How far past the far edge of the zone price may go and "
                      "still count as a completion. Beyond this the pattern has "
                      "failed and is discarded rather than traded."),
            ]),
            ParamGroup("Entry Timing", [
                Param("require_opposing_bar", "Signal Bar Opposes Bet", False, "bool",
                      help="Only bet when the completing bar itself closed "
                           "against the bet — i.e. price is still falling into a "
                           "bullish PRZ. Skips entries where the turn already "
                           "happened."),
                Param("opposing_bar_min_atr", "Min Opposing Body (xATR)", 0.0, "float",
                      0.0, 3.0, 0.05,
                      "Also require that bar's body to be at least this multiple "
                      "of the Vol ATR. 0 accepts any opposing bar. Needs the box "
                      "above."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback. Measures the legs, sizes the PRZ limits, and "
                      "sizes TP/SL in TP/SL mode."),
                Param("atr_pct_min", "ATR % Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR % Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (too violent)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Reversal", "enum",
                      options=_DIRECTIONS,
                      help="Reversal takes the pattern at face value (bullish "
                           "pattern -> LONG at the PRZ). Continuation fades it, "
                           "betting the swing that reached D carries on."),
            ]),
            common.trend_filter_group(),
            common.trading_window_group(),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        enabled = [(name, _PATTERNS[name]) for key, name in _PATTERN_KEYS if p[key]]
        if not enabled:
            return []

        piv_left, piv_right = p["pivot_left"], p["pivot_right"]
        tol = p["ratio_tolerance"]
        max_to_d = p["max_bars_to_d"]
        overshoot = p["prz_overshoot_atr"]
        wick_entry = p["prz_entry"] == "Wick Touch"
        opposing_bar = bool(p["require_opposing_bar"])
        opposing_min = p["opposing_bar_min_atr"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        take_reversal = p["predict_direction"] == "Reversal"

        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_logic"] == "With Trend"
        use_window = bool(p["use_trading_window"])
        allowed = common.allowed_days(p)
        win_start, win_end = common.window_minutes(p)

        atr = ind.atr(candles, p["vol_atr_length"])
        piv_hi, piv_lo = ind.pivots(candles, piv_left, piv_right)
        trend_ma = (common.moving_average(common.source_values(candles, p["source"]),
                                          p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)

        pivs: List[dict] = []        # confirmed, alternating H/L
        active: List[dict] = []      # patterns armed and waiting for their PRZ
        armed: set = set()           # their keys, so one X-A-B-C arms once
        cursor = 0                   # next bar whose pivot status may be admitted

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            # --- advance the pivot cursor FIRST ------------------------------
            # Scan state, not a filter: it must run on every bar, before any
            # `continue`, or the confirmed-pivot history goes wrong. A pivot at
            # bar j is admitted only at bar j + pivot_right, which is what keeps
            # the whole strategy free of look-ahead.
            changed = False
            while cursor <= i - piv_right and cursor < n:
                j = cursor
                for kind, series in (("H", piv_hi), ("L", piv_lo)):
                    if series[j] is None:
                        continue
                    ch, replaced = _push_pivot(pivs, kind, j, series[j])
                    changed |= ch
                    if replaced is not None:
                        # That swing extended, so its geometry is gone: retire
                        # every pattern that hung off it.
                        for pat in [q for q in active if q["c_key"] == replaced]:
                            active.remove(pat)
                            armed.discard(pat["key"])
                cursor += 1

            a = atr[i]
            if changed and len(pivs) >= 4 and a is not None and a > 0:
                # C is the newest pivot; a high there means the next leg is down,
                # i.e. a bullish (buy-the-PRZ) setup. Patterns already armed off
                # older pivots stay armed — a PRZ is a standing order until it is
                # reached, invalidated or expires.
                for pat in _build_patterns(pivs, enabled, tol,
                                           -1 if pivs[-1]["kind"] == "H" else 1,
                                           a, p):
                    if pat["key"] not in armed:
                        armed.add(pat["key"])
                        active.append(pat)
            if not active:
                continue

            lo_px, hi_px, cl = c["low"], c["high"], c["close"]
            hits: List[dict] = []
            for pat in active[:]:
                if i - pat["c_i"] > max_to_d:
                    active.remove(pat)                  # never reached: expired
                    armed.discard(pat["key"])
                    continue
                bullish = pat["sgn"] < 0
                slack = overshoot * pat["atr0"]
                # Blown through the far side: the pattern has failed, and a
                # failed pattern is not a signal.
                if (lo_px < pat["lo"] - slack) if bullish else (hi_px > pat["hi"] + slack):
                    active.remove(pat)
                    armed.discard(pat["key"])
                    continue
                probe = (lo_px if bullish else hi_px) if wick_entry else cl
                if (probe > pat["hi"]) if bullish else (probe < pat["lo"]):
                    continue                            # zone not reached yet
                # Reached: this pattern is now spent either way, so a rejected
                # bet cannot come back as a deeper entry on a later bar.
                active.remove(pat)
                armed.discard(pat["key"])
                hits.append(pat)

            if not hits or a is None or a <= 0:
                continue
            # Bullish and bearish zones can complete on the same bar (one on the
            # low, one on the high). That is a genuine disagreement, not a bet.
            bull = [q for q in hits if q["sgn"] < 0]
            bear = [q for q in hits if q["sgn"] > 0]
            if bull and bear:
                continue
            bullish = bool(bull)
            done = bull or bear

            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue
            if use_window and not common.in_window(c["time"], allowed,
                                                   win_start, win_end):
                continue

            if take_reversal:
                side = "long" if bullish else "short"
            else:
                side = "short" if bullish else "long"

            body = cl - c["open"]
            if opposing_bar:
                if side == "long" and body >= 0:
                    continue
                if side == "short" and body <= 0:
                    continue
                if opposing_min > 0 and abs(body) < opposing_min * a:
                    continue

            if use_trend and not common.trend_ok(side, cl, trend_ma[i], trend_with):
                continue

            lead = max(done, key=lambda q: q["xa_atr"])   # report the biggest leg
            names = sorted({q["name"] for q in done})
            mode = "Reversal" if take_reversal else "Continuation"
            reason = (f"{'Bullish' if bullish else 'Bearish'} {'+'.join(names)} "
                      f"completed in PRZ {lead['lo']:.1f}-{lead['hi']:.1f} "
                      f"(AB/XA {lead['ab']:.3f}, BC {lead['bc']:.3f}, XA "
                      f"{lead['xa_atr']:.1f}xATR) -> {mode} {side.upper()} "
                      f"(ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"pattern": lead["name"], "patterns": names,
                      "bias": "bullish" if bullish else "bearish",
                      "prz_lo": round(lead["lo"], 2), "prz_hi": round(lead["hi"], 2),
                      "ab_ratio": round(lead["ab"], 4),
                      "bc_ratio": round(lead["bc"], 4),
                      "xa_atr": round(lead["xa_atr"], 2),
                      "bars_c_to_d": i - lead["c_i"],
                      "pattern_bars": lead["c_i"] - lead["x_i"],
                      "atr_pct": round(atr_pct, 3),
                      "bar_body_atr": round(body / a, 2)},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m, BTCUSDT). Exit / Backtest params are unused in that mode.
#
# METHOD — fitted to the last two years, with a real holdout inside them
# ----------------------------------------------------------------------
# The brief was "optimise for the last 2 years", so selection ran there and the
# window was split rather than used whole:
#
#   TRAIN    2024-07-29 -> 2025-11-01   selection happened here and only here
#   HOLDOUT  2025-11-01 -> 2026-07-29   scored once, after the picks were frozen
#   UNSWEPT  2017-08-17 -> 2024-07-29   never loaded by the sweep at all — a
#                                       second, much larger out-of-sample check
#
# 5,032 configurations in four stages (810 + 2,592 geometry, 864 filter, 1,536
# fine), every one scored by calling this module's own `generate_signals`, so a
# preset cannot drift from what the dashboard runs. Selection was mechanical:
# admit configs with >= the tier's bet floor on train, both halves of train at
# >= 52%, and `pivot_left` off the grid boundary; then take the highest train hit
# rate. The holdout was read afterwards and changed nothing.
#
# RESULTS — flat $1 per bet, next-candle direction, whole record
# --------------------------------------------------------------
#   preset       bets      hit      z   | unswept 17-24 |  2yr    train  HOLDOUT
#   Volume     43,820   55.38%  +22.5   | 33,710 55.59% | 54.70%  54.56%  54.93%
#   Balanced   13,144   57.11%  +16.3   | 10,604 57.28% | 56.38%  56.49%  56.19%
#   Selective   3,972   57.28%   +9.2   |  3,025 57.06% | 57.97%  60.24%  54.37%
#
# **Balanced is the pick.** It gives up 1.4pp of hit rate against Selective for
# 3.3x the volume, and its train / holdout / unswept columns agree to within
# 1.1pp — the flattest of the three. Selective is the one to distrust: 60.24% on
# train against 54.37% on the holdout is a 5.9pp shrinkage on only 366 holdout
# bets.
#
# WHY THIS LOOKS REAL RATHER THAN FITTED
# ---------------------------------------
#   * The never-swept 2017-2024 years score HIGHER than the window that was
#     optimised on (55.59 / 57.28 / 57.06% against 54.56 / 56.49 / 60.24%), on
#     3-5x the bets. A curve fit does not usually generalise backwards.
#   * It is not directional beta. Bets split almost exactly evenly and both sides
#     win at the same rate (Volume: 21,544 long at 55.44%, 22,276 short at
#     55.33%), while the share of 5m candles closing up is 49.6-50.5% in every
#     calendar year.
#   * No look-ahead. A truncation test — regenerate signals on the series cut off
#     AT each signal bar — reproduces 120 sampled signals across the three
#     presets with zero mismatches and zero future bars available.
#
# THE DIRECTION IS THE TEXTBOOK ONE
# ----------------------------------
# Every admitted config bets WITH the pattern. At the shipped settings, over the
# two-year window: Reversal 54.70 / 56.41 / 58.11% against Continuation 45.19 /
# 43.56 / 41.89% (the same bets, opposite sides). Harmonic theory says the PRZ is
# where the swing turns, and on BTC 5m it is. Note this also makes the strategy
# mean-reverting — a bullish PRZ is reached by price falling into it, so buying
# it fades the last move, exactly like every other strategy that works here.
#
# THE FIBONACCI RATIOS EARN ALMOST NOTHING — THE SHAPE CONSTRAINT EARNS
# ---------------------------------------------------------------------
# Three tests, all at the Balanced preset's geometry over the two-year window.
#
# 1. **The level of D carries no Fibonacci signal.** Replace the pattern set with
#    ONE free box — AB, BC and CD unconstrained, D at an arbitrary retracement r
#    of XA, every other rule identical — and walk r through the canonical values
#    and deliberate non-canonical neighbours:
#
#       r      hit     vs nbrs        r       hit     vs nbrs
#      0.300  51.00%      -         0.886 *  53.72%   +0.29pp
#      0.382 * 50.59%   -0.99pp     0.950    53.08%   -0.29pp
#      0.450  52.17%   +0.71pp      1.000 ~  53.02%   -0.22pp
#      0.500 ~ 52.32%   +0.07pp     1.130    53.40%   -0.06pp
#      0.550  52.32%   +0.04pp      1.272 *  53.90%   +0.03pp
#      0.618 * 52.25%   -0.21pp     1.450    54.32%   +0.43pp
#      0.700  52.61%   -0.29pp      1.618 *  53.89%   -0.07pp
#      0.786 * 53.54%   +0.34pp     1.800    53.60%      -
#                        (* = canonical Fibonacci, ~ = conventional)
#
#    Hit rate rises smoothly with depth and there is no bump at the golden-ratio
#    values. 0.618 comes in 0.21pp BELOW the mean of its neighbours; 0.382 is the
#    single worst point on the curve. Same verdict fib_retracement reached from
#    the other direction.
#
# 2. **But the joint constraint does earn.** That best free box tops out at
#    54.0-54.3%, and tightening its tolerance until it is nearly as selective as
#    the real thing does not close the gap (r=1.272 at tol=0.01: 3,664 bets,
#    54.15%) — against the real six boxes at 2,537 bets and 56.41%. Requiring AB,
#    BC and CD to be jointly in range is worth roughly +2pp over any single
#    D-level rule.
#
# 3. **The canonical values are a weak optimum.** Shift every pattern's AB and D
#    window off its textbook centre by a fixed offset, keeping widths:
#
#       offset   -0.15   -0.10   -0.05    0.00   +0.05   +0.10   +0.15
#       2yr hit  54.54%  55.17%  55.97%  56.41%  55.69%  55.69%  54.65%
#       holdout  56.38%  55.85%  54.41%  56.19%  56.16%  57.69%  56.43%
#
#    Canonical peaks on the two-year window by 0.4-0.6pp over its immediate
#    neighbours — but the holdout column peaks at +0.10, not at 0. Read this as
#    "the textbook numbers are a reasonable place to put the boxes", not as
#    evidence that phi does anything.
#
# OTHER FINDINGS
# --------------
#   * **Per pattern** (one enabled at a time, Balanced settings, 2 years):
#     Crab 479 bets 59.08% (z +4.0), Butterfly 347 @ 59.65% (+3.6), Gartley 902 @
#     55.88% (+3.5), Bat 646 @ 55.11% (+2.6), Cypher 188 @ 55.32% (+1.5), Shark
#     144 @ 52.78% (+0.7), AB=CD 3,365 @ 54.23% (+4.9). AB=CD — the one pattern
#     with no Fibonacci content, it only asks CD to equal AB — carries the most
#     total edge by z while having the lowest rate, purely on volume. Balanced
#     and Selective leave it OFF; Volume keeps it on, which is most of why Volume
#     has 3x the bets and 2pp less edge.
#   * **The four classics alone are a real alternative to Balanced**: Gartley +
#     Bat + Butterfly + Crab at the same settings gives 2,258 bets at 56.82%
#     (train 57.09%, holdout 56.38%) — marginally better than the shipped
#     six-pattern Balanced on every column. It was not picked because the
#     selection rule ranks on train hit and the six-pattern config won there;
#     it is worth knowing about.
#   * **`require_cd_zone` is worth +1.1pp** (56.41% at 2,537 bets on, 55.32% at
#     3,816 off). It also narrows the PRZ, so this is not clean evidence that CD
#     confluence means anything beyond selectivity.
#   * **The entry trigger barely matters**: Close Inside 56.41% vs Wick Touch
#     56.26% at the Balanced settings (holdout: 56.19% vs 56.79%).
#   * **`require_opposing_bar` is worth nothing here** — +0.1pp, against +2.8pp
#     in fib_retracement and Multi Horizon. A PRZ touch already selects for a bar
#     moving into the zone, so the filter is redundant. Off in every preset.
#   * **No weekend gate.** The weekend premium is +0.69 / +0.75 / +2.46pp on
#     Volume / Balanced / Selective — small, and only Selective's is interesting
#     at all (928 weekend bets, |z| < 1.5). No weekend presets are shipped.
#
# CAVEATS, in order of how much they should worry you
# ----------------------------------------------------
# 1. **2017 breaks it.** The partial year 2017 (Aug-Dec) scores 44.91 / 48.24 /
#    43.81% — far below chance, and not noise at 105-1,434 bets. That is the
#    mechanism running in reverse: these presets buy reversals, and 2017 was a
#    parabolic run in which the reversals did not come. Reversal's BOS presets
#    fail in the same year for the same reason. Expect losses in a sustained
#    runaway trend.
# 2. **The edge decays.** Volume runs 55-57% across 2018-2023 and 54.1-55.2%
#    across 2024-2026; Balanced 56.4-59.6% then 54.4-57.5%. The recent numbers
#    are the live estimate, not the headline.
# 3. **Volume is thin by this repo's standards.** Balanced is ~1,270 bets/year
#    and Selective ~470 — roughly one a day. Selective's holdout is 366 bets,
#    which is not enough to separate 54% from 58%.
# 4. **The Volume preset's trend filter is nearly decorative.** Against-SMA50 was
#    selected over no filter by +0.22pp on train. Do not read meaning into it.
# 5. A bet pays only when hit rate > the price paid. Balanced's 56.4% needs a
#    fill below ~0.564, and a real Polymarket book will not quote a directional
#    5m market at 0.50 — the hit rate is the finding, the EV is an upper bound.
# 6. Days and times are UTC; a bar is stamped by its open time.
_HARMONIC_COMMON = {
    "max_pattern_bars": 500, "max_bars_to_d": 300,
    "prz_overshoot_atr": 1.0,
    "require_opposing_bar": False, "opposing_bar_min_atr": 0.0,
    "vol_atr_length": 14,
    "predict_direction": "Reversal",
    "use_trading_window": False,
}

# Every pattern except AB=CD, which fires 3x as often at a 2pp lower rate.
_NO_ABCD = {"use_gartley": True, "use_bat": True, "use_butterfly": True,
            "use_crab": True, "use_cypher": True, "use_shark": True,
            "use_abcd": False}

PRESETS: dict = {
    # 43,820 bets, 55.38% hit (z +22.5); 54.70% over the two years it was fitted
    # to, 54.93% on the holdout, 55.59% across the never-swept 2017-2024. The
    # widest net and the thinnest edge: AB=CD is on, which is most of the volume.
    "PM 5m Volume": {
        **_HARMONIC_COMMON,
        "use_gartley": True, "use_bat": True, "use_butterfly": True,
        "use_crab": True, "use_cypher": True, "use_shark": True, "use_abcd": True,
        "pivot_left": 3, "pivot_right": 3, "ratio_tolerance": 0.10,
        "min_xa_atr": 3.0, "max_prz_atr": 2.0, "require_cd_zone": False,
        "prz_entry": "Wick Touch",
        "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        # Worth +0.22pp on train over no filter at all. See caveat 4.
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "SMA", "ma_length": 50, "source": "close",
    },
    # *** THE PICK. *** 13,144 bets, 57.11% hit (z +16.3); train 56.49%, holdout
    # 56.19%, never-swept 2017-2024 57.28%, and every full calendar year from
    # 2018 on at or above 54.42%. The three windows agree to within 1.1pp, which
    # is the best reason to trust it.
    "PM 5m Balanced": {
        **_HARMONIC_COMMON, **_NO_ABCD,
        "pivot_left": 5, "pivot_right": 3, "ratio_tolerance": 0.10,
        "min_xa_atr": 3.0, "max_prz_atr": 2.0, "require_cd_zone": True,
        "prz_entry": "Close Inside",
        "atr_pct_min": 0.10, "atr_pct_max": 2.0,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
    # 3,972 bets, 57.28% hit (z +9.2) — the best headline of the three and the
    # least trustworthy. 60.24% on train against 54.37% on 366 holdout bets is
    # the worst shrinkage here, and at ~470 bets/year it has no room to prove
    # otherwise. Shipped to show where the frontier ends; prefer Balanced.
    "PM 5m Selective": {
        **_HARMONIC_COMMON, **_NO_ABCD,
        "pivot_left": 5, "pivot_right": 3, "ratio_tolerance": 0.10,
        "min_xa_atr": 7.0, "max_prz_atr": 4.0, "require_cd_zone": False,
        "prz_entry": "Wick Touch",
        "atr_pct_min": 0.10, "atr_pct_max": 2.0,
        "use_trend_filter": True, "trend_logic": "Against Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
    },
}
