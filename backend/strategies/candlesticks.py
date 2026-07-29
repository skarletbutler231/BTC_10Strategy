"""Candlesticks — the classic Japanese patterns, defined mechanically.

Idea
----
Candlestick patterns are the oldest chart-reading tool there is: a hammer, an
engulfing bar, a morning star. Each is a claim about *who won the bar* — a long
lower wick says sellers pushed price down and were rejected; an engulfing bar
says one side's whole range was erased by the other. The claim is then read as
a reversal (the prior move is exhausted) or as a continuation (the move has
conviction).

The usual problem with the tool is that "that's a hammer" is a judgement call.
So every pattern here is a **formula over OHLC**, with the fuzzy parts — how big
is a "strong body", how long is a "long wick" — exposed as parameters a sweep
can turn. Nine pattern families are available, each independently switchable, so
a sweep can ask which of them (if any) actually predicts the next 5m candle on
BTC rather than assuming the textbook is right.

**One structural adaptation, and it matters.** The textbook definitions of
piercing line, dark cloud cover and the star patterns all require a *gap* — the
next session opening away from the last close. Crypto trades 24/7, so on a 5m
BTC chart `open[i] == close[i-1]` almost exactly, every bar, and a strict gap
test would make those three patterns fire essentially never. Every gap condition
here is therefore relaxed to a **touch** condition (`<=` / `>=` instead of
`<` / `>`). This is a real change of meaning, not a rounding detail: what
survives of those patterns is the body geometry, not the gap.

Pattern families
----------------
Reversal patterns (read as: the prior move is finished)

  ``pat_engulfing``  Engulfing — bar i's body swallows bar i-1's opposite-colour
                     body. Optionally the whole range (``engulf_mode``).
  ``pat_hammer``     Hammer / Shooting Star — small body at one end, a wick of at
                     least ``pin_wick_min`` of the range at the other, and at
                     most ``pin_opp_wick_max`` on the near side.
  ``pat_harami``     Harami (inside bar) — a strong body followed by an
                     opposite-colour body contained inside it.
  ``pat_piercing``   Piercing / Dark Cloud Cover — bar i opens at or beyond bar
                     i-1's close and closes back past the midpoint of its body,
                     without fully engulfing it.
  ``pat_star``       Morning / Evening Star — strong body, small-bodied pause bar
                     beyond its close, then a strong body back through the
                     midpoint of the first.
  ``pat_doji``       Doji — body at most ``doji_body_max`` of the range. Has no
                     direction of its own, so it is read against the prior move
                     (see below).
  ``pat_tweezer``    Tweezer top / bottom — two consecutive opposite-colour bars
                     whose highs (top) or lows (bottom) match within
                     ``tweezer_tol_atr`` × ATR.

Continuation patterns (read as: the move has conviction)

  ``pat_marubozu``   Marubozu — body at least ``marubozu_body_min`` of the range;
                     almost no wick in either direction.
  ``pat_soldiers``   Three White Soldiers / Three Black Crows — three strong
                     same-colour bodies, each closing beyond the last.

Parameter groups
----------------
Patterns          pat_engulfing, pat_hammer, pat_harami, pat_piercing, pat_star,
                  pat_doji, pat_marubozu, pat_soldiers, pat_tweezer
Pattern Geometry  body_strong_min, body_small_max, doji_body_max, pin_wick_min,
                  pin_opp_wick_max, marubozu_body_min, tweezer_tol_atr,
                  engulf_mode, min_range_atr
Prior Move        require_prior_move, prior_move_logic, prior_move_bars,
                  prior_move_atr
Volatility        vol_atr_length, atr_pct_min, atr_pct_max
Trend Filter      use_trend_filter, trend_logic, ma_type, ma_length, source
Decision          predict_direction  (Pattern | Fade)
Day of Week       trade_mon .. trade_sun

Entry logic
-----------
  1. Every enabled pattern is tested on the closed bars ending at bar i. A match
     carries a direction (bullish -> long, bearish -> short) and a kind
     (reversal or continuation).
  2. `require_prior_move` demands context, which is what makes a pattern a
     pattern rather than a shape: the net close-to-close move over the
     `prior_move_bars` bars ending at bar i-1 must be at least
     `prior_move_atr` × ATR. `prior_move_logic` says which way it has to point —

       Textbook   a reversal pattern needs the move **against** it (a hammer
                  needs a decline into it), a continuation pattern needs it
                  **with**. The classical reading.
       Extension  every pattern needs the move **with** its direction: the bar
                  caps a move already under way rather than contradicting one.
       Reversal   every pattern needs the move **against** its direction.

     Extension is not a curiosity — it is where the measured edge lives, and by
     a wide margin. See the preset notes at the bottom of this file.

     A doji has no intrinsic direction: it always takes the side that opposes
     the prior move, and is skipped when that move is flat. It therefore cannot
     satisfy Extension and never fires under it.
  3. If the bar's enabled patterns disagree — some bullish, some bearish — the
     bar is discarded rather than resolved by majority. Same rule as the
     Combined strategy applies to disagreeing voters.
  4. The bar's range must be at least `min_range_atr` × ATR, ATR%
     (`vol_atr_length`) must sit inside [atr_pct_min, atr_pct_max], and the
     optional trend and weekday filters must pass.
  5. `predict_direction` = Pattern takes the textbook side; Fade takes the
     opposite. The whole point of exposing it is that the textbook side is a
     hypothesis, not a given.

The Vol ATR sizes TP/SL; in Polymarket up/down mode the exit params are unused
and each signal is simply a bet on the next candle's direction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup, Signal, Strategy

_DAYS = ["trade_mon", "trade_tue", "trade_wed", "trade_thu",
         "trade_fri", "trade_sat", "trade_sun"]  # index == datetime.weekday()

# Pattern key -> (label, kind). "rev" reads the prior move as exhausted;
# "cont" reads it as having conviction. The kind only affects which way
# `require_prior_move` points.
PATTERNS = {
    "pat_engulfing": ("Engulfing", "rev"),
    "pat_hammer": ("Hammer / Shooting Star", "rev"),
    "pat_harami": ("Harami (inside bar)", "rev"),
    "pat_piercing": ("Piercing / Dark Cloud", "rev"),
    "pat_star": ("Morning / Evening Star", "rev"),
    "pat_doji": ("Doji", "rev"),
    "pat_tweezer": ("Tweezer Top / Bottom", "rev"),
    "pat_marubozu": ("Marubozu", "cont"),
    "pat_soldiers": ("Three Soldiers / Crows", "cont"),
}


class Candlesticks(Strategy):
    id = "candlesticks"
    name = "Candlesticks"
    description = ("Nine classic candlestick pattern families — engulfing, "
                   "hammer, harami, piercing, star, doji, tweezer, marubozu, "
                   "soldiers — each defined as a formula over OHLC with its "
                   "fuzzy thresholds exposed, so a sweep can test which of them "
                   "actually predicts the next candle.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Patterns", [
                Param("pat_engulfing", "Engulfing", True, "bool",
                      help="Bar i's body swallows bar i-1's opposite-colour body. "
                           "Bullish = down bar then a larger up bar."),
                Param("pat_hammer", "Hammer / Shooting Star", True, "bool",
                      help="Small body at one end of the range with a long "
                           "rejection wick at the other. Bullish = long lower wick."),
                Param("pat_harami", "Harami (inside bar)", False, "bool",
                      help="A strong body followed by an opposite-colour body "
                           "contained entirely inside it — momentum stalling."),
                Param("pat_piercing", "Piercing / Dark Cloud", False, "bool",
                      help="Bar i opens at/beyond bar i-1's close and closes back "
                           "past the midpoint of its body without engulfing it."),
                Param("pat_star", "Morning / Evening Star", False, "bool",
                      help="Strong body, small-bodied pause beyond its close, then "
                           "a strong body back through the first bar's midpoint."),
                Param("pat_doji", "Doji", False, "bool",
                      help="Body at most Doji Max Body of the range — indecision. "
                           "Direction comes from the prior move, which it fades."),
                Param("pat_tweezer", "Tweezer Top / Bottom", False, "bool",
                      help="Two opposite-colour bars whose highs (top) or lows "
                           "(bottom) match within Tweezer Tolerance × ATR."),
                Param("pat_marubozu", "Marubozu", False, "bool",
                      help="Near-wickless body — a bar one side owned outright. "
                           "Read as continuation, not reversal."),
                Param("pat_soldiers", "Three Soldiers / Crows", False, "bool",
                      help="Three strong same-colour bodies, each closing beyond "
                           "the last. Read as continuation."),
            ]),
            ParamGroup("Pattern Geometry", [
                Param("body_strong_min", "Strong Body Min (body/range)", 0.5, "float",
                      0.0, 1.0, 0.05,
                      "How decisive a bar must be to count as a 'strong body' — "
                      "used by engulfing, harami's outer bar, the star's bookends "
                      "and the soldiers."),
                Param("body_small_max", "Small Body Max (body/range)", 0.3, "float",
                      0.0, 1.0, 0.05,
                      "Ceiling on body/range for the 'small body' bars: the hammer's "
                      "body and the star's middle pause bar."),
                Param("doji_body_max", "Doji Max Body (body/range)", 0.1, "float",
                      0.0, 0.5, 0.01,
                      "Ceiling on body/range for a doji."),
                Param("pin_wick_min", "Pin Wick Min (wick/range)", 0.5, "float",
                      0.1, 1.0, 0.05,
                      "Minimum rejection wick, as a fraction of the bar's range, "
                      "for a hammer (lower) or shooting star (upper)."),
                Param("pin_opp_wick_max", "Pin Opposite Wick Max", 0.2, "float",
                      0.0, 1.0, 0.05,
                      "Ceiling on the wick at the other end of a hammer / shooting "
                      "star, so the bar is one-sided rather than a spinning top."),
                Param("marubozu_body_min", "Marubozu Body Min (body/range)", 0.9,
                      "float", 0.5, 1.0, 0.01,
                      "Minimum body/range for a marubozu."),
                Param("tweezer_tol_atr", "Tweezer Tolerance (xATR)", 0.05, "float",
                      0.0, 1.0, 0.01,
                      "How closely two highs (or lows) must match, in ATRs, to "
                      "count as a tweezer."),
                Param("engulf_mode", "Engulf Mode", "Body", "enum",
                      options=["Body", "Body+Wick"],
                      help="Body: bar i's body covers bar i-1's body (the standard "
                           "definition). Body+Wick also demands its whole range "
                           "cover the previous bar's — a much rarer, stricter bar."),
                Param("min_range_atr", "Min Bar Range (xATR)", 0.5, "float",
                      0.0, 5.0, 0.05,
                      "The signal bar's high-low range must be at least this many "
                      "ATRs, so a pattern printed on a dead bar is ignored."),
            ]),
            ParamGroup("Prior Move", [
                Param("require_prior_move", "Require Prior Move?", True, "bool",
                      help="Demand context: a pattern needs a move to act on. A "
                           "hammer in a flat tape reverses nothing."),
                Param("prior_move_logic", "Prior Move Logic", "Textbook", "enum",
                      options=["Textbook", "Extension", "Reversal"],
                      help="Which way the prior move must point. Textbook: against "
                           "a reversal pattern, with a continuation one — the "
                           "classical reading. Extension: with the pattern, for "
                           "every family (the bar caps a move already under way). "
                           "Reversal: against the pattern, for every family. A doji "
                           "is defined as opposing the move, so it never fires "
                           "under Extension."),
                Param("prior_move_bars", "Prior Move Lookback (bars)", 12, "int",
                      1, 500, 1,
                      "How many bars the prior move is measured over, ending at the "
                      "bar BEFORE the signal bar (12 bars = 1h on 5m)."),
                Param("prior_move_atr", "Min Prior Move (xATR)", 1.0, "float",
                      0.0, 20.0, 0.1,
                      "How big that move must be, in ATRs, close to close."),
            ]),
            ParamGroup("Volatility Filter", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also measures bar range / prior move and sizes "
                      "TP/SL (xATR)."),
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
                      help="With Trend: long above / short below the MA. Against "
                           "Trend: the opposite."),
                Param("ma_type", "MA Type", "EMA", "enum", options=ind.MA_TYPES,
                      help="Moving-average type for the trend filter."),
                Param("ma_length", "MA Length", 200, "int", 2, 1000, 1,
                      "Lookback for the trend MA."),
                Param("source", "Source", "close", "enum", options=ind.SOURCES,
                      help="Price source for the trend MA."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Pattern", "enum",
                      options=["Pattern", "Fade"],
                      help="Pattern takes the textbook side (a bullish engulfing "
                           "buys); Fade takes the opposite. Exposed because the "
                           "textbook side is a hypothesis, not a given."),
            ]),
            ParamGroup("Day of Week (UTC)", [
                Param(_DAYS[i], lbl, True, "bool",
                      help=f"Allow entries on {lbl} (UTC).")
                for i, lbl in enumerate(("Monday", "Tuesday", "Wednesday",
                                         "Thursday", "Friday", "Saturday",
                                         "Sunday"))
            ]),
        ]

    def presets(self) -> dict:
        return PRESETS

    def generate_signals(self, candles: List[dict], params: dict) -> List[Signal]:
        p = self.resolve_params(params)
        n = len(candles)
        if n == 0:
            return []

        enabled = [k for k in PATTERNS if p[k]]
        if not enabled:
            return []
        rev_on = [k for k in enabled if PATTERNS[k][1] == "rev"]
        cont_on = [k for k in enabled if PATTERNS[k][1] == "cont"]

        atr = ind.atr(candles, p["vol_atr_length"])

        # Per-bar geometry, computed once. `body` keeps its sign (colour); the
        # ratios are all fractions of the bar's own range, so they are
        # scale-free across nine years of price levels.
        op = [c["open"] for c in candles]
        hi = [c["high"] for c in candles]
        lo = [c["low"] for c in candles]
        cl = [c["close"] for c in candles]
        body = [cl[i] - op[i] for i in range(n)]
        rng = [hi[i] - lo[i] for i in range(n)]
        body_top = [max(op[i], cl[i]) for i in range(n)]
        body_bot = [min(op[i], cl[i]) for i in range(n)]
        br = [(abs(body[i]) / rng[i]) if rng[i] > 0 else 0.0 for i in range(n)]
        upper = [(hi[i] - body_top[i]) / rng[i] if rng[i] > 0 else 0.0 for i in range(n)]
        lower = [(body_bot[i] - lo[i]) / rng[i] if rng[i] > 0 else 0.0 for i in range(n)]

        strong = p["body_strong_min"]
        small = p["body_small_max"]
        doji_max = p["doji_body_max"]
        wick_min = p["pin_wick_min"]
        wick_opp = p["pin_opp_wick_max"]
        maru_min = p["marubozu_body_min"]
        tweez_tol = p["tweezer_tol_atr"]
        full_engulf = p["engulf_mode"] == "Body+Wick"
        min_range = p["min_range_atr"]

        need_move = p["require_prior_move"]
        move_logic = p["prior_move_logic"]
        move_bars = p["prior_move_bars"]
        move_atr = p["prior_move_atr"]

        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        use_trend = p["use_trend_filter"]
        with_trend = p["trend_logic"] == "With Trend"
        trend_ma = ind.ma(ind.price_source(candles, p["source"]),
                          p["ma_type"], p["ma_length"]) if use_trend else [None] * n
        take_pattern = p["predict_direction"] == "Pattern"

        allowed_days = {i for i in range(7) if p[_DAYS[i]]}
        gate_days = len(allowed_days) < 7

        signals: List[Signal] = []
        for i in range(2, n):
            a = atr[i]
            if a is None or a <= 0 or rng[i] <= 0:
                continue
            if rng[i] < min_range * a:
                continue

            # Prior move over the bars ending at i-1, in ATRs. Signed: positive
            # is a rally into the pattern. Also the doji's only source of
            # direction.
            j = i - 1 - move_bars
            move = (cl[i - 1] - cl[j]) / a if j >= 0 else None

            # --- pattern matching -> +1 bullish / -1 bearish per family ------
            hits: List[tuple] = []   # (pattern_key, direction, kind)

            for key in rev_on:
                d = 0
                if key == "pat_engulfing":
                    if br[i] >= strong:
                        if body[i] > 0 and body[i - 1] < 0 \
                                and op[i] <= body_bot[i - 1] and cl[i] >= body_top[i - 1] \
                                and (not full_engulf or (hi[i] >= hi[i - 1] and lo[i] <= lo[i - 1])):
                            d = 1
                        elif body[i] < 0 and body[i - 1] > 0 \
                                and op[i] >= body_top[i - 1] and cl[i] <= body_bot[i - 1] \
                                and (not full_engulf or (hi[i] >= hi[i - 1] and lo[i] <= lo[i - 1])):
                            d = -1
                elif key == "pat_hammer":
                    if br[i] <= small:
                        if lower[i] >= wick_min and upper[i] <= wick_opp:
                            d = 1                      # hammer: sellers rejected
                        elif upper[i] >= wick_min and lower[i] <= wick_opp:
                            d = -1                     # shooting star
                elif key == "pat_harami":
                    if br[i - 1] >= strong:
                        inside = (body_top[i] <= body_top[i - 1]
                                  and body_bot[i] >= body_bot[i - 1])
                        if inside and body[i - 1] < 0 and body[i] > 0:
                            d = 1
                        elif inside and body[i - 1] > 0 and body[i] < 0:
                            d = -1
                elif key == "pat_piercing":
                    if br[i - 1] >= strong and br[i] >= strong:
                        mid = (op[i - 1] + cl[i - 1]) / 2.0
                        if body[i - 1] < 0 and body[i] > 0 \
                                and op[i] <= cl[i - 1] and mid < cl[i] < op[i - 1]:
                            d = 1
                        elif body[i - 1] > 0 and body[i] < 0 \
                                and op[i] >= cl[i - 1] and op[i - 1] < cl[i] < mid:
                            d = -1
                elif key == "pat_star":
                    if br[i - 2] >= strong and br[i - 1] <= small and br[i] >= strong:
                        mid = (op[i - 2] + cl[i - 2]) / 2.0
                        if body[i - 2] < 0 and body[i] > 0 \
                                and body_top[i - 1] <= cl[i - 2] and cl[i] >= mid:
                            d = 1
                        elif body[i - 2] > 0 and body[i] < 0 \
                                and body_bot[i - 1] >= cl[i - 2] and cl[i] <= mid:
                            d = -1
                elif key == "pat_doji":
                    # No colour of its own: a doji is read as the prior move
                    # running out of buyers (or sellers).
                    if br[i] <= doji_max and move is not None and move != 0:
                        d = -1 if move > 0 else 1
                elif key == "pat_tweezer":
                    tol = tweez_tol * a
                    if body[i - 1] < 0 and body[i] > 0 and abs(lo[i] - lo[i - 1]) <= tol:
                        d = 1
                    elif body[i - 1] > 0 and body[i] < 0 and abs(hi[i] - hi[i - 1]) <= tol:
                        d = -1
                if d:
                    hits.append((key, d, "rev"))

            for key in cont_on:
                d = 0
                if key == "pat_marubozu":
                    if br[i] >= maru_min:
                        d = 1 if body[i] > 0 else -1 if body[i] < 0 else 0
                elif key == "pat_soldiers":
                    if br[i] >= strong and br[i - 1] >= strong and br[i - 2] >= strong:
                        if body[i] > 0 and body[i - 1] > 0 and body[i - 2] > 0 \
                                and cl[i] > cl[i - 1] > cl[i - 2] \
                                and op[i] <= cl[i - 1] and op[i - 1] <= cl[i - 2]:
                            d = 1
                        elif body[i] < 0 and body[i - 1] < 0 and body[i - 2] < 0 \
                                and cl[i] < cl[i - 1] < cl[i - 2] \
                                and op[i] >= cl[i - 1] and op[i - 1] >= cl[i - 2]:
                            d = -1
                if d:
                    hits.append((key, d, "cont"))

            if not hits:
                continue

            # --- context gate: does the pattern have something to act on? ----
            if need_move:
                if move is None:
                    continue
                kept = []
                for key, d, kind in hits:
                    # `want` is the sign the prior move must carry.
                    if move_logic == "Extension":
                        want = d
                    elif move_logic == "Reversal":
                        want = -d
                    else:      # Textbook: reversal wants it against, cont with
                        want = -d if kind == "rev" else d
                    if want > 0 and move >= move_atr:
                        kept.append((key, d, kind))
                    elif want < 0 and move <= -move_atr:
                        kept.append((key, d, kind))
                hits = kept
                if not hits:
                    continue

            # Disagreement between enabled families is information, not noise to
            # be majority-voted away: drop the bar (same rule as Combined).
            dirs = {d for _, d, _ in hits}
            if len(dirs) > 1:
                continue
            pat_dir = dirs.pop()

            # --- generic filters ---------------------------------------------
            if gate_days and datetime.fromtimestamp(
                    candles[i]["time"], timezone.utc).weekday() not in allowed_days:
                continue
            atr_pct = a / cl[i] * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            side = ("long" if pat_dir > 0 else "short") if take_pattern else \
                   ("short" if pat_dir > 0 else "long")

            if use_trend:
                tm = trend_ma[i]
                if tm is None:
                    continue
                above = cl[i] > tm
                agree = (side == "long" and above) or (side == "short" and not above)
                if agree != with_trend:
                    continue

            names = [PATTERNS[k][0] for k, _, _ in hits]
            mode = "pattern" if take_pattern else "fade"
            reason = (f"{'Bullish' if pat_dir > 0 else 'Bearish'} "
                      + " + ".join(names)
                      + (f" after {move:+.1f}xATR move" if move is not None else "")
                      + f" -> {mode} {side.upper()} (ATR% {atr_pct:.2f})")

            signals.append(Signal(
                index=i, time=candles[i]["time"], side=side, price=cl[i],
                reason=reason, atr=a,
                meta={"patterns": [k for k, _, _ in hits],
                      "pattern_dir": pat_dir,
                      "prior_move_atr": round(move, 2) if move is not None else None,
                      "body_ratio": round(br[i], 3),
                      "range_atr": round(rng[i] / a, 2),
                      "atr_pct": round(atr_pct, 3), "mode": mode},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m). Exit / Backtest params are unused in that mode.
#
# Sweep: BTCUSDT 1m resampled to 5m over the entire DB — 939,434 bars,
# 2017-08-17 .. 2026-07-28 — in five stages totalling ~3,000 combinations
# (117 family x context + 160 context + 1,872 geometry + 504 family
# combinations + 225 filter). Every combination was scored by calling this
# module's own `generate_signals`, so a preset cannot drift from what the
# dashboard runs.
#
# Parameters were selected on **2017-2023 only**; 2024-2026 was scored
# afterwards. The holdout column was displayed during stage 2 and then switched
# off for stages 3-5, where the shapes and filters were actually chosen — so
# treat it as a very good shrinkage estimate rather than a perfectly blind one.
#
# Measured results (whole DB, flat $1 per bet)
# --------------------------------------------
#   preset       bets     hit   train 17-23   TEST 24-26  2025-26  worst yr    z
#   Volume     36,079  56.99%      58.67%       54.59%     54.32%   48.33%   26.5
#   Balanced   13,501  58.41%      60.61%       55.48%     55.44%   47.47%   19.5
#   Selective   5,279  58.97%      61.65%       55.57%     54.94%   52.79%   13.0
#   Hi Hit      1,472  59.78%      63.23%       55.17%     56.38%   48.98%    7.5
#
# Every worst year is 2017 — a partial year (Aug-Dec) and the most relentlessly
# trending stretch in the record, which is exactly the regime that punishes
# fading a bar that extends a move. From 2018 on, Balanced's worst year is 55.0%.
#
# **Balanced is the preset to use.** All four survive the holdout, and their
# holdout hit rates are within 1pp of each other (54.59 / 55.48 / 55.57 /
# 55.17%) — so the tier that carries the most bets at the top of that band wins
# on evidence rather than on headline number.
#
# FINDING 1: THE TEXTBOOK READING IS BACKWARDS
# ---------------------------------------------
# Every family was tested in both prior-move contexts — the classical
# "reversal" reading (the pattern contradicts the move into it) and the
# "extension" reading (the pattern caps a move already under way) — with the
# bet in each case placed AGAINST the prior move, so the two are directly
# comparable. Train hit rate, prior move >= 1.0xATR over 12 bars:
#
#     family        reversal ctx        extension ctx      control = 52.18%
#     soldiers    43.00% (   614)      58.21% ( 2,723)
#     marubozu    45.83% (12,319)      56.94% (10,524)
#     star        48.02% ( 2,353)      56.87% (   932)
#     engulfing   48.68% (26,659)      55.14% (16,959)
#     piercing    49.51% ( 8,738)      53.62% ( 3,264)
#     hammer      53.21% (25,835)      53.10% (22,574)
#     harami      51.47% (33,216)      52.38% (12,701)
#     tweezer     50.45% (24,014)      52.05% (13,880)
#     doji        53.13% (34,367)         n/a (undefined)
#
# The classical reversal reading is at or BELOW the 52.18% control for six of
# the nine families, and catastrophically so for the two that are supposed to be
# the most decisive: three white soldiers after a decline is 43.00%, a marubozu
# against the move 45.83%. The extension reading beats the control for all eight
# families where it is defined. Only the two wick families — hammer and doji —
# earn anything in the direction the textbook says, and they earn about a point.
#
# Read plainly: a bullish engulfing bar is not a bottom. It is a big green
# candle, and a big green candle at the end of a rally is a good thing to sell.
#
# FINDING 2: MOST OF THE EDGE IS THE CONTEXT, NOT THE SHAPE
# ----------------------------------------------------------
# Because every winning configuration bets against the immediately preceding
# move, the shapes were tested against a tight matched control: same prior-move
# gate, same bar-range floor, same volatility band, fading the bar's own
# direction — but with NO body-ratio requirement, so any bar that extends the
# move qualifies:
#
#   preset config              control bets    hit  |  preset bets    hit    gap
#   6b/1.0xATR/rng0.5              184,532  55.16%  |     36,079  56.99%  +1.83
#   6b/1.5xATR/rng1.0               60,246  56.74%  |     13,501  58.41%  +1.67
#   12b/2.0xATR/rng1.5              24,118  57.12%  |      5,279  58.97%  +1.85
#
# Against the DISJOINT remainder of each control (the bars the preset does not
# take), the geometry is worth +2.27 / +2.15 / +2.37pp at z = +7.8 / +4.4 /
# +3.1. So the candlestick shape is real and statistically solid — and it is
# roughly a fifth of the story. The other four fifths is "fade a decisive bar
# that extends a move", which needs no pattern vocabulary at all. If you only
# want the effect and not the taxonomy, the control is simpler and has 5x the
# volume at 1-2pp less.
#
# Other findings
# --------------
#   * **Fade, always.** No configuration of any family beat 50% betting the
#     pattern's own direction once the context gate was set correctly.
#   * **The trend filter and the volatility band earn nothing.** Across the 225
#     filter combinations, "Against SMA50" beat filter-off by 0.0-0.2pp and the
#     ATR% band by ~0.3pp — inside noise at every volume. Both ship OFF/wide,
#     unlike Fib Retracement and Volume Exhaustion where Against Trend paid.
#   * **Body-only engulfment beats whole-range.** `engulf_mode = "Body+Wick"`
#     appears nowhere in the top 20 of its 288-combination grid.
#   * **A big bar matters more than a pretty one.** `min_range_atr` is the
#     single most valuable geometry knob: pushing it from 0.0 to 1.5xATR adds
#     3-4pp across every family. `marubozu_body_min` between 0.75 and 0.90
#     barely separates (61.0-61.9%).
#   * **No weekend gate.** Held fixed and split by UTC day, the premium is
#     +0.27 / +1.28 / +0.59 / +7.46pp on the four tiers. Only Hi Hit's is
#     nominally significant (z = +2.69) — one result out of four comparisons on
#     424 weekend bets. No weekend presets are shipped.
#
# Caveats
# -------
# 1. **Shrinkage scales with training hit rate, again.** Ranked by train hit,
#    the four tiers lose 4.1 / 5.1 / 6.1 / 8.1 points out-of-sample, in exactly
#    that order — the same pattern Fib Retracement showed. Treat any in-sample
#    number here as an upper bound.
# 2. **The edge decays.** 58.67% on train, 54.59% on the holdout, 54.32% over
#    2025-26 for Volume. Every tier's 2026 is below its 2018-2023 average.
# 3. **2017 loses** on three of four tiers. A parabolic trend is this
#    strategy's failure mode, and it will recur.
# 4. **Read Finding 2 before treating the pattern vocabulary as the source of
#    the edge.** It is worth about 2pp of the ~57%.
# 5. Days are UTC and a bar is stamped by its open time.
#
# A bet pays only when hit rate > your odds: Balanced's 55.48% holdout needs an
# entry below ~0.5548 to be +EV, before fees.
PRESETS: dict = {
    # 36,079 bets (~11/day), 56.99% hit; 54.59% across 14,868 out-of-sample
    # bets. The widest net and the thinnest margin — check your entry price.
    "PM 5m Volume": {
        "pat_engulfing": False, "pat_hammer": False, "pat_harami": False,
        "pat_piercing": False, "pat_star": False, "pat_doji": False,
        "pat_tweezer": False, "pat_marubozu": True, "pat_soldiers": False,
        "body_strong_min": 0.5, "body_small_max": 0.3, "doji_body_max": 0.1,
        "pin_wick_min": 0.5, "pin_opp_wick_max": 0.2,
        "marubozu_body_min": 0.75, "tweezer_tol_atr": 0.05,
        "engulf_mode": "Body", "min_range_atr": 0.5,
        "require_prior_move": True, "prior_move_logic": "Extension",
        "prior_move_bars": 6, "prior_move_atr": 1.0,
        "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Fade",
    },
    # *** THE PICK. *** 13,501 bets (~4/day), 58.41% hit; 55.48% across 5,793
    # out-of-sample bets, 55.44% over 2025-26, and every year from 2018 on at
    # or above 55.0%. Holds the top of the holdout band while carrying 2.5x the
    # bets of the tiers beside it.
    "PM 5m Balanced": {
        "pat_engulfing": False, "pat_hammer": False, "pat_harami": False,
        "pat_piercing": False, "pat_star": False, "pat_doji": False,
        "pat_tweezer": False, "pat_marubozu": True, "pat_soldiers": False,
        "body_strong_min": 0.5, "body_small_max": 0.3, "doji_body_max": 0.1,
        "pin_wick_min": 0.5, "pin_opp_wick_max": 0.2,
        "marubozu_body_min": 0.75, "tweezer_tol_atr": 0.05,
        "engulf_mode": "Body", "min_range_atr": 1.0,
        "require_prior_move": True, "prior_move_logic": "Extension",
        "prior_move_bars": 6, "prior_move_atr": 1.5,
        "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Fade",
    },
    # 5,279 bets (~1.6/day), 58.97% hit, 55.57% out-of-sample — nominally the
    # best holdout of the four, on 2,325 bets against Balanced's 5,793. A longer,
    # larger prior move and a bigger bar; the same idea, more strictly gated.
    "PM 5m Selective": {
        "pat_engulfing": False, "pat_hammer": False, "pat_harami": False,
        "pat_piercing": False, "pat_star": False, "pat_doji": False,
        "pat_tweezer": False, "pat_marubozu": True, "pat_soldiers": False,
        "body_strong_min": 0.5, "body_small_max": 0.3, "doji_body_max": 0.1,
        "pin_wick_min": 0.5, "pin_opp_wick_max": 0.2,
        "marubozu_body_min": 0.8, "tweezer_tol_atr": 0.05,
        "engulf_mode": "Body", "min_range_atr": 1.5,
        "require_prior_move": True, "prior_move_logic": "Extension",
        "prior_move_bars": 12, "prior_move_atr": 2.0,
        "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Fade",
    },
    # 1,472 bets (~165/year), 59.78% hit — the highest headline number and the
    # least trustworthy: 63.23% train against 55.17% holdout is the worst
    # shrinkage of the four, and 629 out-of-sample bets is not much to rest on.
    # The one tier built on engulfing rather than marubozu. NOT RECOMMENDED;
    # shipped to show where the frontier ends.
    "PM 5m Hi Hit": {
        "pat_engulfing": True, "pat_hammer": False, "pat_harami": False,
        "pat_piercing": False, "pat_star": False, "pat_doji": False,
        "pat_tweezer": False, "pat_marubozu": False, "pat_soldiers": False,
        "body_strong_min": 0.7, "body_small_max": 0.3, "doji_body_max": 0.1,
        "pin_wick_min": 0.5, "pin_opp_wick_max": 0.2,
        "marubozu_body_min": 0.9, "tweezer_tol_atr": 0.05,
        "engulf_mode": "Body", "min_range_atr": 1.5,
        "require_prior_move": True, "prior_move_logic": "Extension",
        "prior_move_bars": 6, "prior_move_atr": 1.5,
        "vol_atr_length": 14, "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Fade",
    },
}
