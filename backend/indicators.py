"""Pure-Python technical indicators.

Everything works on lists of candle dicts (see binance.py) and returns lists
aligned index-for-index with the input, using None for warm-up bars where the
indicator is not yet defined. Kept dependency-free and explicit so the logic is
easy to read and port.
"""

from __future__ import annotations

from collections import deque
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


def rolling_swing(candles: list[dict], window: int):
    """Rolling window extremes *with their bar indices* -> (hh, ll, hi_i, lo_i).

    Like :func:`rolling_high_low` but it also reports WHERE inside the window
    each extreme sits, which is what turns a pair of extremes into a directed
    swing *leg*: if the high came after the low the leg is up, and vice versa.
    Entries are None until `window` bars are available.

    Computed in O(n) with two monotonic deques rather than re-scanning the
    window at every bar — the difference matters when a parameter sweep walks
    ~1M bars thousands of times. Ties resolve to the MOST RECENT extreme, so a
    flat run is attributed to its latest bar.
    """
    n = len(candles)
    hh: List[Num] = [None] * n
    ll: List[Num] = [None] * n
    hi_i: List[Optional[int]] = [None] * n
    lo_i: List[Optional[int]] = [None] * n
    if window <= 0 or n < window:
        return hh, ll, hi_i, lo_i

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    dmax: deque = deque()   # indices, highs strictly decreasing front -> back
    dmin: deque = deque()   # indices, lows strictly increasing front -> back
    for i in range(n):
        h, l = highs[i], lows[i]
        while dmax and highs[dmax[-1]] <= h:
            dmax.pop()
        dmax.append(i)
        while dmin and lows[dmin[-1]] >= l:
            dmin.pop()
        dmin.append(i)
        lo_bound = i - window + 1
        if dmax[0] < lo_bound:
            dmax.popleft()
        if dmin[0] < lo_bound:
            dmin.popleft()
        if i >= window - 1:
            hi_i[i] = dmax[0]
            lo_i[i] = dmin[0]
            hh[i] = highs[dmax[0]]
            ll[i] = lows[dmin[0]]
    return hh, ll, hi_i, lo_i


def pivots(candles: list[dict], left: int, right: int):
    """Fractal swing pivots -> (pivot_high, pivot_low), index-aligned.

    ``pivot_high[j]`` is ``high[j]`` when that high is the highest of the window
    ``[j-left, j+right]``, else None; ``pivot_low[j]`` mirrors it on lows. Ties
    are inclusive, so a flat plateau marks every bar in it as a pivot.

    LOOK-AHEAD WARNING: a pivot at bar j depends on the `right` bars that come
    AFTER it, so it is not knowable until bar ``j + right``. Callers must not
    read ``pivot_*[j]`` while scanning any bar earlier than ``j + right`` — see
    the confirmation cursor in strategies/reversal.py for the pattern that
    enforces this.

    Computed in O(n) with two monotonic deques — the same technique
    :func:`rolling_swing` uses. Bar j is a pivot high exactly when it attains
    the maximum of the window ``[j-left, j+right]``, so one sliding-window
    maximum answers it for every bar at once. The naive per-bar rescan is
    O(n*(left+right)), which is fine at ``left=3`` but unusable on 1-second
    bars where matching a 150-minute swing needs ``left=9000``.
    """
    n = len(candles)
    ph: List[Num] = [None] * n
    pl: List[Num] = [None] * n
    if left < 0 or right < 0:
        return ph, pl
    width = left + right + 1
    if n < width:
        return ph, pl
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    dmax: deque = deque()   # indices, highs decreasing front -> back
    dmin: deque = deque()   # indices, lows increasing front -> back
    for end in range(n):
        h, l = highs[end], lows[end]
        while dmax and highs[dmax[-1]] <= h:
            dmax.pop()
        dmax.append(end)
        while dmin and lows[dmin[-1]] >= l:
            dmin.pop()
        dmin.append(end)
        start = end - width + 1
        if dmax[0] < start:
            dmax.popleft()
        if dmin[0] < start:
            dmin.popleft()
        if end >= width - 1:
            # the window [start, end] is centred on j, `right` bars back
            j = end - right
            if highs[dmax[0]] == highs[j]:
                ph[j] = highs[j]
            if lows[dmin[0]] == lows[j]:
                pl[j] = lows[j]
    return ph, pl


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


