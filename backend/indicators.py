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
