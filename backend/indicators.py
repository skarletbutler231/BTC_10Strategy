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
