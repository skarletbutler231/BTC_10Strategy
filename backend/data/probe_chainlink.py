"""Probe the Chainlink Data Streams BTC/USD feed before building a full ingester.

Answers the three questions that decide the architecture:

  1. Do the credentials + feed id + HMAC signing actually work? (latest report)
  2. How far back does history go? (retention — sweep timestamps into the past)
  3. What is the native update cadence? (reports-per-minute -> resample target)

Run it (creds come from .env):

    python3 -m backend.data.probe_chainlink

Nothing is written to the DB; this only reads and prints.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .. import chainlink


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main() -> int:
    try:
        client_id, _, feed_id = chainlink._config()
    except chainlink.ChainlinkError as e:
        print(f"config error: {e}")
        return 1

    print(f"client id : {client_id[:8]}…{client_id[-4:]}")
    print(f"feed id   : {feed_id or '(unset!)'}")
    print()

    # 1) latest report — validates auth + feed id + decode -------------------
    print("[1] latest report")
    try:
        rep = chainlink.fetch_latest()
        px = chainlink.report_price(rep)
        print(f"    BTC/USD = ${px['price']:,.2f}   "
              f"(bid ${px['bid']:,.2f} / ask ${px['ask']:,.2f})")
        print(f"    as of   = {_fmt(px['feed_time'])}")
        sane = 1_000 < px["price"] < 10_000_000
        print(f"    decode  = {'OK (price looks sane)' if sane else 'SUSPECT — check feed id / schema'}")
    except chainlink.ChainlinkError as e:
        print(f"    FAILED: {e}")
        print("    -> fix auth/feed id before probing history.")
        return 1
    print()

    # 2) retention sweep — how far back can we fetch? ------------------------
    print("[2] history retention (fetch a report at N ago)")
    now = int(time.time())
    windows = [
        ("1 day", 86400), ("7 days", 7 * 86400), ("13 days", 13 * 86400),
        ("14 days", 14 * 86400), ("15 days", 15 * 86400), ("20 days", 20 * 86400),
        ("30 days", 30 * 86400),
    ]
    oldest_ok = None
    for label, delta in windows:
        target = now - delta
        try:
            rep = chainlink.fetch_at(target)
            px = chainlink.report_price(rep)
            drift = px["feed_time"] - target
            print(f"    {label:>9} ago  OK   got {_fmt(px['feed_time'])} "
                  f"(±{drift:+d}s)  ${px['price']:,.2f}")
            oldest_ok = label
        except chainlink.ChainlinkError as e:
            msg = str(e).splitlines()[0][:80]
            print(f"    {label:>9} ago  ---  {msg}")
    print(f"    -> oldest fetchable: {oldest_ok or 'none (real-time only)'}")
    print()

    # 3) native cadence — reports per minute in a recent window --------------
    print("[3] native cadence (reports in the last ~2 min via /page)")
    try:
        reports = chainlink.fetch_page(now - 120, limit=100)
        n = len(reports)
        if n >= 2:
            ts = sorted(chainlink.report_price(r)["feed_time"] for r in reports)
            span = max(1, ts[-1] - ts[0])
            print(f"    {n} reports over {span}s  ≈ {n / span:.2f} reports/sec")
            print(f"    -> plenty to build 1m/5m OHLC candles" if n / span > 0.1
                  else "    -> sparse; check whether this is the right stream")
        else:
            print(f"    only {n} report(s) returned — /page may be limited on this key")
    except chainlink.ChainlinkError as e:
        print(f"    /page not available: {str(e).splitlines()[0]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
