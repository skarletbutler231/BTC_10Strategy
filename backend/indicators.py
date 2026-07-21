"""Pure-Python technical indicators.

Everything works on lists of candle dicts (see binance.py) and returns lists
aligned index-for-index with the input, using None for warm-up bars where the
indicator is not yet defined. Kept dependency-free and explicit so the logic is
easy to read and port.
"""

from __future__ import annotations

from typing import List, Optional

Num = Optional[float]


def true_ranges(candles: list[dict]) -> List[float]:
    tr: List[float] = []
    prev_close = None
    for c in candles:
        h, l = c["high"], c["low"]
        if prev_close is None:
            tr.append(h - l)
        else:
            tr.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = c["close"]
    return tr


def atr(candles: list[dict], period: int) -> List[Num]:
    """Wilder's ATR. None until `period` bars of history exist."""
    n = len(candles)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    tr = true_ranges(candles)
    # seed with simple average of the first `period` true ranges
    seed = sum(tr[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def rsi(candles: list[dict], period: int) -> List[Num]:
    """Wilder's RSI (0-100). None until `period` bars of change exist."""
    n = len(candles)
    out: List[Num] = [None] * n
    if period <= 0 or n <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        ch = candles[i]["close"] - candles[i - 1]["close"]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period

    def rsi_from(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    out[period] = rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, n):
        ch = candles[i]["close"] - candles[i - 1]["close"]
        gain = max(ch, 0.0)
        loss = max(-ch, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = rsi_from(avg_gain, avg_loss)
    return out


def williams_r(candles: list[dict], period: int) -> List[Num]:
    """Williams %R over the last `period` bars, on a -100..0 scale.

    0 means the close sits exactly at the window's highest high (overbought);
    -100 means it sits at the lowest low (oversold). None during warm-up, and
    also on a perfectly flat window where the measure is undefined.
    """
    n = len(candles)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        seg = candles[i - period + 1 : i + 1]
        hh = max(c["high"] for c in seg)
        ll = min(c["low"] for c in seg)
        rng = hh - ll
        if rng > 0:
            out[i] = (hh - candles[i]["close"]) / rng * -100.0
    return out


def cci(candles: list[dict], period: int) -> List[Num]:
    """Commodity Channel Index on the typical price (H+L+C)/3.

    CCI = (TP - SMA(TP)) / (0.015 * mean absolute deviation). The 0.015 scaling
    is Lambert's original constant, which puts roughly 70-80% of readings inside
    +/-100 — so |CCI| >= 100 is the conventional "stretched" band. A flat window
    (zero mean deviation) yields 0.0 rather than a divide-by-zero.
    """
    n = len(candles)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    tp = [(c["high"] + c["low"] + c["close"]) / 3.0 for c in candles]
    basis = sma(tp, period)
    for i in range(period - 1, n):
        m = basis[i]
        if m is None:
            continue
        seg = tp[i - period + 1 : i + 1]
        mad = sum(abs(x - m) for x in seg) / period
        out[i] = 0.0 if mad == 0 else (tp[i] - m) / (0.015 * mad)
    return out


def rolling_close_extremes(candles: list[dict], window: int):
    """Rolling (min_close, max_close) over the last `window` closes ending at i.

    Returns two lists; entries are None until `window` closes are available.
    """
    n = len(candles)
    lo: List[Num] = [None] * n
    hi: List[Num] = [None] * n
    if window <= 0:
        return lo, hi
    closes = [c["close"] for c in candles]
    for i in range(window - 1, n):
        seg = closes[i - window + 1 : i + 1]
        lo[i] = min(seg)
        hi[i] = max(seg)
    return lo, hi


# ---- moving averages & dispersion (operate on plain value lists) ------------
# These take a list of floats (e.g. closes, or a derived price source) rather
# than candles, so any strategy can feed them whatever series it needs.

def sma(values: List[float], period: int) -> List[Num]:
    """Simple moving average. None until `period` values exist."""
    n = len(values)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    s = sum(values[:period])
    out[period - 1] = s / period
    for i in range(period, n):
        s += values[i] - values[i - period]
        out[i] = s / period
    return out


def ema(values: List[float], period: int) -> List[Num]:
    """Exponential moving average, seeded with an SMA of the first `period`."""
    n = len(values)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def wma(values: List[float], period: int) -> List[Num]:
    """Linearly-weighted moving average (most recent value weighted highest)."""
    n = len(values)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    denom = period * (period + 1) / 2.0
    for i in range(period - 1, n):
        seg = values[i - period + 1 : i + 1]
        out[i] = sum(v * (j + 1) for j, v in enumerate(seg)) / denom
    return out


def rma(values: List[float], period: int) -> List[Num]:
    """Wilder's smoothed moving average (RMA/SMMA), as used inside ATR/RSI."""
    n = len(values)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def rolling_std(values: List[float], period: int) -> List[Num]:
    """Population standard deviation over the last `period` values (Bollinger)."""
    n = len(values)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        seg = values[i - period + 1 : i + 1]
        m = sum(seg) / period
        var = sum((x - m) ** 2 for x in seg) / period
        out[i] = var ** 0.5
    return out


MA_TYPES = ["SMA", "EMA", "WMA", "RMA"]
SOURCES = ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"]


def price_source(candles: list[dict], source: str) -> List[float]:
    """Pull a named price series (see SOURCES) out of candles."""
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


def ma(values: List[float], ma_type: str, length: int) -> List[Num]:
    """Dispatch to a moving average by name (see MA_TYPES)."""
    if ma_type == "EMA":
        return ema(values, length)
    if ma_type == "WMA":
        return wma(values, length)
    if ma_type == "RMA":
        return rma(values, length)
    return sma(values, length)  # SMA (default)


def rolling_percentile_rank(values: List[Num], window: int) -> List[Num]:
    """Percentile rank (0-100) of values[i] within the last `window` values.

    Warm-up (None) inputs are ignored; an output is produced only once a full
    `window` of defined values ends at i. Used to detect a Bollinger "squeeze"
    (bandwidth in a low percentile of its recent range).
    """
    n = len(values)
    out: List[Num] = [None] * n
    if window <= 0:
        return out
    for i in range(window - 1, n):
        cur = values[i]
        if cur is None:
            continue
        seg = values[i - window + 1 : i + 1]
        if any(v is None for v in seg):
            continue
        below = sum(1 for v in seg if v <= cur)
        out[i] = 100.0 * below / window
    return out
