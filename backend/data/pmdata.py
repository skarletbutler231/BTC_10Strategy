"""Client for the PMData daily-archive API (api.pmdata.dev).

PMData records Polymarket's websocket feeds and republishes them as one ZIP per
(series, data_type, day); each ZIP holds one Parquet per market. For BTC 5m that
is 288 markets and ~30M L2 events a day.

Billing is what shapes this module: **PMData counts usage by day unlocked**, and
an unlocked day is then free forever — across every series and data_type. So the
archives are downloaded once to disk and never re-fetched; the folded SQLite
tables are always rebuildable from them without spending quota again.

Archive layout (root = PMDATA_ARCHIVE, else a ``pmdata`` dir beside market.db):

    <root>/<series>/<data_type>/<series>_<data_type>_<YYYY-MM-DD>.zip

Recording starts, per PMData's docs: btc-5m 2026-02-13; btc-15m/btc-1h
2026-01-26. Today's archive only appears after the day closes.
"""

from __future__ import annotations

import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from .. import db

BASE = "https://api.pmdata.dev"
SERIES_START = {           # first day each series has an archive for
    "btc-5m": date(2026, 2, 13),
    "btc-15m": date(2026, 1, 26),
    "btc-1h": date(2026, 1, 26),
}
DATA_TYPES = ("poly_l2", "poly_trade", "onchain_fills")
TIMEOUT = (30, 300)        # (connect, read) seconds — a day archive is ~330 MB


class PMDataError(RuntimeError):
    pass


def api_key() -> str:
    key = db.env_value("PMDATA_API_KEY")
    if not key:
        raise PMDataError("PMDATA_API_KEY is not set (environment or repo-root .env)")
    return key


def archive_root() -> Path:
    """Where day archives live.

    Defaults beside the shared market.db rather than inside the checkout: the
    archive is tens of GB and, like the DB, is worth sharing between checkouts
    instead of duplicating per branch.
    """
    env = db.env_value("PMDATA_ARCHIVE")
    return Path(env).expanduser() if env else db.db_path().parent / "pmdata"


def day_file(series: str, data_type: str, day: date) -> Path:
    return (archive_root() / series / data_type /
            f"{series}_{data_type}_{day:%Y-%m-%d}.zip")


def day_url(series: str, data_type: str, day: date) -> str:
    name = f"{series}_{data_type}_{day:%Y-%m-%d}.zip"
    return f"{BASE}/polymarket/{series}/{data_type}/{name}"


def day_range(series: str, start: "date | None", end: "date | None") -> "list[date]":
    """Days to fetch, clamped to what the series can actually have.

    The upper bound is yesterday (UTC): PMData publishes a day only once it has
    closed, so asking for today is a guaranteed 404.
    """
    lo = start or SERIES_START.get(series)
    if lo is None:
        raise PMDataError(f"no known recording start for series {series!r}; pass --from")
    lo = max(lo, SERIES_START.get(series, lo))
    # UTC, not local: PMData's day boundaries are UTC, so a machine behind UTC
    # would otherwise ask for a day that has not closed yet and 404.
    newest = datetime.now(timezone.utc).date() - timedelta(days=1)
    hi = min(end or newest, newest)
    return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)] if hi >= lo else []


def _valid_zip(path: Path) -> bool:
    """A complete, readable archive with at least one member."""
    try:
        with zipfile.ZipFile(path) as z:
            return bool(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def download_day(series: str, data_type: str, day: date, *,
                 force: bool = False, session: "requests.Session | None" = None) -> dict:
    """Fetch one day archive into the local store.

    Idempotent and resumable: an already-valid file is a no-op (and costs no
    quota), and a truncated ``.part`` from an interrupted run is continued with a
    Range request rather than restarted. Returns a status dict; a missing day
    (404) is reported as ``status='missing'`` rather than raised, so a backfill
    can stride over gaps in PMData's coverage.
    """
    dest = day_file(series, data_type, day)
    if dest.exists() and not force:
        if _valid_zip(dest):
            return {"day": day, "status": "cached", "bytes": dest.stat().st_size}
        dest.unlink()                       # corrupt/truncated -> refetch

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".zip.part")
    have = part.stat().st_size if part.exists() else 0

    s = session or requests.Session()
    headers = {"api_key": api_key(), "User-Agent": "Mozilla/5.0"}
    if have:
        headers["Range"] = f"bytes={have}-"

    t0 = time.time()
    with s.get(day_url(series, data_type, day), headers=headers,
               stream=True, timeout=TIMEOUT) as r:
        if r.status_code == 404:
            return {"day": day, "status": "missing", "bytes": 0}
        if r.status_code == 416:            # already have the whole body
            part.rename(dest)
            return {"day": day, "status": "ok", "bytes": dest.stat().st_size,
                    "secs": time.time() - t0}
        if r.status_code not in (200, 206):
            raise PMDataError(
                f"{series}/{data_type} {day}: HTTP {r.status_code} {r.text[:200]}")
        # A 200 to a Range request means the server ignored it — start over.
        mode = "ab" if (have and r.status_code == 206) else "wb"
        if mode == "wb":
            have = 0
        with open(part, mode) as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)

    if not _valid_zip(part):
        part.unlink(missing_ok=True)
        raise PMDataError(f"{series}/{data_type} {day}: downloaded archive is not a valid zip")
    part.rename(dest)
    n = dest.stat().st_size
    return {"day": day, "status": "ok", "bytes": n, "secs": time.time() - t0}


def local_days(series: str, data_type: str) -> "list[date]":
    """Days already present and valid in the local archive, ascending."""
    d = archive_root() / series / data_type
    if not d.is_dir():
        return []
    out = []
    prefix = f"{series}_{data_type}_"
    for p in d.glob(f"{prefix}*.zip"):
        try:
            out.append(date.fromisoformat(p.stem[len(prefix):]))
        except ValueError:
            continue
    return sorted(out)


def archive_size() -> "tuple[int, int]":
    """(file count, total bytes) under the archive root."""
    root = archive_root()
    if not root.is_dir():
        return 0, 0
    files = [p for p in root.rglob("*.zip") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


__all__ = ["PMDataError", "api_key", "archive_root", "day_file", "day_url",
           "day_range", "download_day", "local_days", "archive_size",
           "SERIES_START", "DATA_TYPES"]
