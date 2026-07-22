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


def rolling_high_low(candles: list[dict], window: int):
    """Donchian channel: (lowest low, highest high) over the last `window` bars.

    Returns two lists; entries are None until `window` bars are available.
    """
    n = len(candles)
    lo: List[Num] = [None] * n
    hi: List[Num] = [None] * n
    if window <= 0:
        return lo, hi
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    for i in range(window - 1, n):
        hi[i] = max(highs[i - window + 1: i + 1])
        lo[i] = min(lows[i - window + 1: i + 1])
    return lo, hi


def adx(candles: list[dict], period: int) -> List[Num]:
    """Wilder's ADX (0-100) — trend STRENGTH, direction-agnostic.

    High ADX = a directional/trending market; low ADX = choppy/ranging. Built the
    standard way: +DM/-DM smoothed by Wilder, DX from the DI spread, ADX = RMA(DX).
    """
    n = len(candles)
    out: List[Num] = [None] * n
    if period <= 0 or n < 2 * period + 1:
        return out

    tr = true_ranges(candles)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = candles[i]["high"] - candles[i - 1]["high"]
        dn = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm[i] = up if (up > dn and up > 0) else 0.0
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0

    # Wilder-smoothed sums seeded over bars 1..period
    sm_tr = sum(tr[1: period + 1])
    sm_p = sum(plus_dm[1: period + 1])
    sm_m = sum(minus_dm[1: period + 1])

    dx_vals: List[float] = []
    dx_idx: List[int] = []
    for i in range(period, n):
        if i > period:
            sm_tr = sm_tr - sm_tr / period + tr[i]
            sm_p = sm_p - sm_p / period + plus_dm[i]
            sm_m = sm_m - sm_m / period + minus_dm[i]
        if sm_tr <= 0:
            dx = 0.0
        else:
            di_p = 100.0 * sm_p / sm_tr
            di_m = 100.0 * sm_m / sm_tr
            tot = di_p + di_m
            dx = 0.0 if tot == 0 else 100.0 * abs(di_p - di_m) / tot
        dx_vals.append(dx)
        dx_idx.append(i)

    if len(dx_vals) < period:
        return out
    val = sum(dx_vals[:period]) / period        # seed ADX with an SMA of DX
    out[dx_idx[period - 1]] = val
    for k in range(period, len(dx_vals)):
        val = (val * (period - 1) + dx_vals[k]) / period
        out[dx_idx[k]] = val
    return out


def efficiency_ratio(candles: list[dict], period: int) -> List[Num]:
    """Kaufman Efficiency Ratio (0-1): net move / total path over `period` bars.

    Near 1 = a clean, efficient trend; near 0 = choppy back-and-forth.
    """
    n = len(candles)
    out: List[Num] = [None] * n
    if period <= 0 or n <= period:
        return out
    closes = [c["close"] for c in candles]
    diffs = [0.0] * n
    for i in range(1, n):
        diffs[i] = abs(closes[i] - closes[i - 1])
    path = sum(diffs[1: period + 1])
    for i in range(period, n):
        if i > period:
            path += diffs[i] - diffs[i - period]
        net = abs(closes[i] - closes[i - period])
        out[i] = (net / path) if path > 0 else 0.0
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


def donchian(candles: list[dict], length: int):
    """Donchian channel -> (upper, lower): the highest high and lowest low over
    the last `length` bars *including* the current one, so `high[i] >= upper[i]`
    means bar i just made a new `length`-bar high."""
    n = len(candles)
    up: List[Num] = [None] * n
    lo: List[Num] = [None] * n
    if length <= 0 or n < length:
        return up, lo
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    for i in range(length - 1, n):
        up[i] = max(highs[i - length + 1: i + 1])
        lo[i] = min(lows[i - length + 1: i + 1])
    return up, lo


def stochastic(candles: list[dict], k_length: int, d_length: int):
    """Stochastic oscillator -> (%K, %D), each 0-100 and index-aligned.

    %K = 100 * (close - lowest low) / (highest high - lowest low) over
    `k_length` bars; %D = simple average of %K over `d_length` bars. A flat
    window (high == low) yields a neutral 50."""
    n = len(candles)
    k: List[Num] = [None] * n
    d: List[Num] = [None] * n
    if k_length <= 0 or d_length <= 0 or n < k_length:
        return k, d
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    for i in range(k_length - 1, n):
        hh = max(highs[i - k_length + 1: i + 1])
        ll = min(lows[i - k_length + 1: i + 1])
        span = hh - ll
        k[i] = 50.0 if span <= 0 else 100.0 * (candles[i]["close"] - ll) / span
    for i in range(k_length - 1 + d_length - 1, n):
        window = k[i - d_length + 1: i + 1]
        if any(v is None for v in window):
            continue
        d[i] = sum(window) / d_length
    return k, d


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
