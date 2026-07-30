"""Live per-second Binance candles into ``market.db``.

Carries the per-second history seeded by ``backend.data.ingest_btc_1s`` forward
in real time: it subscribes to Binance's ``<symbol>@kline_1s`` websocket and
writes every **closed** 1-second kline into ``candles`` as
``(symbol='BTCUSDT', interval='1s')`` with Binance's real OHLC **and volume**
(the btc_1s.db seed only had open/close, so those older rows carry a synthesised
high/low and volume 0 — see ``ingest_btc_1s``). Note that a genuine 1s kline may
also report zero volume: ~7% of seconds see no trade, and Binance still emits a
flat bar for them.

Unlike the other ingesters this is a **long-running daemon**, not a cron job.
Run it under systemd (or ``nohup``); it is safe to restart at any time.

Staying whole:
  * **Startup / reconnect gap-fill** — everything between the newest 1s candle
    in the DB and now is pulled from the REST klines endpoint before the socket
    tail takes over, so a restart or a dropped connection never leaves a hole.
  * **Periodic reconcile** — every ``--reconcile-sec`` the last
    ``--reconcile-lookback`` seconds are checked for missing seconds and any are
    refetched. A second with no trades yields no websocket event, and a message
    can always be dropped; this is the net that catches both. It costs one
    indexed DB scan when there is nothing to fix.
  * **Idle watchdog** — no message for ``--idle-timeout`` seconds forces a
    reconnect, which is the only reliable liveness signal on a 1/sec feed.

Writes are ``INSERT OR IGNORE`` and batched (``--flush-sec``) so a re-run can
never duplicate, and the per-minute cron ingesters sharing this DB are never
starved of the write lock.

Usage:
    python3 -m backend.data.binance_1s_stream                  # run the daemon
    python3 -m backend.data.binance_1s_stream --backfill-only  # catch up, then exit
    python3 -m backend.data.binance_1s_stream --poll           # REST polling, no websocket dep
    python3 -m backend.data.binance_1s_stream --symbol ETHUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

from .. import binance, db
# Binance moved the archive/API from millisecond to microsecond stamps in 2025;
# reuse the ingester's normaliser rather than assuming a unit here.
from .ingest import _to_seconds

INTERVAL = "1s"
REST_PAGE = 1000               # Binance hard cap of klines per request
LOCK_FILE = "/tmp/btc10_binance_1s_stream.lock"

WS_HOSTS = [
    "wss://data-stream.binance.vision",   # public market-data mirror, no key, not geo-blocked
    "wss://stream.binance.com:9443",
]


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  {msg}", flush=True)


# ---- batched writer ---------------------------------------------------------

class Writer:
    """Buffers candle rows and commits them in batches.

    A commit per second would keep the shared DB's write lock churning for no
    benefit; batching bounds that to one commit per ``flush_sec``.
    """

    def __init__(self, conn, symbol: str, *, flush_sec: float = 5.0, flush_rows: int = 60):
        self.conn = conn
        self.symbol = symbol
        self.flush_sec = flush_sec
        self.flush_rows = flush_rows
        self.buf: list = []
        self.written = 0
        self.last = None            # newest row seen: (ts, o, h, l, c, v)
        self._last_flush = time.time()

    def add(self, row: tuple) -> None:
        self.buf.append(row)
        if self.last is None or row[0] >= self.last[0]:
            self.last = row

    def extend(self, rows: "list[tuple]") -> None:
        for r in rows:
            self.add(r)

    def maybe_flush(self) -> int:
        if len(self.buf) >= self.flush_rows or (time.time() - self._last_flush) >= self.flush_sec:
            return self.flush()
        return 0

    def flush(self) -> int:
        self._last_flush = time.time()
        if not self.buf:
            return 0
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO candles "
            "  (symbol, interval, time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
            [(self.symbol, INTERVAL, *r) for r in self.buf],
        )
        self.conn.commit()
        n = self.conn.total_changes - before
        self.buf.clear()
        self.written += n
        return n


# ---- REST backfill ----------------------------------------------------------

def _rows(klines: list) -> "list[tuple]":
    """Binance kline arrays -> (time_s, open, high, low, close, volume) tuples."""
    return [(_to_seconds(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
            for k in klines]


def _fetch_range(symbol: str, lo: int, hi: int, *, pace: float = 0.15) -> "list[tuple]":
    """Every 1s kline with ``lo <= time <= hi`` (unix seconds), via REST."""
    out: list = []
    cursor = lo
    while cursor <= hi:
        page_hi = min(cursor + REST_PAGE - 1, hi)
        kl = binance._get("/api/v3/klines", {
            "symbol": symbol, "interval": INTERVAL,
            "startTime": cursor * 1000, "endTime": page_hi * 1000 + 999,
            "limit": REST_PAGE,
        })
        rows = _rows(kl) if kl else []
        out.extend(rows)
        # Always advance past the page, even when Binance returns nothing for it
        # (a halted / not-yet-listed window), so the loop can never spin.
        cursor = max(page_hi, rows[-1][0] if rows else page_hi) + 1
        if cursor <= hi:
            time.sleep(pace)
    return out


def _newest(conn, symbol: str) -> "int | None":
    r = conn.execute(
        "SELECT MAX(time) hi FROM candles WHERE symbol=? AND interval=?",
        (symbol, INTERVAL),
    ).fetchone()
    return r["hi"] if r else None


def gap_fill(conn, writer: Writer, symbol: str, *, max_hours: float, cold_minutes: int = 60) -> int:
    """Fill everything between the newest stored 1s candle and the last closed second."""
    end = int(time.time()) - 1                      # the current second is still forming
    newest = _newest(conn, symbol)
    start = (newest + 1) if newest is not None else (end - cold_minutes * 60)
    if start > end:
        return 0

    cap = int(max_hours * 3600)
    if end - start + 1 > cap:
        missed = end - start + 1 - cap
        log(f"WARNING gap is {(end - start + 1) / 3600:.1f}h, over --max-backfill-hours "
            f"({max_hours}h): filling the most recent {max_hours}h only. "
            f"{missed:,} earlier second(s) stay missing — backfill those from the "
            f"Binance Vision 1s archives (data.binance.vision .../klines/{symbol}/1s/).")
        start = end - cap + 1

    log(f"gap-fill {symbol} {start}..{end} "
        f"({datetime.fromtimestamp(start, timezone.utc):%Y-%m-%d %H:%M:%S} .. "
        f"{datetime.fromtimestamp(end, timezone.utc):%H:%M:%S}Z, {end - start + 1:,}s)")
    writer.extend(_fetch_range(symbol, start, end))
    n = writer.flush()
    log(f"gap-fill wrote {n:,} row(s)")
    return n


def _runs(values: "list[int]"):
    """Collapse a sorted second list into contiguous (lo, hi) runs."""
    lo = prev = None
    for v in values:
        if lo is None:
            lo = prev = v
        elif v == prev + 1:
            prev = v
        else:
            yield lo, prev
            lo = prev = v
    if lo is not None:
        yield lo, prev


def reconcile(conn, writer: Writer, symbol: str, *, lookback: int, margin: int = 10) -> int:
    """Refetch any second missing from the recent window.

    ``margin`` keeps the freshest seconds out of scope so a candle still in the
    writer's buffer is not mistaken for a hole.
    """
    end = int(time.time()) - margin
    start = end - lookback + 1
    if start > end:
        return 0
    present = {r["time"] for r in conn.execute(
        "SELECT time FROM candles WHERE symbol=? AND interval=? AND time BETWEEN ? AND ?",
        (symbol, INTERVAL, start, end))}
    missing = [t for t in range(start, end + 1) if t not in present]
    if not missing:
        return 0

    runs = list(_runs(missing))
    log(f"reconcile: {len(missing):,} missing second(s) in the last {lookback}s "
        f"across {len(runs)} run(s); refetching")
    for lo, hi in runs:
        writer.extend(_fetch_range(symbol, lo, hi))
    n = writer.flush()
    log(f"reconcile wrote {n:,} row(s)")
    return n


# ---- websocket tail ---------------------------------------------------------

async def _recv(ws, stop: asyncio.Event, timeout: float):
    """Next message, ``None`` if asked to stop; raises on idle timeout."""
    recv = asyncio.ensure_future(ws.recv())
    halt = asyncio.ensure_future(stop.wait())
    done, pending = await asyncio.wait({recv, halt}, timeout=timeout,
                                       return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    if recv in done:
        return recv.result()
    if halt in done:
        return None
    raise ConnectionError(f"no message for {timeout:.0f}s")


async def _tail(ws_url: str, conn, writer: Writer, cfg, stop: asyncio.Event) -> None:
    from websockets.asyncio.client import connect

    # close_timeout is deliberately short: the default 10s spends the whole
    # shutdown budget waiting for Binance's close frame, which delays exit under
    # systemd for no gain — the rows are already committed by then.
    async with connect(ws_url, ping_interval=20, ping_timeout=20,
                       open_timeout=15, close_timeout=2, max_queue=4096) as ws:
        log(f"connected {ws_url}")
        next_reconcile = time.time() + cfg.reconcile_sec
        next_status = time.time() + cfg.status_sec
        while not stop.is_set():
            raw = await _recv(ws, stop, cfg.idle_timeout)
            if raw is None:                          # asked to stop
                writer.flush()                       # commit before the close handshake
                break
            try:
                k = json.loads(raw).get("k")
            except Exception:                        # noqa: BLE001 - ignore a malformed frame
                continue
            if k and k.get("x"):                     # only sealed 1s klines
                writer.add((_to_seconds(k["t"]), float(k["o"]), float(k["h"]),
                            float(k["l"]), float(k["c"]), float(k["v"])))
                writer.maybe_flush()

            now = time.time()
            if now >= next_reconcile:
                writer.flush()                       # buffered rows must be visible first
                reconcile(conn, writer, cfg.symbol, lookback=cfg.reconcile_lookback)
                next_reconcile = now + cfg.reconcile_sec
            if now >= next_status:
                last = writer.last
                tip = (f"{datetime.fromtimestamp(last[0], timezone.utc):%H:%M:%S}Z "
                       f"close={last[4]:,.2f}") if last else "(no candle yet)"
                log(f"ok  rows={writer.written:,}  last={tip}")
                next_status = now + cfg.status_sec
    writer.flush()


async def _daemon(cfg) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    conn = db.connect(cfg.db_path)
    writer = Writer(conn, cfg.symbol, flush_sec=cfg.flush_sec)
    host_i = 0
    backoff = 1.0
    try:
        while not stop.is_set():
            try:
                gap_fill(conn, writer, cfg.symbol, max_hours=cfg.max_backfill_hours)
                url = f"{WS_HOSTS[host_i % len(WS_HOSTS)]}/ws/{cfg.symbol.lower()}@kline_{INTERVAL}"
                await _tail(url, conn, writer, cfg, stop)
                backoff = 1.0
            except Exception as e:                   # noqa: BLE001 - any drop is retryable
                writer.flush()
                host_i += 1                          # next attempt tries the other host
                log(f"stream error ({type(e).__name__}: {e}); reconnecting in {backoff:.0f}s")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)
    finally:
        writer.flush()
        log(f"stopped. {writer.written:,} row(s) written this run.")
        conn.close()
    return 0


def _poll(cfg) -> int:
    """Websocket-free fallback: repeat the REST gap-fill on a short interval."""
    stop = {"now": False}
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.__setitem__("now", True))

    conn = db.connect(cfg.db_path)
    writer = Writer(conn, cfg.symbol, flush_sec=0.0)
    log(f"REST polling every {cfg.poll_sec}s (no websocket)")
    try:
        while not stop["now"]:
            try:
                gap_fill(conn, writer, cfg.symbol, max_hours=cfg.max_backfill_hours)
            except Exception as e:                   # noqa: BLE001 - keep polling through blips
                log(f"poll error ({type(e).__name__}: {e})")
            for _ in range(int(cfg.poll_sec * 10)):
                if stop["now"]:
                    break
                time.sleep(0.1)
    finally:
        writer.flush()
        log(f"stopped. {writer.written:,} row(s) written this run.")
        conn.close()
    return 0


# ---- entry point ------------------------------------------------------------

def _take_lock(path: str):
    """Fail fast if another copy is already streaming into the same DB."""
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(f"{os.getpid()}\n")
    fh.flush()
    return fh


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="Stream Binance 1s candles into market.db.")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--db", dest="db_path", default=None, help="override MARKET_DB")
    ap.add_argument("--max-backfill-hours", type=float, default=168.0,
                    help="cap on a single REST catch-up (default 168 = 7 days)")
    ap.add_argument("--flush-sec", type=float, default=5.0, help="seconds between commits")
    ap.add_argument("--reconcile-sec", type=float, default=120.0,
                    help="how often to hunt for missing seconds (0 disables)")
    ap.add_argument("--reconcile-lookback", type=int, default=900,
                    help="window of seconds each reconcile checks")
    ap.add_argument("--idle-timeout", type=float, default=30.0,
                    help="reconnect after this long with no message")
    ap.add_argument("--status-sec", type=float, default=300.0, help="heartbeat interval")
    ap.add_argument("--backfill-only", action="store_true", help="catch up via REST, then exit")
    ap.add_argument("--poll", action="store_true", help="REST polling instead of the websocket")
    ap.add_argument("--poll-sec", type=float, default=5.0, help="--poll interval")
    ap.add_argument("--lock-file", default=LOCK_FILE)
    ap.add_argument("--no-lock", action="store_true", help="skip the single-instance guard")
    cfg = ap.parse_args(argv)
    cfg.symbol = cfg.symbol.upper()
    if cfg.reconcile_sec <= 0:
        cfg.reconcile_sec = float("inf")

    lock = None
    if not cfg.no_lock:
        lock = _take_lock(cfg.lock_file)
        if lock is None:
            print(f"another instance holds {cfg.lock_file}; exiting.", file=sys.stderr)
            return 0

    try:
        if cfg.backfill_only:
            conn = db.connect(cfg.db_path)
            writer = Writer(conn, cfg.symbol, flush_sec=0.0)
            try:
                gap_fill(conn, writer, cfg.symbol, max_hours=cfg.max_backfill_hours)
                reconcile(conn, writer, cfg.symbol, lookback=cfg.reconcile_lookback)
            finally:
                writer.flush()
                conn.close()
            return 0
        if cfg.poll:
            return _poll(cfg)
        try:
            import websockets  # noqa: F401
        except ImportError:
            print("websockets is not installed; use --poll for the REST fallback "
                  "or `pip install websockets`.", file=sys.stderr)
            return 1
        log(f"starting {cfg.symbol} {INTERVAL} -> {cfg.db_path or db.db_path()}")
        return asyncio.run(_daemon(cfg))
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
