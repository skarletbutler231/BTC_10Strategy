"""Parameter groups and helpers shared by multiple strategies.

Several strategies expose the same optional filters — an allowed trading window
(weekday + UTC time-of-day) and a moving-average trend filter — plus the same
price-source and MA-type choices. They live here so each new strategy composes
them instead of re-declaring them.

Usage in a strategy's param_groups():

    from .common import trading_window_group, trend_filter_group
    ...
    trading_window_group(),
    trend_filter_group(),

and in generate_signals():

    allowed = allowed_days(p)
    start_min, end_min = window_minutes(p)
    ...
    if p["use_trading_window"] and not in_window(c["time"], allowed, start_min, end_min):
        continue
    if p["use_trend_filter"] and not trend_ok(side, cl, trend_ma[i], with_trend):
        continue
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .. import indicators as ind
from .base import Param, ParamGroup

# trade_* keys are ordered to match datetime.weekday(): Monday=0 .. Sunday=6
DAYS = ["trade_mon", "trade_tue", "trade_wed", "trade_thu",
        "trade_fri", "trade_sat", "trade_sun"]
DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday",
              "Friday", "Saturday", "Sunday"]
MA_TYPES = ["SMA", "EMA", "WMA", "RMA"]
SOURCES = ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"]


# ---- price source & moving averages -----------------------------------------

def source_values(candles: List[dict], source: str) -> List[float]:
    """Extract a price series from candles by source name."""
    if source == "open":
        return [c["open"] for c in candles]
    if source == "high":
        return [c["high"] for c in candles]
    if source == "low":
        return [c["low"] for c in candles]
    if source == "hl2":
        return [(c["high"] + c["low"]) / 2.0 for c in candles]
    if source == "hlc3":
        return [(c["high"] + c["low"] + c["close"]) / 3.0 for c in candles]
    if source == "ohlc4":
        return [(c["open"] + c["high"] + c["low"] + c["close"]) / 4.0 for c in candles]
    return [c["close"] for c in candles]  # default: close


def moving_average(values: List[float], ma_type: str, length: int):
    """Dispatch to the requested moving average (defaults to SMA)."""
    if ma_type == "EMA":
        return ind.ema(values, length)
    if ma_type == "WMA":
        return ind.wma(values, length)
    if ma_type == "RMA":
        return ind.rma(values, length)
    return ind.sma(values, length)


# ---- shared parameter groups ------------------------------------------------

def trading_window_group(title: str = "Allowed Trading Window") -> ParamGroup:
    """Weekday checkboxes + a UTC start/end time-of-day window."""
    return ParamGroup(title, [
        Param("use_trading_window", "Use Allowed Trading Window?", False, "bool",
              help="Restrict entries to the weekdays and UTC time span below."),
        *[Param(DAYS[i], DAY_LABELS[i], True, "bool",
                help=f"Allow entries on {DAY_LABELS[i]} (UTC).")
          for i in range(7)],
        Param("start_hour", "Start Hour", 0, "int", 0, 23, 1, "Window start hour (UTC, 0-23)."),
        Param("start_minute", "Start Minute", 0, "int", 0, 59, 1, "Window start minute (UTC)."),
        Param("end_hour", "End Hour", 23, "int", 0, 23, 1, "Window end hour (UTC, 0-23)."),
        Param("end_minute", "End Minute", 59, "int", 0, 59, 1, "Window end minute (UTC)."),
    ])


def trend_filter_group(title: str = "Trend Filter", ma_type: str = "EMA",
                       ma_length: int = 200) -> ParamGroup:
    """Optional moving-average trend agreement filter."""
    return ParamGroup(title, [
        Param("use_trend_filter", "Use Trend Filter?", False, "bool",
              help="Require price to agree with a moving-average trend."),
        Param("trend_logic", "Trend Logic", "With Trend", "enum",
              options=["With Trend", "Against Trend"],
              help="With Trend: long above / short below MA. Against Trend: the opposite."),
        Param("ma_type", "MA Type", ma_type, "enum", options=MA_TYPES,
              help="Moving-average type for the trend filter."),
        Param("ma_length", "MA Length", ma_length, "int", 2, 1000, 1,
              "Lookback for the trend MA."),
        Param("source", "Source", "close", "enum", options=SOURCES,
              help="Price source for the trend MA."),
    ])


# ---- runtime helpers --------------------------------------------------------

def allowed_days(params: dict) -> set:
    """Set of weekday ints (Mon=0..Sun=6) enabled in the trading window."""
    return {i for i in range(7) if params.get(DAYS[i], True)}


def window_minutes(params: dict):
    """(start, end) minutes-of-day for the trading window."""
    return (params["start_hour"] * 60 + params["start_minute"],
            params["end_hour"] * 60 + params["end_minute"])


def in_window(ts: int, allowed: set, start_min: int, end_min: int) -> bool:
    """Is this candle time inside the allowed weekday + UTC time window?

    Wrap-aware: start > end means an overnight window (e.g. 22:00 -> 04:00).
    """
    dt = datetime.fromtimestamp(ts, timezone.utc)
    if dt.weekday() not in allowed:
        return False
    cur = dt.hour * 60 + dt.minute
    if start_min <= end_min:
        return start_min <= cur <= end_min
    return cur >= start_min or cur <= end_min


def trend_ok(side: str, close: float, ma_value, with_trend: bool) -> bool:
    """Does `side` agree with the MA trend (or deliberately oppose it)?"""
    if ma_value is None:
        return False
    above = close > ma_value
    agree = (side == "long" and above) or (side == "short" and not above)
    return agree if with_trend else not agree
