"""Chainlink Data Streams client (BTC/USD) — the true settlement source for
Polymarket's 5m/15m up-down markets.

Stdlib-only (urllib/hmac/hashlib), in keeping with the rest of the backend.

Data Streams is a *pull* API on ``api.dataengine.chain.link``. Every request is
authenticated with three headers built from an HMAC-SHA256 signature over the
exact request line (method + path-with-query + body hash + client id + ms
timestamp), keyed by the user secret. See ``_auth_headers``.

A report's price lives inside the ABI-encoded ``fullReport`` blob, not the JSON
top level, so ``decode_full_report`` unpacks the v3 crypto schema to a plain
dict. Prices are 18-decimal fixed point.

Config comes from the environment (loaded from the repo-root ``.env`` if present):
    CHAINLINK_API_KEY       client id (UUID)
    CHAINLINK_USER_SECRET   HMAC signing secret
    CHAINLINK_BTC_FEED_ID   BTC/USD stream feed id (verify vs Polymarket's)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://api.dataengine.chain.link"


class ChainlinkError(RuntimeError):
    pass


# ---- config / env -----------------------------------------------------------

def _load_dotenv() -> None:
    """Populate missing CHAINLINK_* vars from the repo-root .env (best effort)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key.startswith("CHAINLINK_") and key not in os.environ:
            os.environ[key] = val


def _config() -> "tuple[str, str, str]":
    _load_dotenv()
    client_id = os.environ.get("CHAINLINK_API_KEY", "").strip()
    secret = os.environ.get("CHAINLINK_USER_SECRET", "").strip()
    feed_id = os.environ.get("CHAINLINK_BTC_FEED_ID", "").strip()
    if not client_id or not secret:
        raise ChainlinkError(
            "CHAINLINK_API_KEY / CHAINLINK_USER_SECRET not set (see .env)")
    return client_id, secret, feed_id


def btc_feed_id() -> str:
    return _config()[2]


# ---- auth -------------------------------------------------------------------

def _auth_headers(method: str, path: str, body: bytes,
                  client_id: str, secret: str) -> dict:
    """Build the three Data Streams auth headers for one request.

    ``path`` MUST be the exact path + query string that is sent on the wire; the
    signature covers it byte-for-byte, so the same string is used here and in
    the URL. Timestamp is unix milliseconds (server tolerance is ~±5 s).
    """
    ts_ms = int(time.time() * 1000)
    body_hash = hashlib.sha256(body).hexdigest()
    to_sign = f"{method} {path} {body_hash} {client_id} {ts_ms}"
    sig = hmac.new(secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "Authorization": client_id,
        "X-Authorization-Timestamp": str(ts_ms),
        "X-Authorization-Signature-SHA256": sig,
        # Cloudflare in front of the API rejects the default urllib UA (err 1010).
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Accept": "application/json",
    }


def _get(path: str) -> dict:
    """Signed GET of a Data Streams path (must start with '/'); returns JSON."""
    client_id, secret, _ = _config()
    headers = _auth_headers("GET", path, b"", client_id, secret)
    req = urllib.request.Request(BASE_URL + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise ChainlinkError(f"HTTP {e.code} for {path}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise ChainlinkError(f"request failed for {path}: {e}") from e


# ---- endpoints --------------------------------------------------------------

def fetch_latest(feed_id: "str | None" = None) -> dict:
    """Most recent report for the feed. Returns the raw ``report`` dict."""
    feed_id = feed_id or btc_feed_id()
    return _get(f"/api/v1/reports/latest?feedID={feed_id}")["report"]


def fetch_at(ts: int, feed_id: "str | None" = None) -> dict:
    """Report at/around a unix-seconds timestamp. Returns the raw ``report``."""
    feed_id = feed_id or btc_feed_id()
    return _get(f"/api/v1/reports?feedID={feed_id}&timestamp={int(ts)}")["report"]


def fetch_page(start_ts: int, limit: int = 100,
               feed_id: "str | None" = None) -> list:
    """Up to ``limit`` sequential reports from ``start_ts`` (unix seconds)."""
    feed_id = feed_id or btc_feed_id()
    path = (f"/api/v1/reports/page?feedID={feed_id}"
            f"&startTimestamp={int(start_ts)}&limit={int(limit)}")
    return _get(path).get("reports", [])


# ---- report decoding --------------------------------------------------------

def _to_signed(b: bytes) -> int:
    """Interpret a 32-byte big-endian word as a two's-complement int256."""
    v = int.from_bytes(b, "big")
    return v - (1 << 256) if v >= (1 << 255) else v


def decode_full_report(full_report: str) -> dict:
    """Decode a Data Streams v3 crypto ``fullReport`` blob to a price dict.

    Outer envelope is ``abi.encode(bytes32[3] ctx, bytes reportData, ...)``; the
    inner ``reportData`` is the v3 struct
    ``(feedId, validFrom, observations, nativeFee, linkFee, expiresAt,
      benchmark, bid, ask)`` — all 32-byte words, prices 18-decimal.
    """
    raw = bytes.fromhex(full_report[2:] if full_report.startswith("0x") else full_report)
    # word 3 = offset (bytes) to the dynamic reportData within the tuple body.
    rd_off = int.from_bytes(raw[96:128], "big")
    rd_len = int.from_bytes(raw[rd_off:rd_off + 32], "big")
    rd = raw[rd_off + 32:rd_off + 32 + rd_len]

    def w(i: int) -> bytes:
        return rd[i * 32:(i + 1) * 32]

    return {
        "valid_from": int.from_bytes(w(1), "big"),
        "observations_ts": int.from_bytes(w(2), "big"),
        "price": _to_signed(w(6)) / 1e18,
        "bid": _to_signed(w(7)) / 1e18,
        "ask": _to_signed(w(8)) / 1e18,
    }


def report_price(report: dict) -> dict:
    """Merge a raw report's timestamps with its decoded price fields."""
    dec = decode_full_report(report["fullReport"])
    dec["feed_time"] = int(report.get("observationsTimestamp") or dec["observations_ts"])
    return dec
