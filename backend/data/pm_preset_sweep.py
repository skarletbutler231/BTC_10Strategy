"""Multi-process parameter sweep for Polymarket up/down presets.

Fits presets for one candle interval by scoring every configuration in a
per-strategy grid in the same binary next-candle mode the dashboard uses
(``backend.polymarket.run_binary_backtest``): a signal at the close of bar i is
a bet on the direction of bar i+1, flat candles lose, flat $1 stake.

Method (the one the CHoCH presets were fitted with, applied to every strategy)
-----------------------------------------------------------------------------
* The last two years of Binance BTCUSDT are the fitting window. It is split,
  not used whole: the first 15 months are TRAIN (selection happens here and
  only here), the last 9 months are HOLDOUT (scored once, after the picks are
  frozen). The years before the window are never loaded by the sweep and act as
  a second, larger out-of-sample check when the picks are reported.
* Three tiers per strategy, each a band of TRAIN bet counts (Volume >= 1,200,
  Balanced 500-1,199, Selective 200-499 on 15m) so the three picks are always
  distinct configurations. Admission needs both halves of TRAIN >= 52% and a
  TRAIN z-score >= 2.5. Within a band the pick is the highest one-sigma lower
  bound on the TRAIN hit rate (hit - SE): the train hit rate with a mild
  penalty for the thinnest configs.
* Every config is scored by calling the strategy's own ``generate_signals`` —
  the sweep has no private re-implementation, so what it measures is exactly
  what the dashboard runs.

Usage
-----
    python3 -m backend.data.pm_preset_sweep --interval 15m --strategy rsi_bb
    python3 -m backend.data.pm_preset_sweep --interval 15m --all --workers 30
    python3 -m backend.data.pm_preset_sweep --interval 15m --report   # re-score picks

Results land in ``--out`` (default ``data/sweeps/<interval>/``) as one JSONL per
strategy (every config with its bucketed counts) plus ``picks.json``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .. import registry
from .. import store
from .. import strategies  # noqa: F401 - registers strategies on import

# ---- fitting window ---------------------------------------------------------
# Two trailing years to the newest ingested bar, split 15 / 9 months. WARMUP is
# how far before TRAIN_START candles are loaded so long lookbacks are settled
# by the first scored bar (90 days covers a 1000-bar MA on 1h).
TRAIN_START = "2024-09-13"
TRAIN_END = "2025-12-13"          # = HOLDOUT start
WINDOW_END = "2026-09-13"
WARMUP_DAYS = 90

SYMBOL = "BTCUSDT"

# Tier bands: [min, max) TRAIN bets. Sized for 15m (≈43,800 train bars); scale
# them with --floor-scale for other intervals.
TIERS = [("Volume", 1200, None), ("Balanced", 500, 1200), ("Selective", 200, 500)]
MIN_HALF_HIT = 52.0               # both halves of TRAIN must clear this
MIN_TRAIN_Z = 2.5


def _ts(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


# ---- grid helpers -----------------------------------------------------------

def product(axes: dict) -> List[dict]:
    """Cartesian product of parameter axes.

    ``axes`` maps an axis name to a list of values. A scalar value is assigned
    to the param named by the axis; a dict value is merged in as-is (so one
    axis can move several params together, e.g. a matched overbought/oversold
    pair or a whole trend-filter block).
    """
    names = list(axes)
    out = []
    for combo in itertools.product(*(axes[n] for n in names)):
        d: dict = {}
        for n, v in zip(names, combo):
            if isinstance(v, dict):
                d.update(v)
            else:
                d[n] = v
        out.append(d)
    return out


# Shared building blocks. Trend-filter blocks use the key names of the
# common.trend_filter_group() strategies; rsi_bb/stoch_wick/atr_devexh spell
# some of them differently and get their own lists below.
_TREND_OFF = {"use_trend_filter": False}
_TREND_COMMON = [
    _TREND_OFF,
    {"use_trend_filter": True, "trend_logic": "With Trend", "ma_type": "EMA", "ma_length": 200},
    {"use_trend_filter": True, "trend_logic": "Against Trend", "ma_type": "EMA", "ma_length": 200},
    {"use_trend_filter": True, "trend_logic": "Against Trend", "ma_type": "SMA", "ma_length": 50},
]
_TREND_3 = _TREND_COMMON[:3]
_ATR_WIDE = {"atr_pct_min": 0.0, "atr_pct_max": 20.0}
_ATR_MID = {"atr_pct_min": 0.1, "atr_pct_max": 3.0}
_VOL_WIDE = {"vol_min_atr_pct": 0.0, "vol_max_atr_pct": 20.0}
_VOL_MID = {"vol_min_atr_pct": 0.1, "vol_max_atr_pct": 3.0}
_ALL_DAYS = {f"trade_{d}": True for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


def _pair(lo_key, hi_key, pairs):
    return [{lo_key: lo, hi_key: hi} for lo, hi in pairs]


# ---- per-strategy grids (15m) ----------------------------------------------
# Each returns the list of param overrides to score. Lookbacks are in BARS, so
# a 5m preset's 20-bar band is 100 minutes and the same 20 bars on 15m is 5
# hours: every grid carries both the 5m bar counts and their wall-clock
# equivalents (a third of the bars) so the sweep can decide which matters.

def grid_rsi_bb():
    return product({
        "direction": ["Both", "Long Only", "Short Only"],
        "rsi_length": [5, 7, 14],
        "rsi": _pair("rsi_oversold", "rsi_overbought", [(20, 80), (25, 75), (30, 70), (35, 65)]),
        "bb_length": [10, 20, 50],
        "bb_mult": [1.5, 2.0, 2.5],
        "pctb": _pair("pctb_lower", "pctb_upper", [(-0.1, 1.1), (0.0, 1.0), (0.1, 0.9)]),
        "candle": [{"min_wick_ratio": 0.0, "min_close_recovery": 0.0}],
        "bias": [{"use_bias_filter": False}],
        "atr": [_ATR_WIDE, _ATR_MID],
        "trend": [
            {"use_trend_filter": False},
            {"use_trend_filter": True, "trend_logic": "With Trend", "ma_type": "EMA", "ma_length": 200, "ma_source": "close"},
            {"use_trend_filter": True, "trend_logic": "Against Trend", "ma_type": "EMA", "ma_length": 200, "ma_source": "close"},
            {"use_trend_filter": True, "trend_logic": "Against Trend", "ma_type": "EMA", "ma_length": 100, "ma_source": "close"},
        ],
    })


def grid_stoch_wick():
    return product({
        "stoch_k_length": [5, 7, 14, 21],
        "stoch_d_length": [1, 3],
        "zone": _pair("oversold", "overbought", [(5, 95), (10, 90), (20, 80)]),
        "min_wick_ratio": [0.0, 0.1, 0.3],
        "min_close_recovery": [0.0],
        "adx": [{"use_adx_filter": False},
                {"use_adx_filter": True, "adx_length": 14, "adx_max": 25},
                {"use_adx_filter": True, "adx_length": 14, "adx_max": 30}],
        "atr": [_ATR_WIDE, _ATR_MID],
        "predict_direction": ["Reversion", "Breakout"],
        "trend": [
            {"use_trend_filter": False},
            {"use_trend_filter": True, "trend_logic": "With Trend", "ma_type": "EMA", "ma_length": 200},
            {"use_trend_filter": True, "trend_logic": "Against Trend", "ma_type": "EMA", "ma_length": 200},
        ],
    })


def grid_atr_devexh():
    return product({
        "velocity_lookback": [1, 3, 5, 10],
        "velocity_mode": ["Decelerating", "Accelerating", "Any"],
        "donchian_length": [10, 20, 30, 50, 100],
        "donchian_confirm": [1, 2, 3, 4],
        "atr": [_ATR_WIDE, _ATR_MID],
        "predict_direction": ["Reversion", "Breakout"],
        "trend": [
            {"use_trend_filter": False},
            {"use_trend_filter": True, "trend_mode": "With Trend", "ma_type": "EMA", "ma_length": 200},
            {"use_trend_filter": True, "trend_mode": "Against Trend", "ma_type": "EMA", "ma_length": 200},
            {"use_trend_filter": True, "trend_mode": "Against Trend", "ma_type": "EMA", "ma_length": 100},
        ],
    })


def grid_bb_squeeze():
    return product({
        "bb_length": [14, 20, 50],
        "bb_mult": [2.0, 2.5],
        "pctb": _pair("pctb_lower", "pctb_upper", [(0.0, 1.0), (-0.05, 1.05)]),
        "squeeze": [{"require_squeeze": False},
                    {"require_squeeze": True, "bw_lookback": 100, "bw_squeeze_pct": 20},
                    {"require_squeeze": True, "bw_lookback": 100, "bw_squeeze_pct": 40}],
        "bias": [{"use_ema_bias": False},
                 {"use_ema_bias": True, "ema_bias_length": 50, "ema_bias_slope_bars": 5},
                 {"use_ema_bias": True, "ema_bias_length": 200, "ema_bias_slope_bars": 5}],
        "min_body_ratio": [0.0, 0.2, 0.4],
        "vol": [dict(vol_atr_length=14, **_VOL_WIDE), dict(vol_atr_length=14, **_VOL_MID)],
        "predict_direction": ["Breakout", "Reversion"],
        "trend": _TREND_3,
    })


def grid_zscore_ms():
    return product({
        "zlen": [{"z_sma_length": n, "z_std_length": n} for n in (20, 50, 100)],
        "z": [{"z_upper": z, "z_lower": -z} for z in (1.5, 2.0, 2.5, 3.0)],
        "kc": [{"require_kc_break": False},
               {"require_kc_break": True, "kc_ema_length": 20, "kc_atr_length": 20, "kc_mult": 1.0},
               {"require_kc_break": True, "kc_ema_length": 20, "kc_atr_length": 20, "kc_mult": 1.5}],
        "bias": [{"use_bias_ma": False},
                 {"use_bias_ma": True, "bias_ema_length": 200, "bias_slope_lookback": 5}],
        "vol_atr_length": [14, 50],
        "vol": [_VOL_WIDE, _VOL_MID],
        "predict_direction": ["Reversion", "Momentum"],
        "trend": _TREND_3,
    })


def grid_regime_switch():
    return product({
        "regime_method": ["ADX", "Efficiency Ratio", "Volatility Ratio"],
        "regime_length": [20],
        "regime_threshold": [15, 20, 25, 35],
        "channel_length": [10, 20, 50],
        "breakout_buffer_atr": [0.0, 0.15, 0.3],
        "min_body_ratio": [0.0, 0.2],
        "regime_mapping": ["Trend=Momentum, Range=Reversion", "Trend=Reversion, Range=Momentum",
                           "Always Reversion", "Always Momentum"],
        "vol": [dict(vol_atr_length=50, **_VOL_WIDE), dict(vol_atr_length=50, vol_min_atr_pct=0.2, vol_max_atr_pct=3.0)],
        "trend": _TREND_3,
    })


def grid_volume_exhaustion():
    return product({
        "vol_ma_length": [10, 20, 50],
        "vol_spike_mult": [1.5, 2.0, 2.5, 4.0],
        "vol_rank_lookback": [500],
        "vol_rank_min": [0, 85, 95],
        "min_body_ratio": [0.0, 0.2, 0.6],
        "wick_min": [0.0, 0.35],
        "atr": [dict(vol_atr_length=50, **_ATR_WIDE), dict(vol_atr_length=50, atr_pct_min=0.15, atr_pct_max=2.0)],
        "predict_direction": ["Reversion", "Continuation"],
        "trend": [_TREND_OFF,
                  {"use_trend_filter": True, "trend_logic": "Against Trend", "ma_type": "SMA", "ma_length": 100},
                  {"use_trend_filter": True, "trend_logic": "With Trend", "ma_type": "EMA", "ma_length": 200}],
    })


def grid_jump_exhaustion():
    return product({
        "atr_length": [14, 20, 30],
        "jump1_atr_mult": [1.0, 1.3, 1.8, 2.5],
        "jump2_atr_mult": [3.0, 5.0, 20.0],
        "close_extreme_min": [0.0, 0.6],
        "wick_min_ratio": [0.0, 0.3],
        "rsi_length": [7, 14],
        "rsi": _pair("rsi_oversold", "rsi_overbought", [(30, 70), (25, 75), (35, 65)]),
        "atr": [_ATR_WIDE, {"atr_pct_min": 0.2, "atr_pct_max": 3.0}],
    })


def grid_cci_williams():
    return product({
        "cci_length": [10, 16, 20, 24],
        "cci_threshold": [110, 150, 190, 260],
        "wr_length": [7, 10, 12, 21],
        "wr": _pair("wr_oversold", "wr_overbought", [(-95, -5), (-90, -10), (-80, -20)]),
        "wick": [{"use_wick_confirm": False, "wick_min": 0.0, "close_recover_min": 0.0},
                 {"use_wick_confirm": True, "wick_min": 0.05, "close_recover_min": 0.0},
                 {"use_wick_confirm": True, "wick_min": 0.25, "close_recover_min": 0.15}],
        "atr": [dict(vol_atr_length=50, **_ATR_WIDE), dict(vol_atr_length=50, atr_pct_min=0.15, atr_pct_max=2.0)],
        "predict_direction": ["Reversion", "Continuation"],
    })


def grid_multi_horizon():
    return product({
        "h": [{"h_fast": f, "h_mid": m, "h_slow": s}
              for f, m, s in ((4, 8, 16), (6, 24, 72), (12, 24, 48), (12, 48, 144), (24, 72, 216), (8, 24, 96))],
        "z_threshold": [2.0, 2.5, 3.0],
        "min_agree": [1, 3],
        "require_fast": [False, True],
        "opp": [{"require_opposing_bar": False},
                {"require_opposing_bar": True, "opposing_bar_min_atr": 0.5},
                {"require_opposing_bar": True, "opposing_bar_min_atr": 0.75}],
        "atr": [dict(vol_atr_length=50, **_ATR_WIDE), dict(vol_atr_length=50, atr_pct_min=0.2, atr_pct_max=3.0)],
        "predict_direction": ["Reversion", "Continuation"],
        "trend": _TREND_3,
    })


def grid_fair_value_gap():
    return product({
        "min_gap_atr_mult": [0.15, 0.5, 1.0, 1.25, 1.5, 2.0],
        "min_impulse_body_ratio": [0.0, 0.5],
        "react": [{"require_reaction": False}, {"require_reaction": True, "reaction_min_atr": 0.5}],
        "entry_depth": [0.25, 0.5, 0.75, 1.0],
        "max_gap_age_bars": [24, 48, 100, 200],
        "atr": [_ATR_WIDE, _ATR_MID],
        "predict_direction": ["Continuation", "Reversion"],
        "trend": _TREND_3,
    })


def grid_fib_retracement():
    return product({
        "swing_lookback": [24, 48, 72, 96],
        "min_leg_atr": [2.0, 4.0, 6.0],
        "min_leg_bars": [1],
        "fib_level": [0.5, 0.618, 0.786, 0.85],
        "fib_tolerance": [0.02, 0.05, 0.1],
        "opp": [{"require_opposing_bar": False},
                {"require_opposing_bar": True, "opposing_bar_min_atr": 0.5},
                {"require_opposing_bar": True, "opposing_bar_min_atr": 0.75}],
        "atr": [dict(vol_atr_length=50, **_ATR_WIDE), dict(vol_atr_length=50, atr_pct_min=0.1, atr_pct_max=2.0)],
        "predict_direction": ["Trend Resume", "Retrace Deeper"],
        "trend": _TREND_COMMON,
    })


_PATS = ["pat_engulfing", "pat_hammer", "pat_harami", "pat_piercing", "pat_star",
         "pat_doji", "pat_tweezer", "pat_marubozu", "pat_soldiers"]


def _pats(*on):
    return {k: (k in on) for k in _PATS}


def grid_candlesticks():
    return product({
        "pats": [_pats("pat_engulfing", "pat_hammer"), _pats("pat_marubozu"), _pats("pat_engulfing"),
                 _pats("pat_hammer"), _pats(*_PATS), _pats("pat_engulfing", "pat_star", "pat_piercing"),
                 _pats("pat_doji", "pat_tweezer", "pat_harami")],
        "marubozu_body_min": [0.75, 0.9],
        "body_strong_min": [0.5, 0.7],
        "min_range_atr": [0.5, 1.0, 1.5],
        "prior": [{"require_prior_move": False},
                  {"require_prior_move": True, "prior_move_logic": "Textbook", "prior_move_bars": 12, "prior_move_atr": 1.0},
                  {"require_prior_move": True, "prior_move_logic": "Extension", "prior_move_bars": 6, "prior_move_atr": 1.0},
                  {"require_prior_move": True, "prior_move_logic": "Extension", "prior_move_bars": 6, "prior_move_atr": 1.5},
                  {"require_prior_move": True, "prior_move_logic": "Extension", "prior_move_bars": 12, "prior_move_atr": 2.0},
                  {"require_prior_move": True, "prior_move_logic": "Reversal", "prior_move_bars": 12, "prior_move_atr": 1.0}],
        "atr": [_ATR_WIDE, _ATR_MID],
        "predict_direction": ["Pattern", "Fade"],
        "trend": _TREND_3,
    })


def grid_reversal():
    no_candles = {"use_engulfing": False, "use_pin_bar": False, "use_star": False, "use_piercing": False}
    out = []
    # A. candlestick family (location gate on/off)
    out += product({
        "pat": [{"use_engulfing": True, "use_pin_bar": True, "use_star": True, "use_piercing": False},
                {"use_engulfing": True, "use_pin_bar": False, "use_star": False, "use_piercing": False},
                {"use_engulfing": False, "use_pin_bar": True, "use_star": False, "use_piercing": False},
                {"use_engulfing": False, "use_pin_bar": False, "use_star": True, "use_piercing": False}],
        "min_body_ratio": [0.3, 0.5],
        "min_wick_ratio": [0.5, 0.7],
        "loc": [{"use_location": True, "swing_lookback": 20, "extreme_tolerance_atr": 0.25},
                {"use_location": True, "swing_lookback": 50, "extreme_tolerance_atr": 0.5},
                {"use_location": False}],
        "rest": [{"use_divergence": False, "use_structure": False, "min_confirmations": 1}],
        "predict_direction": ["Reversal", "Continuation"],
        "atr": [_ATR_WIDE, _ATR_MID],
        "trend": _TREND_3,
    })
    # B. break of structure
    out += product({
        "base": [dict(no_candles, use_location=False, use_divergence=False, use_structure=True,
                      structure_pattern="Break of Structure", min_confirmations=1, retest_tolerance_atr=0.5)],
        "pivot_left": [4, 8, 12, 20, 30, 50],
        "pivot_right": [1, 2, 3],
        "max_pivot_gap": [60, 200, 300],
        "predict_direction": ["Reversal", "Continuation"],
        "atr": [{"atr_pct_min": 0.02, "atr_pct_max": 20.0}, {"atr_pct_min": 0.08, "atr_pct_max": 20.0}, _ATR_MID],
        "trend": _TREND_3,
    })
    # C. double top / bottom
    out += product({
        "base": [dict(no_candles, use_location=False, use_divergence=False, use_structure=True,
                      structure_pattern="Double Top/Bottom", min_confirmations=1)],
        "pivot_left": [4, 8, 12, 20, 30],
        "pivot_right": [1, 2, 3],
        "retest_tolerance_atr": [0.25, 0.5, 1.0],
        "predict_direction": ["Reversal", "Continuation"],
        "atr": [_ATR_WIDE, _ATR_MID],
        "trend": _TREND_3,
    })
    # D. divergence
    out += product({
        "base": [dict(no_candles, use_location=False, use_divergence=True, use_structure=False,
                      min_confirmations=1, rsi_length=14)],
        "osc_type": ["RSI", "MACD Hist"],
        "min_osc_gap": [1.0, 3.0, 5.0],
        "pivot_left": [3, 8, 12],
        "pivot_right": [1, 3],
        "predict_direction": ["Reversal", "Continuation"],
        "atr": [_ATR_WIDE, _ATR_MID],
        "trend": _TREND_3,
    })
    return out


_HARM = ["use_gartley", "use_bat", "use_butterfly", "use_crab", "use_cypher", "use_shark", "use_abcd"]


def _harm(*on):
    return {k: (k in on) for k in _HARM}


def grid_harmonic():
    return product({
        "piv": [{"pivot_left": l, "pivot_right": r} for l, r in ((3, 3), (5, 3), (8, 3), (12, 3))],
        "pats": [_harm(*_HARM), _harm(*[k for k in _HARM if k != "use_abcd"]), _harm("use_abcd"),
                 _harm("use_gartley", "use_bat"), _harm("use_butterfly", "use_crab")],
        "ratio_tolerance": [0.05, 0.1],
        "require_cd_zone": [True, False],
        "min_xa_atr": [3.0, 7.0],
        "max_prz_atr": [2.0, 4.0],
        "prz": [{"prz_entry": "Wick Touch", "prz_overshoot_atr": 1.0, "max_bars_to_d": 300},
                {"prz_entry": "Close Inside", "prz_overshoot_atr": 1.0, "max_bars_to_d": 300}],
        "atr": [_ATR_WIDE, {"atr_pct_min": 0.1, "atr_pct_max": 2.0}],
        "predict_direction": ["Reversal", "Continuation"],
        "trend": [_TREND_OFF, _TREND_COMMON[3], _TREND_COMMON[2]],
    })


def grid_choch():
    return product({
        "pivot_left": [4, 8, 12, 20, 30],
        "pivot_right": [1, 3],
        "signal_on": ["CHoCH only", "BOS only", "Both"],
        "break_mode": ["Close Beyond", "Wick Beyond"],
        "break_buffer_atr": [0.0, 0.5, 0.75, 1.0],
        "min_displacement_atr": [0.0, 0.5, 1.0],
        "entry": [{"entry_mode": "On Break"},
                  {"entry_mode": "On Retest", "retest_tol_atr": 0.25, "max_retest_bars": 20}],
        "htf": [{"use_htf_filter": False},
                {"use_htf_filter": True, "htf_logic": "Agree", "htf_pivot_left": 30, "htf_pivot_right": 3},
                {"use_htf_filter": True, "htf_logic": "Oppose", "htf_pivot_left": 60, "htf_pivot_right": 3}],
        "predict_direction": ["With Structure", "Against Structure"],
        "atr": [dict(vol_atr_length=50, atr_pct_min=0.05, atr_pct_max=1.5), dict(vol_atr_length=50, **_ATR_WIDE)],
    })


_OSC9 = ["use_roc", "use_rsi", "use_stoch", "use_willr", "use_cci", "use_uo", "use_macd", "use_tsi", "use_ao"]


def _osc(*on):
    return {k: (k in on) for k in _OSC9}


def grid_momentum():
    return product({
        "panel": [_osc(*_OSC9), _osc("use_rsi", "use_stoch", "use_willr", "use_cci"),
                  _osc("use_roc", "use_macd", "use_tsi", "use_ao"), _osc("use_rsi")],
        "osc_length": [5, 10, 14],
        "norm_atr_mult": [1.0, 4.0],
        "min_agree": [1, 2, 4],
        "score_threshold": [0.3, 0.7],
        "trigger_mode": ["Extreme", "Zero Cross", "Momentum Turn"],
        "predict_direction": ["Fade Momentum", "Follow Momentum"],
        "atr": [dict(vol_atr_length=14, **_ATR_WIDE), dict(vol_atr_length=50, **_ATR_MID)],
        "trend": _TREND_3,
    })


def grid_elliott_wave():
    return product({
        "pivot_atr_mult": [2.5, 4.0, 6.0],
        "min_pivot_bars": [1, 3],
        "min_wave1_atr": [0.0, 6.0],
        "enforce_impulse_rules": [True, False],
        "w2": [{"wave2_min_retrace": 0.236, "wave2_max_retrace": 1.0},
               {"wave2_min_retrace": 0.0, "wave2_max_retrace": 3.0},
               {"wave2_min_retrace": 0.618, "wave2_max_retrace": 1.2}],
        "w4": [{"wave4_min_retrace": 0.0, "wave4_max_retrace": 3.0}],
        "trade_setup": ["Wave 3", "Wave 5", "Wave 3 + 5", "Post-Impulse Reversal"],
        "entry_mode": ["Pivot Confirm", "Retrace Zone"],
        "max_setup_age_bars": [48, 288],
        "opp": [{"require_opposing_bar": False}, {"require_opposing_bar": True, "opposing_bar_min_atr": 0.75}],
        "predict_direction": ["Follow Count", "Fade Count"],
        "atr": [_ATR_WIDE],
        "trend": [_TREND_OFF, _TREND_COMMON[3]],
    })


def grid_renko():
    return product({
        "brick": [{"brick_mode": "ATR", "atr_length": 14, "brick_atr_mult": m} for m in (0.5, 1.0, 2.0, 3.0)]
                 + [{"brick_mode": "Percent", "brick_pct": p} for p in (0.1, 0.2, 0.3, 0.5, 1.0)],
        "reversal_bricks": [1, 2, 3],
        "trigger": ["Brick Reversal", "Brick Run", "Any New Brick"],
        "min_run_bricks": [2, 3, 5, 8],
        "max_new_bricks": [0, 1, 2],
        "predict_direction": ["Fade Brick", "Follow Brick"],
        "atr": [_ATR_WIDE, {"atr_pct_min": 0.15, "atr_pct_max": 1.0}],
        "trend": _TREND_3,
    })


def grid_trend_lines():
    return product({
        "pivot_left": [5, 12, 20, 30, 50],
        "pivot_right": [1, 2, 3],
        "gap": [{"min_pivot_gap": 5, "max_pivot_gap": 200, "max_line_age_bars": 200},
                {"min_pivot_gap": 5, "max_pivot_gap": 1000, "max_line_age_bars": 1000}],
        "require_direction": [True, False],
        "max_slope_atr": [0.5, 2.0],
        "trig": [{"use_break": True, "use_bounce": False, "break_buffer_atr": b} for b in (0.0, 0.1, 0.3, 0.5)]
                + [{"use_break": False, "use_bounce": True, "bounce_tol_atr": t} for t in (0.25, 0.5)],
        "predict_direction": ["With Signal", "Against Signal"],
        "atr": [{"atr_pct_min": 0.02, "atr_pct_max": 20.0}, {"atr_pct_min": 0.05, "atr_pct_max": 1.5}],
        "trend": _TREND_3,
    })


def grid_support_resistance():
    return product({
        "pivot_left": [5, 12, 20, 30],
        "pivot_right": [1, 2, 3],
        "cluster_tol_atr": [0.5, 1.0],
        "min_touches": [1, 2, 3],
        "max_level_age_bars": [500, 3000],
        "max_levels": [30],
        "trig": [{"use_break": True, "use_bounce": False, "break_buffer_atr": b} for b in (0.0, 0.1, 0.3)]
                + [{"use_break": False, "use_bounce": True, "zone_tol_atr": t} for t in (0.25, 0.5)],
        "predict_direction": ["With Signal", "Against Signal"],
        "atr": [_ATR_WIDE, {"atr_pct_min": 0.05, "atr_pct_max": 1.5}],
        "trend": [_TREND_OFF, _TREND_COMMON[2]],
    })


_GANN_ANGLES = ["use_1x8", "use_1x4", "use_1x3", "use_1x2", "use_1x1", "use_2x1", "use_3x1", "use_4x1", "use_8x1"]


def _fan(unit, *on):
    d = {k: (k in on) for k in _GANN_ANGLES}
    d["unit_atr_mult"] = unit
    return d


def grid_gann():
    return product({
        "pivot_left": [10, 20, 30, 50],
        "pivot_right": [1, 2],
        "fan": [_fan(0.002, "use_1x1"),                       # a flat level: the angles measured as worthless on 5m
                _fan(0.5, "use_1x1"),
                _fan(0.5, "use_1x2", "use_1x1", "use_2x1"),
                _fan(0.5, *_GANN_ANGLES),
                _fan(0.25, *_GANN_ANGLES)],
        "max_anchor_age_bars": [100, 300],
        "trig": [{"use_break": True, "use_bounce": False, "break_buffer_atr": b} for b in (0.1, 0.3, 0.8, 1.6)]
                + [{"use_break": False, "use_bounce": True, "bounce_tol_atr": 0.25}],
        "predict_direction": ["With Signal", "Against Signal"],
        "atr": [_ATR_WIDE, {"atr_pct_min": 0.05, "atr_pct_max": 1.5}],
        "trend": [_TREND_OFF, _TREND_COMMON[2]],
    })


def grid_oscillators():
    return product({
        "osc_type": ["RSI", "Stochastic", "Stoch RSI", "Williams %R", "CCI", "Ultimate", "TSI"],
        "osc_length": [5, 11, 14, 21],
        "smooth_k": [1, 2],
        "signal_length": [3],
        "zone": _pair("oversold", "overbought", [(10, 90), (20, 80), (30, 70)]),
        "trigger_mode": ["Zone Entry", "Zone Exit", "Signal Cross", "Centerline Cross", "Failure Swing"],
        "predict_direction": ["Fade Oscillator", "Follow Oscillator"],
        "atr": [_ATR_WIDE, _ATR_MID],
        "trend": _TREND_3,
    })


GRIDS = {
    "rsi_bb": grid_rsi_bb, "stoch_wick": grid_stoch_wick, "atr_devexh": grid_atr_devexh,
    "bb_squeeze": grid_bb_squeeze, "zscore_ms": grid_zscore_ms, "regime_switch": grid_regime_switch,
    "volume_exhaustion": grid_volume_exhaustion, "jump_exhaustion": grid_jump_exhaustion,
    "cci_williams": grid_cci_williams, "multi_horizon": grid_multi_horizon,
    "fair_value_gap": grid_fair_value_gap, "fib_retracement": grid_fib_retracement,
    "candlesticks": grid_candlesticks, "reversal": grid_reversal, "harmonic": grid_harmonic,
    "choch": grid_choch, "momentum": grid_momentum, "elliott_wave": grid_elliott_wave,
    "renko": grid_renko, "trend_lines": grid_trend_lines, "support_resistance": grid_support_resistance,
    "gann": grid_gann, "oscillators": grid_oscillators,
}


# ---- scoring ----------------------------------------------------------------
# Worker-side globals, set once before the pool forks so every worker shares
# the candle list copy-on-write instead of pickling it per task.
_CANDLES: List[dict] = []
_BOUNDS: tuple = ()          # (train_start, train_mid, train_end, window_end) unix seconds
_STRAT = None
_GRID: List[dict] = []

BUCKETS = ("pre", "h1", "h2", "hold", "post")


def _bucket(t: int) -> str:
    ts, tm, te, we = _BOUNDS
    if t < ts:
        return "pre"
    if t < tm:
        return "h1"
    if t < te:
        return "h2"
    if t < we:
        return "hold"
    return "post"


def score_signals(candles: List[dict], signals) -> dict:
    """Bucketed (bets, wins) — same resolution rule as run_binary_backtest."""
    n = len(candles)
    out = {b: [0, 0] for b in BUCKETS}
    for s in signals:
        j = s.index + 1
        if j >= n:
            continue
        res = candles[j]
        b = out[_bucket(res["time"])]
        b[0] += 1
        if (s.side == "long" and res["close"] > res["open"]) or \
           (s.side == "short" and res["close"] < res["open"]):
            b[1] += 1
    return out


def _evaluate(idx: int):
    params = _GRID[idx]
    try:
        sig = _STRAT.generate_signals(_CANDLES, _STRAT.resolve_params(params))
    except Exception as e:  # noqa: BLE001 - one bad config must not kill the sweep
        return idx, None, repr(e)
    return idx, score_signals(_CANDLES, sig), None


# ---- statistics -------------------------------------------------------------

def hit(bw):
    bets, wins = bw
    return 100.0 * wins / bets if bets else 0.0


def zscore(bw):
    bets, wins = bw
    return (wins - 0.5 * bets) / math.sqrt(0.25 * bets) if bets else 0.0


def se(bw):
    bets, wins = bw
    if not bets:
        return 0.0
    p = wins / bets
    return 100.0 * math.sqrt(p * (1 - p) / bets)


def train_of(r: dict):
    h1, h2 = r["h1"], r["h2"]
    return [h1[0] + h2[0], h1[1] + h2[1]]


def select(results: List[dict], floor_scale: float = 1.0):
    """Tier picks from a list of {'params', 'h1', 'h2', 'hold', ...} rows."""
    picks = {}
    for tier, lo, hi in TIERS:
        lo = int(lo * floor_scale)
        hi = None if hi is None else int(hi * floor_scale)
        best = None
        best_key = None
        for r in results:
            tr = train_of(r)
            if tr[0] < lo or (hi is not None and tr[0] >= hi):
                continue
            if hit(r["h1"]) < MIN_HALF_HIT or hit(r["h2"]) < MIN_HALF_HIT:
                continue
            if zscore(tr) < MIN_TRAIN_Z:
                continue
            key = hit(tr) - se(tr)
            if best is None or key > best_key:
                best, best_key = r, key
        picks[tier] = best
    return picks


# ---- driver -----------------------------------------------------------------

def load_candles(interval: str, start: str, end: str, warmup_days: int = WARMUP_DAYS) -> List[dict]:
    lo = (_ts(start) - warmup_days * 86400) * 1000
    hi = _ts(end) * 1000 + 86399_000
    return store.get_candles(SYMBOL, interval, lo, hi)


def sweep_one(sid: str, candles: List[dict], bounds: tuple, workers: int, out_dir: Path,
              floor_scale: float) -> dict:
    global _CANDLES, _BOUNDS, _STRAT, _GRID
    _CANDLES, _BOUNDS = candles, bounds
    _STRAT = registry.get(sid)
    _GRID = GRIDS[sid]()
    n = len(_GRID)
    t0 = time.time()
    print(f"[{sid}] {n} configs on {len(candles)} bars, {workers} workers", flush=True)

    rows: List[dict] = []
    errors = 0
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        done = 0
        for idx, sc, err in pool.imap_unordered(_evaluate, range(n), chunksize=4):
            done += 1
            if err is not None:
                errors += 1
                continue
            rows.append({"params": _GRID[idx], **sc})
            if done % 500 == 0 or done == n:
                el = time.time() - t0
                print(f"  {done}/{n}  {el:.0f}s  (~{el / done * (n - done):.0f}s left)", flush=True)
    if errors:
        print(f"  {errors} configs raised and were skipped", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"{sid}.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    picks = select(rows, floor_scale)
    _print_picks(sid, picks)
    return {tier: (p["params"] if p else None) for tier, p in picks.items()}


def _print_picks(sid: str, picks: dict):
    print(f"[{sid}] picks:")
    for tier, r in picks.items():
        if r is None:
            print(f"  {tier:10s} -- nothing admitted")
            continue
        tr = train_of(r)
        print(f"  {tier:10s} train {tr[0]:6d} bets {hit(tr):6.2f}%  (h1 {hit(r['h1']):.2f} / h2 {hit(r['h2']):.2f})  "
              f"HOLDOUT {r['hold'][0]:5d} bets {hit(r['hold']):6.2f}%   z {zscore(tr):+.1f}")
        print(f"             {json.dumps(r['params'], sort_keys=True)}")


def load_rows(path: Path) -> List[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def report(interval: str, out_dir: Path, floor_scale: float):
    """Re-derive picks from saved rows and score them over the WHOLE record
    (unswept years, per calendar year) — the numbers the preset notes quote."""
    picks_path = out_dir / "picks.json"
    all_picks = {}
    for path in sorted(out_dir.glob("*.jsonl")):
        sid = path.stem
        if sid not in GRIDS:
            continue
        all_picks[sid] = {t: (p["params"] if p else None) for t, p in select(load_rows(path), floor_scale).items()}
    with open(picks_path, "w") as f:
        json.dump(all_picks, f, indent=1, sort_keys=True)

    cov = store.coverage(SYMBOL)
    full = store.get_candles(SYMBOL, interval, cov["min"] * 1000, cov["max"] * 1000)
    ts, te, we = _ts(TRAIN_START), _ts(TRAIN_END), _ts(WINDOW_END) + 86400
    tm = (ts + te) // 2
    global _BOUNDS
    _BOUNDS = (ts, tm, te, we)
    lines = []
    full_report = {}
    for sid, tiers in all_picks.items():
        strat = registry.get(sid)
        lines.append(f"\n== {sid} ==")
        full_report[sid] = {}
        for tier, params in tiers.items():
            if params is None:
                lines.append(f"  {tier:10s} -- nothing admitted")
                continue
            sig = strat.generate_signals(full, strat.resolve_params(params))
            sc = score_signals(full, sig)
            by_year: Dict[int, list] = {}
            for s in sig:
                j = s.index + 1
                if j >= len(full):
                    continue
                res = full[j]
                y = datetime.fromtimestamp(res["time"], timezone.utc).year
                b = by_year.setdefault(y, [0, 0])
                b[0] += 1
                b[1] += int((s.side == "long" and res["close"] > res["open"])
                            or (s.side == "short" and res["close"] < res["open"]))
            tot = [sum(v[0] for v in sc.values()), sum(v[1] for v in sc.values())]
            tr = train_of(sc)
            two = [tr[0] + sc["hold"][0], tr[1] + sc["hold"][1]]
            full_report[sid][tier] = {
                "params": params, "all": tot, "unswept": sc["pre"], "2yr": two,
                "train": tr, "h1": sc["h1"], "h2": sc["h2"], "holdout": sc["hold"],
                "by_year": {str(k): v for k, v in sorted(by_year.items())},
            }
            worst = min(((hit(v), y) for y, v in by_year.items() if v[0] >= 50), default=(0, None))
            lines.append(
                f"  {tier:10s} all {tot[0]:6d} {hit(tot):6.2f}% z{zscore(tot):+6.1f} | unswept {sc['pre'][0]:6d} "
                f"{hit(sc['pre']):6.2f}% | 2yr {hit(two):6.2f}% | train {hit(tr):6.2f}% ({hit(sc['h1']):.1f}/{hit(sc['h2']):.1f}) "
                f"| HOLDOUT {sc['hold'][0]:5d} {hit(sc['hold']):6.2f}% | worst yr {worst[0]:.1f}% ({worst[1]})")
    print("\n".join(lines))
    with open(out_dir / "report.json", "w") as f:
        json.dump(full_report, f, indent=1, sort_keys=True)
    print(f"\nwrote {picks_path} and {out_dir / 'report.json'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", default="15m")
    ap.add_argument("--strategy", action="append", help="strategy id (repeatable)")
    ap.add_argument("--all", action="store_true", help="every strategy with a grid")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--out", default=None, help="results dir (default data/sweeps/<interval>)")
    ap.add_argument("--floor-scale", type=float, default=1.0,
                    help="multiply the tier bet floors (1.0 is sized for 15m)")
    ap.add_argument("--report", action="store_true",
                    help="skip sweeping; re-select from saved rows and score the picks on the whole record")
    ap.add_argument("--list", action="store_true", help="print grid sizes and exit")
    args = ap.parse_args(argv)

    out_dir = Path(args.out) if args.out else (Path(__file__).resolve().parents[2] / "data" / "sweeps" / args.interval)

    if args.list:
        for sid, g in GRIDS.items():
            print(f"{sid:20s} {len(g()):7d}")
        return

    if args.report:
        report(args.interval, out_dir, args.floor_scale)
        return

    sids = list(GRIDS) if args.all else (args.strategy or [])
    if not sids:
        ap.error("give --strategy ID (repeatable) or --all")
    for sid in sids:
        if sid not in GRIDS:
            ap.error(f"no grid for {sid!r}; known: {', '.join(GRIDS)}")

    candles = load_candles(args.interval, TRAIN_START, WINDOW_END)
    ts, te, we = _ts(TRAIN_START), _ts(TRAIN_END), _ts(WINDOW_END) + 86400
    bounds = (ts, (ts + te) // 2, te, we)
    print(f"{len(candles)} {args.interval} bars "
          f"{datetime.fromtimestamp(candles[0]['time'], timezone.utc):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(candles[-1]['time'], timezone.utc):%Y-%m-%d %H:%M}; "
          f"train {TRAIN_START}..{TRAIN_END}, holdout ..{WINDOW_END}", flush=True)

    picks = {}
    if (out_dir / "picks.json").exists():
        with open(out_dir / "picks.json") as f:
            picks = json.load(f)
    for sid in sids:
        picks[sid] = sweep_one(sid, candles, bounds, args.workers, out_dir, args.floor_scale)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "picks.json", "w") as f:
            json.dump(picks, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    sys.exit(main())
