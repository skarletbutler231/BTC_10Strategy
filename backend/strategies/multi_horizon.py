"""Strategy #10 from the video: Multi Horizon  ("agreement across timeframes").

Idea
----
One lookback window only ever tells you one story. A close can look wildly
stretched against the last hour and perfectly ordinary against the last twelve —
and a signal built on either window alone cannot tell those two situations
apart. This strategy measures the *same* quantity at three horizons at once and
only acts when enough of them agree.

The quantity is a **z-score**: how many standard deviations the close sits from
its own mean over that horizon.

    z(h) = (close - SMA(close, h)) / stdev(close, h)

Because it is expressed in that horizon's own sigmas, z is directly comparable
across horizons and across the 2017-2026 price range — a 2-sigma stretch means
the same thing at $4k and at $120k. Defaults of 12 / 48 / 144 bars are 1h / 4h /
12h on the 5m interval.

Agreement is the whole point. `min_agree` sets how many of the three horizons
must be stretched past `z_threshold`, and they must all lean the SAME way — if
any horizon is stretched the other way the bar is skipped, because that is a
conflict, not a signal. `require_fast` additionally insists the fast horizon be
one of the agreeing ones, which restricts entries to moves that are stretched
*right now* rather than merely stretched an hour ago.

Entry timing
------------
The stretch tells you *what* to bet; it does not tell you *when*. `require_opposing_bar`
adds that second question: only bet when the signal bar itself closed AGAINST the
bet — for a reversion SHORT, the bar must still be pushing up. If the bar has
already turned your way the reversal has begun without you, and the measured edge
on those entries collapses to a coin flip (see the numbers below the presets).
`opposing_bar_min_atr` optionally demands that opposing bar have some real body.

Parameter groups
----------------
Horizons     h_fast, h_mid, h_slow
Signal       z_threshold, min_agree, require_fast
Volatility   vol_atr_length, atr_pct_min, atr_pct_max
Entry Timing require_opposing_bar, opposing_bar_min_atr
Trend        use_trend_filter, trend_logic, ma_type, ma_length, source
Decision     predict_direction  (Reversion | Continuation)

Entry logic
-----------
  1. Count horizons with z >= +z_threshold (n_up) and z <= -z_threshold (n_dn).
  2. Require n_up >= min_agree and n_dn == 0  -> stretched UP (mirror for DOWN).
  3. If require_fast, the fast horizon must be among the stretched ones.
  4. Volatility regime: ATR%(vol_atr_length) within [atr_pct_min, atr_pct_max].
  5. If require_opposing_bar, the signal bar must close against the bet, by at
     least opposing_bar_min_atr x ATR.
  6. Optional trend-filter agreement.

**Reversion** fades the stretch (SHORT an up-stretch); **Continuation** rides it.
The Vol ATR sizes TP/SL; in Polymarket up/down mode the exit params are unused.
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


class MultiHorizon(Strategy):
    id = "multi_horizon"
    name = "Multi Horizon"
    description = ("Measures how stretched the close is at three horizons at once "
                   "(z-score vs each horizon's own mean) and acts only when enough "
                   "of them agree, with volatility and trend filters.")

    def param_groups(self) -> List[ParamGroup]:
        return [
            ParamGroup("Horizons", [
                Param("h_fast", "Fast Horizon (bars)", 12, "int", 2, 500, 1,
                      "Shortest lookback for the z-score (12 bars = 1h on 5m)."),
                Param("h_mid", "Mid Horizon (bars)", 48, "int", 2, 1000, 1,
                      "Middle lookback (48 bars = 4h on 5m)."),
                Param("h_slow", "Slow Horizon (bars)", 144, "int", 2, 2000, 1,
                      "Longest lookback (144 bars = 12h on 5m)."),
            ]),
            ParamGroup("Signal", [
                Param("z_threshold", "Z Threshold", 2.0, "float", 0.5, 8.0, 0.1,
                      "A horizon counts as stretched when |z| reaches this many sigma."),
                Param("min_agree", "Min Horizons Agreeing", 2, "int", 1, 3, 1,
                      "How many of the three horizons must be stretched the same way. "
                      "Any horizon stretched the opposite way vetoes the bar."),
                Param("require_fast", "Require Fast Horizon", False, "bool",
                      help="Insist the fast horizon be one of the agreeing ones, so "
                           "the move is stretched now rather than an hour ago."),
            ]),
            ParamGroup("Volatility Filter", [
                Param("vol_atr_length", "Vol ATR Length", 14, "int", 2, 200, 1,
                      "ATR lookback; also sizes TP/SL (xATR) for this strategy."),
                Param("atr_pct_min", "ATR% Min", 0.05, "float", 0.0, 5.0, 0.01,
                      "Skip signals below this ATR-as-%-of-price (dead tape)."),
                Param("atr_pct_max", "ATR% Max", 1.5, "float", 0.05, 20.0, 0.01,
                      "Skip signals above this ATR% (violent regime)."),
            ]),
            ParamGroup("Entry Timing", [
                Param("require_opposing_bar", "Signal Bar Opposes Bet", False, "bool",
                      help="Only take the bet when the signal bar itself closed against "
                           "it — for a reversion SHORT the bar must still be pushing up. "
                           "Skips entries where the reversal already started."),
                Param("opposing_bar_min_atr", "Min Opposing Body (xATR)", 0.0, "float",
                      0.0, 3.0, 0.05,
                      "Also require that opposing bar's body be at least this multiple "
                      "of the Vol ATR. 0 accepts any opposing bar. Needs the box above."),
            ]),
            ParamGroup("Trend Filter", [
                Param("use_trend_filter", "Use Trend Filter?", False, "bool",
                      help="Require price to agree with a moving-average trend."),
                Param("trend_logic", "Trend Logic", "With Trend", "enum",
                      options=["With Trend", "Against Trend"],
                      help="With Trend: long above / short below the MA. Against Trend: the opposite."),
                Param("ma_type", "MA Type", "EMA", "enum", options=ind.MA_TYPES,
                      help="Moving-average type for the trend filter."),
                Param("ma_length", "MA Length", 200, "int", 2, 1000, 1,
                      "Lookback for the trend MA."),
                Param("source", "Source", "close", "enum", options=ind.SOURCES,
                      help="Price source for the trend MA."),
            ]),
            ParamGroup("Decision", [
                Param("predict_direction", "Predict Direction", "Reversion", "enum",
                      options=["Reversion", "Continuation"],
                      help="Reversion fades the stretch; Continuation rides it."),
            ]),
            ParamGroup("Day of Week (UTC)", [
                Param(_DAYS[i], lbl, True, "bool",
                      help=f"Allow entries on {lbl} (UTC). See the Polymarket "
                           f"presets -- the weekend premium here is weak and has "
                           f"faded, unlike in Jump Exhaustion or CCI Williams.")
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

        # Day gate (UTC). Index matches datetime.weekday(): Monday == 0.
        allowed_days = {i for i in range(7) if p[_DAYS[i]]}
        gate_days = len(allowed_days) < 7

        closes = [c["close"] for c in candles]
        horizons = [p["h_fast"], p["h_mid"], p["h_slow"]]
        zs = []
        for h in horizons:
            basis = ind.sma(closes, h)
            sd = ind.rolling_std(closes, h)
            z: List = [None] * n
            for i in range(n):
                if basis[i] is None or sd[i] is None or sd[i] <= 0:
                    continue
                z[i] = (closes[i] - basis[i]) / sd[i]
            zs.append(z)

        atr_vol = ind.atr(candles, p["vol_atr_length"])
        use_trend = p["use_trend_filter"]
        trend_ma = ind.ma(ind.price_source(candles, p["source"]),
                          p["ma_type"], p["ma_length"]) if use_trend else [None] * n

        thr = p["z_threshold"]
        min_agree = p["min_agree"]
        require_fast = p["require_fast"]
        ap_min, ap_max = p["atr_pct_min"], p["atr_pct_max"]
        with_trend = p["trend_logic"] == "With Trend"
        reversion = p["predict_direction"] == "Reversion"
        opposing_bar = p["require_opposing_bar"]
        opposing_min = p["opposing_bar_min_atr"]

        signals: List[Signal] = []
        for i, c in enumerate(candles):
            if gate_days and datetime.fromtimestamp(
                    c["time"], timezone.utc).weekday() not in allowed_days:
                continue
            a = atr_vol[i]
            if a is None or a <= 0:
                continue
            zv = [z[i] for z in zs]
            if any(v is None for v in zv):
                continue

            n_up = sum(1 for v in zv if v >= thr)
            n_dn = sum(1 for v in zv if v <= -thr)
            if n_up >= min_agree and n_dn == 0:
                extreme = "up"
            elif n_dn >= min_agree and n_up == 0:
                extreme = "down"
            else:
                continue

            if require_fast:
                zf = zv[0]
                if extreme == "up" and zf < thr:
                    continue
                if extreme == "down" and zf > -thr:
                    continue

            cl = c["close"]
            atr_pct = a / cl * 100.0
            if atr_pct < ap_min or atr_pct > ap_max:
                continue

            side = ("short" if extreme == "up" else "long") if reversion \
                else ("long" if extreme == "up" else "short")

            # The bar must still be moving against the bet — once it has turned
            # our way the reversal is already under way and the edge is gone.
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
                above = cl > tm
                agree = (side == "long" and above) or (side == "short" and not above)
                if with_trend and not agree:
                    continue
                if not with_trend and agree:
                    continue

            mode = "reversion" if reversion else "continuation"
            agreeing = n_up if extreme == "up" else n_dn
            reason = (f"{agreeing}/3 horizons {extreme.upper()}-stretched "
                      f"(z {zv[0]:+.1f}/{zv[1]:+.1f}/{zv[2]:+.1f}) "
                      f"-> {mode} {side.upper()} (ATR% {atr_pct:.2f})")

            signals.append(Signal(
                index=i, time=c["time"], side=side, price=cl, reason=reason, atr=a,
                meta={"z_fast": round(zv[0], 2), "z_mid": round(zv[1], 2),
                      "z_slow": round(zv[2], 2), "agree": agreeing,
                      "atr_pct": round(atr_pct, 3), "extreme": extreme, "mode": mode,
                      "bar_body_atr": round(body / a, 2)},
            ))
        return signals


# ---------------------------------------------------------------------------
# Presets for **Polymarket up/down** mode (Mode = "Polymarket up/down",
# interval = 5m). Exit / Backtest params are unused in that mode.
#
# Sweep: BTCUSDT 1m resampled to 5m over the entire DB — 936,829 bars,
# 2017-08-17 .. 2026-07-19. Same admission rules as the other two Polymarket-tuned
# strategies: win EVERY calendar year, clear 53% over 2024-2026 on its own, be
# binomially significant on both spans.
#
# Re-swept once `require_opposing_bar` existed, with the filter INSIDE the loop
# (672k combinations) so it could shape the parameters rather than be bolted onto
# a set chosen without it. That re-sweep selected on 2017-2023 only and scored on
# 2024-2026, making the recent column genuinely out-of-sample. Findings:
#
#   * Train hit rate is informative but optimistic — the top 50 configs by
#     2017-2023 hit average 63.2% there and 60.8% on 2024-26, so budget ~3pp of
#     shrinkage on any in-sample number.
#   * Four of the five presets were already at the out-of-sample frontier: no
#     train-selected config beat Balanced, Selective, Hi Hit or Max Hit at equal
#     bet count (-0.3 to -2.7pp). They are kept unchanged.
#   * **Volume was not**, and was replaced. The new one beats the old by +2.1pp
#     out-of-sample on MORE bets — see the preset comment below.
#   * `opposing_bar_min_atr` earns its keep after all, but only when the other
#     parameters are free to adapt: 21 of the 25 best Volume-tier configs ask for
#     an opposing body of 0.50-0.75 x ATR. Bolted onto the pre-existing presets it
#     did nothing (that test is still the reason it stays 0 in the other four).
#
# Three findings worth more than the numbers
# -------------------------------------------
#   * **Reversion only, again.** All 4,304 combinations that passed were
#     Reversion; zero Continuation. That now holds across three independent
#     strategies on this data — on BTC 5m, stretch reverts. The 2026 re-sweep
#     confirms it emphatically: Continuation configs CAN win every training year
#     (28,789 of them did), but not one appears in the top 5,000 by training hit,
#     and not one of the 40,330 configs clearing 53% over 2024-26 on 1,000+ bets
#     is Continuation. 90% of those 40,330 have require_opposing_bar ON.
#   * **The veto matters more than the agreement.** The best configurations use
#     `min_agree = 1` together with `require_fast` — i.e. they do NOT demand that
#     several horizons line up. What earns the edge is the *other* half of the
#     rule: no horizon may be stretched the opposite way. Multi-horizon pays off
#     as a conflict filter, not as a confirmation stack.
#   * **"With Trend" here, unlike Volume Exhaustion.** Combined with Reversion it
#     resolves to buying a down-stretch while price is above the MA — buy the dip
#     in an uptrend. (Volume Exhaustion preferred Against Trend; these are
#     different setups and there is no contradiction.)
#
# Entry timing: `require_opposing_bar` (added after the original sweep)
# --------------------------------------------------------------------
# Every preset below now sets `require_opposing_bar = True`. The rule is one
# comparison — the signal bar must close AGAINST the bet — and it splits the old
# signal set cleanly in two:
#
#   preset      kept   kept hit    dropped   dropped hit   two-proportion z
#   Volume    44,971    57.63%      53,947      53.69%         +12.42
#   Balanced  38,497    57.78%       1,845      51.22%          +5.56
#   Selective 20,635    57.82%       1,071      50.42%          +4.78
#   Hi Hit     7,825    59.41%         490      52.24%          +3.13
#   Max Hit    3,511    61.63%         287      50.52%          +3.71
#
# The dropped bucket is a coin flip in all five presets — betting a reversion
# after the bar has already turned is not a thin edge, it is no edge. Rejecting
# it lifts the hit rate in 47 of the 50 preset-years, and the three exceptions are
# all under 0.7pp. Costs 5-8% of bets on four presets, 24% on Volume.
#
# Why NOT to skip windows after a loss (tested, it loses)
# -------------------------------------------------------
# Consecutive losing windows are conspicuous in a backtest, and runs of them are
# genuinely longer than chance: shuffling outcomes within each run of neighbouring
# signals, loss-runs of >=3 come out z=+2.3 to +20 above the shuffled null. The
# natural reaction is to skip a window whose predecessor was a neighbour, pointed
# the same way, and lost. That has been measured and it makes things WORSE.
#
# The reason is that a run of neighbouring signals exists *because* the bet kept
# losing. A win resolves the stretch, so the next bar no longer fires — the win is
# what ENDS the run. Runs are therefore shaped loss, loss, ..., win:
#
#   preset      runs (>=2)   first window   middle windows   last window
#   Volume        17,935        20.55%          38.42%          94.18%
#   Balanced       9,428        11.00%          13.37%          84.09%
#   Selective      5,077        11.33%          13.87%          83.38%
#   Hi Hit         1,394        14.56%          26.33%          75.11%
#   Max Hit          706        15.44%          31.09%          75.50%
#
# Skipping everything after a loss keeps the FIRST window of each run and discards
# the rest, including the terminal winner. Concretely it removes the group that
# hits 57-62% (above baseline) and keeps the group that hits 50-53%. Measured over
# 5 presets x 2 readings of "the previous prediction" (last emitted vs last acted
# on) x neighbour windows of 1-3 bars x this filter on/off = 60 configurations:
# hit rate falls in 58 of them, by ~1.1pp on Volume and 0.3-0.4pp elsewhere, while
# discarding 20-35% of the bets. The two exceptions are +0.02pp and +0.06pp on
# Max Hit, the smallest preset — noise.
#
# Note the run-position numbers above are NOT tradeable: you only know a window
# was "last in its run" after seeing it win. What IS tradeable is the predecessor's
# outcome, and that says the opposite of the intuition — a losing bet means the
# stretch grew, so the next bet is stronger; a winning bet means the reversion
# already paid out, so the next one is picking up scraps. `require_opposing_bar`
# above is that same fact in per-bar form, which is why it works.
#
# `opposing_bar_min_atr` tightens it further by demanding a real body on that
# opposing bar. It buys hit rate with sample size and is left at 0 in the presets:
# on Max Hit, k=0.75 reaches 63.06% but on 63% of the bets, and the 2024-26 column
# does not move monotonically with k on any preset, so the extra points look like
# curve-fit rather than signal. Tune it if you want fewer, sharper bets.
#
# Measured results (whole DB, flat $1 per bet, with the filter on)
# ----------------------------------------------------------------
#   preset      bets     hit    2024-26 bets  2024-26 hit  worst yr    z
#   Volume     44,971  57.63%      13,586       55.64%      50.19%    32.4
#   Balanced   38,497  57.78%      10,420       56.31%      50.49%    30.5
#   Selective  20,635  57.82%       3,002       57.76%      50.81%    22.5
#   Hi Hit      7,825  59.41%       1,939       58.48%      54.42%    16.6
#   Max Hit     3,511  61.63%         552       61.41%      55.56%    13.8
#
# (Before any of this work: 55.12 / 57.48 / 57.45 / 58.99 / 60.80 overall and
# 53.12 / 56.25 / 57.65 / 58.26 / 61.00 over 2024-26.) Every preset still clears
# the original admission rules — every calendar year wins, 2024-26 clears 53%.
#
# This is the strongest of the four strategies here. Volume now carries both the
# most bets and the highest z in the repo (32.4) at 55.64% over 2024-26, and its
# 2024-26 number is out-of-sample — it was chosen without the sweep ever seeing
# those years. Balanced holds 56.31% over 10,420 recent bets, and unlike the other
# strategies' high-hit presets, Hi Hit and Max Hit are backed by real samples —
# every year lands between 54.4% and 63.9%, so neither rests on one lucky regime.
#
# Caveats
# -------
# 1. **The edge still decays**, though less sharply than elsewhere: Balanced runs
#    ~57-60% in 2018-2023 against ~56% in 2024-2026. Read the 2024-26 column.
# 2. **2017 is the weak year** (~50%) for the first three; it is a partial year
#    (Aug-Dec) and the "every year wins" rule was binding there. Hi Hit and Max
#    Hit clear it comfortably at 54.4% / 55.6%.
# 3. Selective's ATR% floor makes it volatility-dependent — only 3,002 of its
#    20,635 bets fall in 2024-26, because recent tape is calmer.
# 4. Only Volume comes from the joint re-sweep. The other four keep parameters
#    chosen before `require_opposing_bar` existed, with the filter bolted on —
#    the re-sweep could not beat them out-of-sample at equal bet count, but they
#    are not joint optima either.
# 5. Volume's 2024-26 number is out-of-sample; the other four presets' are not,
#    since those years were part of the original selection. Do not read the four
#    as being on equal footing with Volume's.
#
# A bet pays only when hit rate > your odds: Volume's 55.64% needs entry below
# ~0.5564, Balanced's 56.31% below ~0.5631.
# --- Polymarket 5m, day-aware sweep -----------------------------------------
# Whole DB (936,829 5m bars, 2017-08 .. 2026-07), Polymarket up/down mode. Two
# families of three tiers: all-days and weekend-gated (Sat+Sun, UTC).
#
# Admission: hit >50% in every calendar year THAT HAS AT LEAST 25 BETS, overall
# z >= 2.5, and 2024-26 must still clear 52%. The 25-bet floor matters: 2017 is
# a partial year (Aug-Dec) and on thin presets holds too little to be evidence.
#
#   preset             bets     hit    worst yr  2024-26  2025-26      z
#   Volume            22,127   58.17%     50.35%    56.25%    56.03%   24.3
#   Balanced          12,568   59.06%     51.35%    55.65%    57.16%   20.3
#   Hi Hit             1,049   65.49%     53.57%    66.00%    64.38%   10.0
#   Wknd Volume        5,457   60.42%     51.42%    58.73%    57.92%   15.4
#   Wknd Balanced      3,554   61.23%     52.46%    58.85%    57.75%   13.4
#   Wknd Hi Hit        1,974   61.90%     54.24%    59.96%    61.77%   10.6
#
# THE WEEKEND GATE IS WEAK HERE -- read this before using the Wknd tiers.
# Holding parameters fixed and splitting by day, the weekend premium on the
# previous presets was +1.11 / +1.41pp (z=+2.14 / +2.49) over the full record
# but only +0.89 / +0.38pp (z=+0.95 / +0.35) over 2024-26, i.e. gone. On the
# selective presets it is NEGATIVE and significantly so (old Max Hit, weekend
# 2025-26: -17.11pp, z=-2.00). So unlike Jump Exhaustion or CCI Williams, the
# Wknd tiers below are best read as "parameters fitted to weekend bars", not as
# "the weekend edge" -- the apparent gap vs the all-days tiers is mostly the
# fit, not a day premium.
#
# CAVEAT: selection used the FULL record with NO holdout, so these hit rates
# carry selection bias and the 2024-26 / 2025-26 columns are a recency check,
# not out-of-sample evidence. Days are UTC; a bar is stamped by its open time.
PRESETS: dict = {
    # Balanced plus a high-volatility floor. Fires far less often in calm tape.
    "PM 5m Selective": {
        "h_fast": 24, "h_mid": 72, "h_slow": 216,
        "z_threshold": 2.5, "min_agree": 1, "require_fast": True,
        "vol_atr_length": 50, "atr_pct_min": 0.2, "atr_pct_max": 3.0,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.0,
        "use_trend_filter": False, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Reversion",
    },
    # Best high-hit preset in this repo: 61.6% over 3,511 bets, z=13.8, and every
    # year from 2017 to 2026 lands between 55.6% and 63.9%. ~390 bets/year.
    "PM 5m Max Hit": {
        "h_fast": 6, "h_mid": 24, "h_slow": 72,
        "z_threshold": 2.5, "min_agree": 1, "require_fast": False,
        "vol_atr_length": 50, "atr_pct_min": 0.2, "atr_pct_max": 3.0,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.0,
        "use_trend_filter": True, "trend_logic": "With Trend",
        "ma_type": "EMA", "ma_length": 200, "source": "close",
        "predict_direction": "Reversion",
    },
    # 22,127 bets, 58.17% hit; 2024-26 56.25%, worst year 50.35%.
    "PM 5m Volume": {
        "h_fast": 12, "h_mid": 24, "h_slow": 48, "z_threshold": 3.0,
        "min_agree": 1, "require_fast": False, "vol_atr_length": 14,
        "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.5,
        "use_trend_filter": False, "trend_logic": 'With Trend',
        "ma_type": 'EMA', "ma_length": 200, "source": 'close',
        "predict_direction": 'Reversion',
    },
    # 12,568 bets, 59.06% hit; 2024-26 55.65%, worst year 51.35%.
    "PM 5m Balanced": {
        "h_fast": 12, "h_mid": 24, "h_slow": 96, "z_threshold": 2.0,
        "min_agree": 1, "require_fast": False, "vol_atr_length": 50,
        "atr_pct_min": 0.2, "atr_pct_max": 3.0,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.5,
        "use_trend_filter": True, "trend_logic": 'With Trend',
        "ma_type": 'EMA', "ma_length": 200, "source": 'close',
        "predict_direction": 'Reversion',
    },
    # 1,049 bets, 65.49% hit; 2024-26 66.00%, worst year 53.57%.
    "PM 5m Hi Hit": {
        "h_fast": 12, "h_mid": 24, "h_slow": 48, "z_threshold": 3.0,
        "min_agree": 1, "require_fast": False, "vol_atr_length": 50,
        "atr_pct_min": 0.2, "atr_pct_max": 3.0,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.5,
        "use_trend_filter": True, "trend_logic": 'With Trend',
        "ma_type": 'EMA', "ma_length": 200, "source": 'close',
        "predict_direction": 'Reversion',
    },
    # 5,457 bets, 60.42% hit; 2024-26 58.73%, worst year 51.42%.
    "PM 5m Wknd Volume": {
        "h_fast": 6, "h_mid": 24, "h_slow": 48, "z_threshold": 3.0,
        "min_agree": 1, "require_fast": False, "vol_atr_length": 50,
        "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.75,
        "use_trend_filter": False, "trend_logic": 'With Trend',
        "ma_type": 'EMA', "ma_length": 200, "source": 'close',
        "predict_direction": 'Reversion',
        **_WEEKEND,
    },
    # 3,554 bets, 61.23% hit; 2024-26 58.85%, worst year 52.46%.
    "PM 5m Wknd Balanced": {
        "h_fast": 24, "h_mid": 48, "h_slow": 96, "z_threshold": 3.0,
        "min_agree": 2, "require_fast": False, "vol_atr_length": 50,
        "atr_pct_min": 0.0, "atr_pct_max": 20.0,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.75,
        "use_trend_filter": False, "trend_logic": 'With Trend',
        "ma_type": 'EMA', "ma_length": 200, "source": 'close',
        "predict_direction": 'Reversion',
        **_WEEKEND,
    },
    # 1,974 bets, 61.90% hit; 2024-26 59.96%, worst year 54.24%.
    "PM 5m Wknd Hi Hit": {
        "h_fast": 6, "h_mid": 24, "h_slow": 48, "z_threshold": 3.0,
        "min_agree": 2, "require_fast": False, "vol_atr_length": 14,
        "atr_pct_min": 0.05, "atr_pct_max": 1.5,
        "require_opposing_bar": True, "opposing_bar_min_atr": 0.75,
        "use_trend_filter": True, "trend_logic": 'Against Trend',
        "ma_type": 'EMA', "ma_length": 200, "source": 'close',
        "predict_direction": 'Reversion',
        **_WEEKEND,
    },
}
