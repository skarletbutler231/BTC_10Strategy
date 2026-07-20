"""Binance spot klines fetcher.

Uses only the standard library (urllib) so the project has no HTTP dependency.
Primary host is data-api.binance.vision (public market-data mirror, no API key,
not geo-blocked); api.binance.com is used as a fallback.

A "candle" in this project is a plain dict:
    {"time": <int unix seconds>, "open", "high", "low", "close", "volume": <float>}
Time is the candle OPEN time in seconds (what lightweight-charts expects).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
]

# Binance kline interval -> milliseconds per bar (used for pagination).
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "6h": 6 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

MAX_LIMIT = 1000          # Binance hard cap per request
MAX_BARS = 60_000         # our own safety cap for a single backtest


class BinanceError(RuntimeError):
    pass


def _get(path: str, params: dict) -> list:
    """GET a Binance REST path, trying each host until one answers."""
    qs = urllib.parse.urlencode(params)
    last_err = None
    for host in HOSTS:
        url = f"{host}{path}?{qs}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "btc-10strategy/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:  # noqa: BLE001 - try next host
            last_err = e
            continue
    raise BinanceError(f"all Binance hosts failed for {path}: {last_err}")


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """Fetch candles in [start_ms, end_ms], paginating past the 1000-bar cap.

    Returns a list of candle dicts sorted by time ascending, de-duplicated.
    """
    interval = interval.lower()
    if interval not in INTERVAL_MS:
        raise BinanceError(f"unsupported interval: {interval}")
    if start_ms >= end_ms:
        raise BinanceError("start must be before end")

    step = INTERVAL_MS[interval]
    out: list[dict] = []
    cursor = start_ms
    guard = 0

    while cursor < end_ms and len(out) < MAX_BARS:
        guard += 1
        if guard > 500:  # ~500k bars worth of pages; pathological, bail out
            break
        rows = _get(
            "/api/v3/klines",
            {
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": MAX_LIMIT,
            },
        )
        if not rows:
            break
        for r in rows:
            out.append(
                {
                    "time": int(r[0]) // 1000,
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                }
            )
        last_open = int(rows[-1][0])
        nxt = last_open + step
        if nxt <= cursor:  # no forward progress -> stop
            break
        cursor = nxt
        if len(rows) < MAX_LIMIT:  # last page
            break
        time.sleep(0.05)  # be gentle with the public endpoint

    # de-dup by time (pagination overlap) and clip to requested window
    seen = {}
    for c in out:
        if start_ms // 1000 <= c["time"] <= end_ms // 1000:
            seen[c["time"]] = c
    return [seen[t] for t in sorted(seen)]