def macd(values: List[float], fast: int, slow: int, signal: int):
    """MACD -> (macd_line, signal_line, histogram), each index-aligned.

    macd = EMA(fast) - EMA(slow); signal = EMA(macd, signal); hist = macd-signal.
    The signal EMA is seeded from the first bar where the MACD line is defined,
    so it warms up off real values rather than treating the None prefix as data.
    """
    n = len(values)
    line: List[Num] = [None] * n
    sig: List[Num] = [None] * n
    hist: List[Num] = [None] * n
    ef, es = ema(values, fast), ema(values, slow)
    for i in range(n):
        a, b = ef[i], es[i]
        if a is not None and b is not None:
            line[i] = a - b

    start = next((i for i, v in enumerate(line) if v is not None), n)
    if start < n:
        for k, v in enumerate(ema([float(x) for x in line[start:]], signal)):
            sig[start + k] = v
    for i in range(n):
        if line[i] is not None and sig[i] is not None:
            hist[i] = line[i] - sig[i]
    return line, sig, hist


def roc(values: List[float], period: int) -> List[Num]:
    """Rate of Change: percent change over `period` bars.

    The plainest momentum measure there is — everything else in the momentum
    family is a smoothing or a bounded rescaling of this idea. Undefined for the
    first `period` bars, and where the reference price is zero.
    """
    n = len(values)
    out: List[Num] = [None] * n
    if period <= 0:
        return out
    for i in range(period, n):
        prev = values[i - period]
        if prev:
            out[i] = (values[i] - prev) / prev * 100.0
    return out


def tsi(values: List[float], long_period: int, short_period: int) -> List[Num]:
    """True Strength Index (-100..100).

    Bar-to-bar momentum, double-smoothed by EMA, divided by the same double
    smoothing of its absolute value. The division is what bounds it: when every
    move is in one direction the two agree and TSI approaches +/-100; when moves
    cancel out the numerator shrinks while the denominator does not.
    """
    n = len(values)
    out: List[Num] = [None] * n
    if n < 2 or long_period <= 0 or short_period <= 0:
        return out
    mom = [0.0] + [values[i] - values[i - 1] for i in range(1, n)]
    absmom = [abs(v) for v in mom]

    def double_smooth(series: List[float]) -> List[Num]:
        first = ema(series, long_period)
        start = next((i for i, v in enumerate(first) if v is not None), n)
        res: List[Num] = [None] * n
        if start >= n:
            return res
        for k, v in enumerate(ema([float(x) for x in first[start:]], short_period)):
            res[start + k] = v
        return res

    num, den = double_smooth(mom), double_smooth(absmom)
    for i in range(n):
        a, b = num[i], den[i]
        if a is not None and b:
            out[i] = 100.0 * a / b
    return out


def awesome_oscillator(candles: list[dict], fast: int, slow: int) -> List[Num]:
    """Bill Williams' Awesome Oscillator: SMA(hl2, fast) - SMA(hl2, slow).

    In price units, so callers must scale it (by ATR) before comparing it with
    anything. Positive means the recent midpoint sits above the older one.
    """
    hl2 = [(c["high"] + c["low"]) / 2.0 for c in candles]
    f, s = sma(hl2, fast), sma(hl2, slow)
    out: List[Num] = [None] * len(candles)
    for i in range(len(candles)):
        if f[i] is not None and s[i] is not None:
            out[i] = f[i] - s[i]
    return out


def ultimate_oscillator(candles: list[dict], p1: int, p2: int, p3: int) -> List[Num]:
    """Wilder's Ultimate Oscillator (0-100) across three lookbacks.

    Buying pressure (close - the lower of low and the previous close) over true
    range, averaged on each horizon and weighted 4:2:1 toward the shortest. The
    three horizons are the point: it is deliberately harder to whipsaw than a
    single-period oscillator. O(n) via running sums.
    """
    n = len(candles)
    out: List[Num] = [None] * n
    periods = sorted({p for p in (p1, p2, p3) if p > 0})
    if n < 2 or len(periods) < 3:
        return out
    a, b, c_ = periods                      # shortest .. longest

    bp: List[float] = [0.0] * n
    tr: List[float] = [0.0] * n
    for i in range(1, n):
        cur, prev_close = candles[i], candles[i - 1]["close"]
        low = min(cur["low"], prev_close)
        bp[i] = cur["close"] - low
        tr[i] = max(cur["high"], prev_close) - low

    sums = {p: [0.0, 0.0] for p in periods}  # period -> [sum bp, sum tr]
    for i in range(1, n):
        for p, acc in sums.items():
            acc[0] += bp[i]
            acc[1] += tr[i]
            if i - p >= 1:                   # drop the bar leaving the window
                acc[0] -= bp[i - p]
                acc[1] -= tr[i - p]
        if i < c_:
            continue
        ta, tb, tc = sums[a][1], sums[b][1], sums[c_][1]
        if ta <= 0 or tb <= 0 or tc <= 0:
            continue
        out[i] = 100.0 * (4.0 * sums[a][0] / ta + 2.0 * sums[b][0] / tb
                          + sums[c_][0] / tc) / 7.0
    return out


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
