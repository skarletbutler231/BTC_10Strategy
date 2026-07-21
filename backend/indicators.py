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


# ---------------------------------------------------------------------------
# Value-series helpers. These operate on a plain list of floats (a "source")
# rather than candle dicts, so moving averages, dispersion and Bollinger Bands
# can be built on any series a strategy needs (close, hl2, ohlc4, bandwidth,
# ...). Output is index-aligned with None for warm-up bars, matching the
# candle-based helpers above.
# ---------------------------------------------------------------------------

# Source selector codes (documented in strategy `help` text so the numeric
# dashboard input maps to a readable choice).
SOURCE_LABELS = {0: "close", 1: "open", 2: "high", 3: "low",
                 4: "hl2", 5: "hlc3", 6: "ohlc4"}


def source(candles: list[dict], code: int) -> List[float]:
    """Extract a price source series from candles. See SOURCE_LABELS."""
    code = int(code)
    out: List[float] = []
    for c in candles:
        o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
        if code == 1:
            v = o
        elif code == 2:
            v = h
        elif code == 3:
            v = l
        elif code == 4:
            v = (h + l) / 2.0
        elif code == 5:
            v = (h + l + cl) / 3.0
        elif code == 6:
            v = (o + h + l + cl) / 4.0
        else:  # 0 or unknown -> close
            v = cl
        out.append(v)
    return out


def sma(values: List[float], period: int) -> List[Num]:
    """Simple moving average (O(n) via a running sum). None until `period`."""
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


def wma(values: List[Num], period: int) -> List[Num]:
    """Linearly-weighted moving average (most-recent value weighted highest).

    Tolerates leading None values in `values` so it can be layered (e.g. HMA
    takes the WMA of a series that itself has a warm-up prefix)."""
    n = len(values)
    out: List[Num] = [None] * n
    if period <= 0:
        return out
    denom = period * (period + 1) / 2.0
    for i in range(period - 1, n):
        window = values[i - period + 1: i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(v * (j + 1) for j, v in enumerate(window)) / denom
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


def hma(values: List[float], period: int) -> List[Num]:
    """Hull moving average: WMA(2*WMA(n/2) - WMA(n), round(sqrt(n)))."""
    n = len(values)
    if period <= 1:
        return list(values)
    half = max(1, period // 2)
    sqrt_n = max(1, int(round(period ** 0.5)))
    w_half = wma(values, half)
    w_full = wma(values, period)
    diff: List[Num] = [
        (2.0 * a - b) if (a is not None and b is not None) else None
        for a, b in zip(w_half, w_full)
    ]
    return wma(diff, sqrt_n)


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


# Moving-average type codes (documented in strategy `help` text).
MA_TYPE_LABELS = {0: "SMA", 1: "EMA", 2: "WMA", 3: "RMA", 4: "HMA"}


def moving_average(values: List[float], period: int, ma_type: int) -> List[Num]:
    """Dispatch to a moving average by MA_TYPE_LABELS code."""
    t = int(ma_type)
    if t == 1:
        return ema(values, period)
    if t == 2:
        return wma(values, period)
    if t == 3:
        return rma(values, period)
    if t == 4:
        return hma(values, period)
    return sma(values, period)  # 0 or unknown


def bollinger(values: List[float], period: int, mult: float):
    """Bollinger Bands on `values`: returns (basis, upper, lower) lists.

    basis = SMA(period); band half-width = mult * population stdev(period)."""
    basis = sma(values, period)
    sd = rolling_std(values, period)
    n = len(values)
    upper: List[Num] = [None] * n
    lower: List[Num] = [None] * n
    for i in range(n):
        b, d = basis[i], sd[i]
        if b is None or d is None:
            continue
        upper[i] = b + mult * d
        lower[i] = b - mult * d
    return basis, upper, lower
