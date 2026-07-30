"""Reversal — candlestick patterns, divergence and market structure in one.

Idea
----
"Reversal" is not one signal but three independent ways of arguing that a move
has run out of participants. This strategy implements all three as separate
*detectors* and lets you decide how many must agree:

  1. **Candlestick pattern** — a classic reversal candle (engulfing, hammer /
     shooting star, morning / evening star, piercing / dark cloud) that prints
     *at* a local extreme. The location gate is what makes it a reversal rather
     than a random candle: an engulfing bar in the middle of a range says
     nothing.
  2. **Divergence** — price makes a lower low (or higher high) while the
     oscillator (RSI or MACD histogram) does not confirm it, measured between
     the last two *confirmed* swing pivots.
  3. **Market structure** — either a double top / double bottom whose neckline
     just broke, or a break of structure: a lower-low downtrend whose latest
     swing high just gave way (mirror for the bearish case).

`min_confirmations` then decides how much agreement an entry needs — 1 makes
the detectors an OR, 2 or 3 makes them a consensus. It is clamped to the number
of detectors actually enabled, so turning detectors off can never silently mute
the strategy.

`predict_direction` decides what to do with the call: **Reversal** takes it at
face value (bullish reversal -> LONG), **Continuation** fades it, betting the
prior move survives the pattern.

Look-ahead
----------
Divergence and structure both rest on fractal pivots, which are only knowable
`pivot_right` bars after they print. Pivots are therefore fed in through a
confirmation cursor that admits a pivot at bar j only once the scan reaches bar
j + pivot_right — so nothing a signal reads is unavailable in real time.

Parameter groups
----------------
Candlestick Patterns  use_engulfing, use_pin_bar, use_star, use_piercing,
                      min_body_ratio, min_wick_ratio
Location              use_location, swing_lookback, extreme_tolerance_atr
Pivots                pivot_left, pivot_right, max_pivot_gap
Divergence            use_divergence, osc_type, rsi_length, macd_fast,
                      macd_slow, macd_signal, min_osc_gap
Structure             use_structure, structure_pattern, retest_tolerance_atr
Confirmation          min_confirmations
Volatility            vol_atr_length, atr_pct_min, atr_pct_max
Decision              predict_direction  (Reversal | Continuation)
Trend Filter          (shared) use_trend_filter, trend_logic, ma_type, ...
Allowed Trading Window(shared) use_trading_window, trade_*, start/end hh:mm

Entry logic (a bar must clear every active gate)
------------------------------------------------
  1. Trading window (opt): weekday + UTC time-of-day.
  2. Volatility: ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max]. This
     ATR also sizes TP/SL and both *_atr tolerances.
  3. Detectors vote bullish / bearish (see above). The pattern vote is subject
     to the Location gate; the structure vote deliberately is NOT, because a
     neckline or structure break happens *away* from the extreme by definition.
  4. Consensus: the winning side needs >= min_confirmations votes and strictly
     more than the other side (a tie is treated as genuine disagreement).
  5. Trend filter (opt): close vs MA, per trend_logic.

In Polymarket mode the exit params are ignored and each signal is a
next-candle UP/DOWN bet (long = UP).
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from . import common
from .base import Param, ParamGroup, Signal, Strategy

_OSC_TYPES = ["RSI", "MACD Hist"]
_STRUCTURE_PATTERNS = ["Double Top/Bottom", "Break of Structure"]
_DIRECTIONS = ["Reversal", "Continuation"]

# A pin bar's *opposite* shadow must be no more than this fraction of its
# rejection wick — the "small other shadow" half of the classic definition.
_PIN_OPPOSITE_MAX = 0.5

# Only the last few pivots of each kind are ever read (divergence needs two,
# structure needs two plus the opposite-side reference), so the confirmed-pivot
# lists are trimmed to this length to stay flat in memory over ~1M bars.
_PIVOT_KEEP = 3


def _pattern_vote(candles: List[dict], i: int, min_body: float, min_wick: float,
                  use_engulfing: bool, use_pin: bool, use_star: bool,
                  use_piercing: bool):
    """Candlestick reversal vote at bar i -> ('bull'|'bear'|None, label).

    Bars matching patterns on both sides are a contradiction and vote neither.
    """
    c = candles[i]
    o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
    rng = h - l
    if rng <= 0:
        return None, ""
    body = abs(cl - o)
    body_r = body / rng
    up_wick = (h - max(o, cl)) / rng
    dn_wick = (min(o, cl) - l) / rng

    bull: List[str] = []
    bear: List[str] = []

    if use_pin:
        if dn_wick >= min_wick and up_wick <= dn_wick * _PIN_OPPOSITE_MAX:
            bull.append("hammer")
        if up_wick >= min_wick and dn_wick <= up_wick * _PIN_OPPOSITE_MAX:
            bear.append("shooting star")

    if (use_engulfing or use_piercing) and i >= 1 and body_r >= min_body:
        p = candles[i - 1]
        po, pcl = p["open"], p["close"]
        p_rng = p["high"] - p["low"]
        p_body_r = (abs(pcl - po) / p_rng) if p_rng > 0 else 0.0
        p_mid = (po + pcl) / 2.0

        if use_engulfing:
            # Body-engulfing: this bar's real body covers the previous one's.
            if cl > o and pcl < po and o <= pcl and cl >= po:
                bull.append("bullish engulfing")
            if cl < o and pcl > po and o >= pcl and cl <= po:
                bear.append("bearish engulfing")

        if use_piercing and p_body_r >= min_body:
            # Closes back through the midpoint of the prior body, but not past
            # its far end — a partial reversal (that's what separates it from
            # an engulfing bar).
            if cl > o and pcl < po and o <= pcl and p_mid < cl < po:
                bull.append("piercing line")
            if cl < o and pcl > po and o >= pcl and po < cl < p_mid:
                bear.append("dark cloud cover")

    if use_star and i >= 2 and body_r >= min_body:
        a, m = candles[i - 2], candles[i - 1]
        a_body = abs(a["close"] - a["open"])
        a_rng = a["high"] - a["low"]
        a_body_r = (a_body / a_rng) if a_rng > 0 else 0.0
        m_body = abs(m["close"] - m["open"])
        # The star itself: an indecisive middle bar between two decisive ones.
        if a_body_r >= min_body and m_body <= 0.5 * min(a_body, body):
            a_mid = (a["open"] + a["close"]) / 2.0
            if a["close"] < a["open"] and cl > o and cl > a_mid:
                bull.append("morning star")
            if a["close"] > a["open"] and cl < o and cl < a_mid:
                bear.append("evening star")

    if bull and not bear:
        return "bull", "+".join(bull)
    if bear and not bull:
        return "bear", "+".join(bear)
    return None, ""


class Reversal(Strategy):
    id = "reversal"
    name = "Reversal"
    description = ("Candlestick reversal patterns at a swing extreme, "
                   "oscillator divergence, and market-structure breaks (double "
                   "top/bottom, break of structure) as three independent "
                   "detectors — take the reversal or fade it, with a "
                   "configurable agreement threshold.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Candlestick Patterns", [
                Param("use_engulfing", "Engulfing", True, "bool",
                      help="Bullish/bearish engulfing: this bar's real body "
                           "fully covers the previous (opposite-colour) body."),
                Param("use_pin_bar", "Pin Bar (Hammer / Shooting Star)", True, "bool",
                      help="A long rejection wick with a small opposite shadow."),
                Param("use_star", "Morning / Evening Star", True, "bool",
                      help="Three-bar pattern: decisive bar, small-bodied star, "
                           "then a decisive bar back through the first body's "
                           "midpoint."),
                Param("use_piercing", "Piercing / Dark Cloud", False, "bool",
                      help="Closes back past the midpoint of the prior body but "
                           "not beyond its far end — a partial engulfing."),
                Param("min_body_ratio", "Min Body Ratio", 0.30, "float", 0.0, 1.0, 0.01,
                      "Minimum |close-open|/(high-low) for the decisive bar of "
                      "a pattern. Does not apply to pin bars, which are small-"
                      "bodied by definition."),
                Param("min_wick_ratio", "Min Wick Ratio (pin bars)", 0.50, "float", 0.0, 1.0, 0.01,
                      "Minimum rejection wick as a fraction of the bar range for "
                      "a hammer / shooting star."),
            ]),
            ParamGroup("Location", [
                Param("use_location", "Require Pattern At Extreme", True, "bool",
                      help="Only count a candlestick pattern if it prints at a "
                           "local extreme. Off turns the pattern detector into a "
                           "pure candle scan, which is usually noise. Never "
                           "applies to the structure detector — a neckline break "
                           "happens away from the extreme by design."),
                Param("swing_lookback", "Swing Lookback (bars)", 20, "int", 2, 500, 1,
                      "Window whose high/low the pattern bar must be sitting at."),
                Param("extreme_tolerance_atr", "Extreme Tolerance (xATR)", 0.25, "float", 0.0, 5.0, 0.05,
                      "How far off that extreme still counts, in ATRs. 0 demands "
                      "an exact new extreme."),
            ]),
            ParamGroup("Pivots", [
                Param("pivot_left", "Pivot Left Bars", 3, "int", 1, 50, 1,
                      "Bars before a pivot that it must dominate."),
                Param("pivot_right", "Pivot Right Bars", 3, "int", 1, 50, 1,
                      "Bars after a pivot that it must dominate. Also the delay "
                      "before a pivot may be used — larger is more reliable but "
                      "later."),
                Param("max_pivot_gap", "Max Pivot Gap (bars)", 60, "int", 2, 1000, 1,
                      "Maximum distance between the two pivots being compared, "
                      "and the maximum age of the newer one at signal time. "
                      "Shared by the divergence and structure detectors."),
            ]),
            ParamGroup("Divergence", [
                Param("use_divergence", "Use Divergence", False, "bool",
                      help="Price makes a new pivot extreme that the oscillator "
                           "does not confirm."),
                Param("osc_type", "Oscillator", "RSI", "enum", options=_OSC_TYPES,
                      help="Series compared against price at the two pivots."),
                Param("rsi_length", "RSI Length", 14, "int", 2, 200, 1,
                      "Lookback when Oscillator is RSI."),
                Param("macd_fast", "MACD Fast", 12, "int", 2, 200, 1,
                      "Fast EMA when Oscillator is MACD Hist."),
                Param("macd_slow", "MACD Slow", 26, "int", 3, 400, 1,
                      "Slow EMA when Oscillator is MACD Hist."),
                Param("macd_signal", "MACD Signal", 9, "int", 1, 100, 1,
                      "Signal EMA when Oscillator is MACD Hist."),
                Param("min_osc_gap", "Min Oscillator Gap", 1.0, "float", 0.0, 100.0, 0.1,
                      "Minimum oscillator difference between the two pivots for "
                      "the divergence to count. Note the natural scale differs "
                      "wildly: RSI is 0-100, MACD histogram is in price units."),
            ]),
            ParamGroup("Structure", [
                Param("use_structure", "Use Market Structure", False, "bool",
                      help="Detect a double top/bottom or a break of structure."),
                Param("structure_pattern", "Structure Pattern", "Double Top/Bottom", "enum",
                      options=_STRUCTURE_PATTERNS,
                      help="Double Top/Bottom: two pivots at the same level, "
                           "fires when the neckline between them breaks. Break "
                           "of Structure: a lower-low downtrend whose latest "
                           "swing high gives way (mirror for bearish)."),
                Param("retest_tolerance_atr", "Level Match Tolerance (xATR)", 0.50, "float", 0.0, 10.0, 0.05,
                      "How close the two pivots must be to count as the same "
                      "level, in ATRs. Double Top/Bottom only."),
            ]),
            ParamGroup("Confirmation", [
                Param("min_confirmations", "Min Confirmations", 1, "int", 1, 3, 1,
                      "How many detectors (pattern / divergence / structure) "
                      "must agree on a direction. Clamped to the number of "
                      "detectors actually enabled, so this can never mute the "
                      "strategy outright. A bar with votes on both sides is "
                      "discarded unless one side strictly outnumbers the other."),
            ]),
            ParamGroup("Volatility", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback. Sizes TP/SL and both xATR tolerances above."),
                Param("atr_pct_min", "ATR % Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR % Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (too violent)."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Reversal", "enum",
                      options=_DIRECTIONS,
                      help="Reversal takes the call at face value (bullish "
                           "reversal -> LONG). Continuation fades it, betting "
                           "the prior move survives."),
            ]),
            common.trend_filter_group(),
            common.trading_window_group(),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)

        use_engulfing = bool(p["use_engulfing"])
        use_pin = bool(p["use_pin_bar"])
        use_star = bool(p["use_star"])
        use_piercing = bool(p["use_piercing"])
        min_body, min_wick = p["min_body_ratio"], p["min_wick_ratio"]

        use_location = bool(p["use_location"])
        swing_lookback = p["swing_lookback"]
        extreme_tol = p["extreme_tolerance_atr"]

        piv_left, piv_right = p["pivot_left"], p["pivot_right"]
        max_gap = p["max_pivot_gap"]

        use_div = bool(p["use_divergence"])
        osc_type = p["osc_type"]
        min_osc_gap = p["min_osc_gap"]

        use_struct = bool(p["use_structure"])
        double_top = p["structure_pattern"] == "Double Top/Bottom"
        level_tol = p["retest_tolerance_atr"]

        vol_len = p["vol_atr_length"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]

        take_reversal = p["predict_direction"] == "Reversal"

        use_trend = bool(p["use_trend_filter"])
        trend_with = p["trend_logic"] == "With Trend"

        use_window = bool(p["use_trading_window"])
        allowed = common.allowed_days(p)
        win_start, win_end = common.window_minutes(p)

        use_pattern = use_engulfing or use_pin or use_star or use_piercing
        enabled = int(use_pattern) + int(use_div) + int(use_struct)
        if enabled == 0:
            return []
        need = max(1, min(p["min_confirmations"], enabled))

        n = len(candles)
        atr = ind.atr(candles, vol_len)
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        if use_location and use_pattern:
            # NB: rolling_high_low returns (lows, highs) — the opposite order to
            # donchian() next to it in indicators.py.
            swing_lo, swing_hi = ind.rolling_high_low(candles, swing_lookback)
        else:
            swing_hi = swing_lo = [None] * n

        use_pivots = use_div or use_struct
        if use_pivots:
            piv_hi, piv_lo = ind.pivots(candles, piv_left, piv_right)
        else:
            piv_hi = piv_lo = [None] * n

        if use_div:
            if osc_type == "MACD Hist":
                _, _, osc = ind.macd([c["close"] for c in candles],
                                     p["macd_fast"], p["macd_slow"], p["macd_signal"])
            else:
                osc = ind.rsi(candles, p["rsi_length"])
        else:
            osc = [None] * n

        trend_ma = (common.moving_average(common.source_values(candles, p["source"]),
                                          p["ma_type"], p["ma_length"])
                    if use_trend else [None] * n)

        # Confirmed pivots. `cursor` is the next bar whose pivot status may be
        # admitted; a pivot at bar j becomes admissible only at bar j+piv_right,
        # which is what keeps the detectors free of look-ahead.
        conf_hi: List[dict] = []
        conf_lo: List[dict] = []
        cursor = 0

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            # --- advance the pivot cursor FIRST -----------------------------
            # This is scan state, not a filter: it must run on every bar, before
            # any `continue`, or the confirmed-pivot history goes wrong.
            if use_pivots:
                while cursor <= i - piv_right and cursor < n:
                    j = cursor
                    if piv_hi[j] is not None:
                        prev = conf_hi[-1] if conf_hi else None
                        conf_hi.append({
                            "i": j, "price": piv_hi[j], "osc": osc[j],
                            # lowest low back to the previous pivot high: the
                            # neckline a double top has to break.
                            "mid": min(lows[prev["i"]:j + 1]) if prev else None,
                        })
                        del conf_hi[:-_PIVOT_KEEP]
                    if piv_lo[j] is not None:
                        prev = conf_lo[-1] if conf_lo else None
                        conf_lo.append({
                            "i": j, "price": piv_lo[j], "osc": osc[j],
                            "mid": max(highs[prev["i"]:j + 1]) if prev else None,
                        })
                        del conf_lo[:-_PIVOT_KEEP]
                    cursor += 1

            if use_window and not common.in_window(c["time"], allowed, win_start, win_end):
                continue

            a = atr[i]
            if a is None or a <= 0:
                continue
            cl = c["close"]
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            bull_votes: List[str] = []
            bear_votes: List[str] = []

            # --- 1. candlestick pattern, gated by location ------------------
            if use_pattern:
                pat_dir, pat_label = _pattern_vote(
                    candles, i, min_body, min_wick,
                    use_engulfing, use_pin, use_star, use_piercing)
                if pat_dir is not None and use_location:
                    # A bullish reversal must print at the window's low, a
                    # bearish one at its high.
                    ref = swing_lo[i] if pat_dir == "bull" else swing_hi[i]
                    if ref is None:
                        pat_dir = None
                    else:
                        off = (c["low"] - ref) if pat_dir == "bull" else (ref - c["high"])
                        if off > extreme_tol * a:
                            pat_dir = None
                if pat_dir == "bull":
                    bull_votes.append(pat_label)
                elif pat_dir == "bear":
                    bear_votes.append(pat_label)

            # --- 2. divergence between the last two confirmed pivots --------
            if use_div:
                if len(conf_lo) >= 2:
                    a0, b0 = conf_lo[-2], conf_lo[-1]
                    if (a0["osc"] is not None and b0["osc"] is not None
                            and b0["i"] - a0["i"] <= max_gap and i - b0["i"] <= max_gap
                            and b0["price"] < a0["price"]
                            and b0["osc"] - a0["osc"] >= min_osc_gap):
                        bull_votes.append("bullish divergence")
                if len(conf_hi) >= 2:
                    a1, b1 = conf_hi[-2], conf_hi[-1]
                    if (a1["osc"] is not None and b1["osc"] is not None
                            and b1["i"] - a1["i"] <= max_gap and i - b1["i"] <= max_gap
                            and b1["price"] > a1["price"]
                            and a1["osc"] - b1["osc"] >= min_osc_gap):
                        bear_votes.append("bearish divergence")

            # --- 3. market structure ----------------------------------------
            # Both variants fire only on the bar that first closes through the
            # level, so a broken level can't re-signal every bar afterwards.
            if use_struct and i >= 1:
                prev_close = candles[i - 1]["close"]
                if double_top:
                    if len(conf_hi) >= 2:
                        a2, b2 = conf_hi[-2], conf_hi[-1]
                        neck = b2["mid"]
                        if (neck is not None
                                and b2["i"] - a2["i"] <= max_gap and i - b2["i"] <= max_gap
                                and abs(b2["price"] - a2["price"]) <= level_tol * a
                                and cl < neck <= prev_close):
                            bear_votes.append("double top")
                    if len(conf_lo) >= 2:
                        a3, b3 = conf_lo[-2], conf_lo[-1]
                        neck = b3["mid"]
                        if (neck is not None
                                and b3["i"] - a3["i"] <= max_gap and i - b3["i"] <= max_gap
                                and abs(b3["price"] - a3["price"]) <= level_tol * a
                                and cl > neck >= prev_close):
                            bull_votes.append("double bottom")
                else:
                    # Bullish BOS: lower swing lows (a downtrend), then the most
                    # recent swing high — which must postdate the older low —
                    # gives way on a close.
                    if len(conf_lo) >= 2 and conf_hi:
                        ref = conf_hi[-1]
                        if (conf_lo[-1]["price"] < conf_lo[-2]["price"]
                                and ref["i"] > conf_lo[-2]["i"] and i - ref["i"] <= max_gap
                                and cl > ref["price"] >= prev_close):
                            bull_votes.append("bullish BOS")
                    if len(conf_hi) >= 2 and conf_lo:
                        ref = conf_lo[-1]
                        if (conf_hi[-1]["price"] > conf_hi[-2]["price"]
                                and ref["i"] > conf_hi[-2]["i"] and i - ref["i"] <= max_gap
                                and cl < ref["price"] <= prev_close):
                            bear_votes.append("bearish BOS")

            # --- consensus ---------------------------------------------------
            nb, ns = len(bull_votes), len(bear_votes)
            if nb >= need and nb > ns:
                call, votes = "bull", bull_votes
            elif ns >= need and ns > nb:
                call, votes = "bear", bear_votes
            else:
                continue

            if take_reversal:
                side = "long" if call == "bull" else "short"
            else:
                side = "short" if call == "bull" else "long"

            if use_trend:
                m = trend_ma[i]
                if m is None:
                    continue
                if not common.trend_ok(side, cl, m, trend_with):
                    continue

            mode = "Reversal" if take_reversal else "Continuation"
            reason = (f"{call.capitalize()}ish reversal -> {mode} {side.upper()} "
                      f"[{', '.join(votes)}] ({nb + ns}/{enabled} votes, "
                      f"ATR% {atr_pct:.2f})")
            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"call": call, "votes": votes, "vote_count": len(votes),
                      "detectors_enabled": enabled, "required": need,
                      "atr_pct": round(atr_pct, 3)},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets — Break of Structure, traded as Continuation (i.e. FADE the break).
#
# METHOD. Unlike the other presets in this repo, these were selected with a
# genuine holdout rather than on the full record:
#
#   TRAIN    2024-07-01 -> 2025-11-01   selection happened here and only here
#   HOLDOUT  2025-11-01 -> 2026-07-29   scored once, after the pick was frozen
#   UNSWEPT  2018-01-01 -> 2024-07-01   the sweep never loaded these years at all
#
# 600 configs over pivot_left x pivot_right x max_pivot_gap x the ATR% band,
# Polymarket 5m up/down mode. Admission on TRAIN only: >= 2,000 bets, both
# halves of train >= 52%, and pivot_left off the grid boundary. That last rule
# matters — train hit rose monotonically with pivot_left all the way to the edge
# of the grid (52.9% -> 56.1%) while the holdout stayed flat, the standard
# overfitting signature, so the train maximum was deliberately NOT taken.
#
#   preset          bets     hit    2018-23   train   HOLDOUT   worst yr      z
#   BOS Volume    32,630  56.60%    58.08%   54.02%   54.83%   54.07% (24)  23.9
#   BOS Balanced  22,386  56.84%    58.09%   55.09%   55.86%   53.67% (24)  20.5
#
# WHY THIS IS PROBABLY REAL. Three things that a curve-fit would not survive:
#   * The 2018-2023 columns are 15-23k bets from years the sweep never touched,
#     and they score HIGHER than the window that was optimised on.
#   * The holdout beats train for both presets — no post-selection decay.
#   * It is not directional beta. Bets run ~48% long / ~52% short, and the
#     fraction of ALL 5m candles closing up is 49.6-50.5% in every single year,
#     so a near-balanced bettor has no drift to harvest.
#
# WHERE IT FAILS — read this before trusting it. 2017 (partial year, Aug-Dec)
# scores 43.9% / 44.7%, far below chance, and that is not noise at ~1,000 bets.
# It is the mechanism working in reverse: these presets FADE a structure break,
# and 2017 was a parabolic bull run in which breaks kept going. Expect this to
# lose in a sustained runaway trend. The edge also decays — 58% across 2018-2023
# against 54-56% across 2024-2026 — so treat the recent numbers as the live
# estimate, not the headline.
#
# The +0.13 EV per $1 at 0.50 odds assumes you are filled at 0.50. You will not
# be: a real Polymarket book prices a directional 5m market away from even, and
# breakeven scales with the price paid. Hit rate is the finding here; the EV
# figure is an upper bound, not a forecast.
#
# NOT SHIPPED, and why. The candlestick-pattern family (384 configs) and the
# divergence family were swept the same way and did not survive: patterns hit
# 54-55% on train but fell to 49-53% on the holdout across every top config,
# and RSI/MACD divergence never cleared 50.7% on train at any usable volume.
# Both remain available as parameters — they are just not worth a preset.
_BOS_COMMON = {
    # Structure is the only detector; patterns and divergence off.
    "use_engulfing": False, "use_pin_bar": False, "use_star": False,
    "use_piercing": False, "min_body_ratio": 0.30, "min_wick_ratio": 0.50,
    "use_location": True, "swing_lookback": 20, "extreme_tolerance_atr": 0.25,
    "max_pivot_gap": 60,
    "use_divergence": False, "osc_type": "RSI", "rsi_length": 14,
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "min_osc_gap": 1.0,
    "use_structure": True, "structure_pattern": "Break of Structure",
    "retest_tolerance_atr": 0.50,
    "min_confirmations": 1,
    "vol_atr_length": 14, "atr_pct_min": 0.08, "atr_pct_max": 1.5,
    "predict_direction": "Continuation",
    "use_trend_filter": False, "trend_logic": "With Trend",
    "ma_type": "EMA", "ma_length": 200, "source": "close",
    "use_trading_window": False,
}

PRESETS: dict = {
    # 32,630 bets, 56.60% hit; 2018-23 58.08%, holdout 54.83%, worst yr 54.07%.
    # Most volume — a shallower left window admits more structure breaks.
    "PM 5m BOS Volume": {**_BOS_COMMON, "pivot_left": 12, "pivot_right": 1},
    # 22,386 bets, 56.84% hit; 2018-23 58.09%, holdout 55.86%, worst yr 53.67%.
    # Best hit rate of the two and the better holdout, for ~2/3 the volume:
    # a 30-bar left window only calls a genuinely significant swing a pivot.
    "PM 5m BOS Balanced": {**_BOS_COMMON, "pivot_left": 30, "pivot_right": 1},
}

# ---------------------------------------------------------------------------
# 1-MINUTE presets. Swept separately, because the parameters do NOT carry over:
# `pivot_left` counts BARS, so the 5m winner's 30 is 150 *minutes*. Run at the
# default pivot_left=3 the whole family reads as dead on 1m tape (Break of
# Structure / Continuation scores 49.26% on train). Pushing the left window out
# to the same wall-clock scale restores it, and the holdout then rises
# monotonically with it — 51.31% at pivot_left=20 up to 53.73% at 300.
#
# Data: market.db BTCUSDT 1m, which has REAL high/low and is 100% complete over
# the window. btc_1s.db was evaluated as the source and rejected: aggregating
# its per-second open/close reproduces open and close essentially exactly
# (close matched on 10,080/10,080 sample bars) but understates the true bar
# range by ~4%, because per-second open/close cannot see inside a second — 39%
# of bars miss the real extreme. Reversal reads high/low for wicks, pivots, ATR
# and the location gate, so the real klines win. The 1s reconstruction was used
# instead as a robustness check, and the presets survive it (see below).
#
# Protocol as before — train 2024-01..2025-10, holdout 2025-10..2026-07 scored
# once after freezing, and 2018-2023 never loaded by the sweep at all.
#
#   preset          bets     hit    2018-23   train   HOLDOUT     z
#   1m BOS Volume  67,441  52.03%    53.19%  51.76%   53.15%   10.5
#   1m BOS Balanced 58,099 52.22%    53.46%  51.83%   53.83%   10.7
#
# READ THE HIT RATES AGAINST 48.44%, NOT 50%. On 1m tape 3.12% of candles close
# exactly at their open, and a flat candle loses whichever side you took, so the
# ceiling for ANY 50/50 bettor is (1 - flat)/2 = 48.44%. The Balanced preset's
# 52.22% is therefore +3.78pp over a coin flip (z = +18.2), not +2.2pp. The flat
# rate swings hard by year — 33.58% in 2017, 0.12% in 2021, 3.46% in 2025 — so
# per-year hit rates are only interpretable against that year's own ceiling.
#
# WHERE IT FAILS. Measured against each year's ceiling, Balanced is NEGATIVE in
# the first two years and positive in every year since:
#
#   2017  -3.11pp (z -3.1)      2022  +6.21pp (z  +9.6)
#   2018  -2.35pp (z -3.4)      2023  +6.60pp (z  +9.7)
#   2019  +5.07pp (z +7.2)      2024  +2.54pp (z  +4.6)
#   2020  +5.26pp (z +8.1)      2025  +4.51pp (z  +8.4)
#   2021  +3.34pp (z +5.8)      2026  +5.08pp (z  +6.6)
#
# 2017-2018 is the same failure mode the 5m presets show, and it is coherent:
# these presets FADE structure breaks, and both the 2017 parabolic run and the
# 2018 crash were sustained one-way trends in which breaks kept going. Expect
# losses in a strongly trending regime. 2017 also had 33.58% flat candles on
# thin early Binance liquidity, which caps any strategy near 33%.
#
# ROBUSTNESS. Re-running both presets on 1m bars rebuilt from btc_1s.db
# (synthetic high/low, ~4% narrower ranges) over 2026-02..07 changes nothing
# material: Volume 53.33% -> 52.96%, Balanced 53.44% -> 53.58%, both well inside
# their 95% intervals. The edge does not depend on exact wick extremes.
_BOS_1M_COMMON = {
    **_BOS_COMMON,
    # A pivot must dominate a wall-clock window comparable to the 5m presets',
    # and 1m bars are individually far smaller, so both the pivot reach and the
    # volatility floor are rescaled.
    "max_pivot_gap": 300,
    "atr_pct_min": 0.02, "atr_pct_max": 1.5,
}

PRESETS.update({
    # 67,441 bets, 52.03% hit vs a 48.44% ceiling; 2018-23 53.19%, holdout 53.15%.
    "PM 1m BOS Volume": {**_BOS_1M_COMMON, "pivot_left": 90, "pivot_right": 2},
    # 58,099 bets, 52.22% hit vs a 48.44% ceiling; 2018-23 53.46%, holdout 53.83%.
    # Best hit of the two and the better holdout; a 150-bar (2.5 h) left window.
    "PM 1m BOS Balanced": {**_BOS_1M_COMMON, "pivot_left": 150, "pivot_right": 1},
})
