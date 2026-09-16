"""Client for the PMData daily-archive API (api.pmdata.dev).

PMData records Polymarket's websocket feeds and republishes them as one ZIP per
(series, data_type, day); each ZIP holds one Parquet per market. For BTC 5m that
is 288 markets and ~30M L2 events a day; BTC 15m is 96 markets and ~110-200 MB.

Billing is what shapes this module. Per PMData's API reference (checked
2026-09-13) **quota is charged per archive download** — 144 units for a 5m day,
48 for 15m, 12 for 1h — and a repeat download is charged again. Only a byte
range starting after byte 0 is free, which is what resuming a ``.part`` file
uses. (Before this, PMData billed by *day unlocked*, which this module's first
backfill ran under.) Either way the rule is the same: archives are downloaded
once to disk and never re-fetched; the folded SQLite tables are always
rebuildable from them without spending quota again.

Archive layout (root = PMDATA_ARCHIVE, else a ``pmdata`` dir beside market.db):

    <root>/<series>/<data_type>/<series>_<data_type>_<YYYY-MM-DD>.zip

Recording starts: btc-5m 2026-02-13; btc-15m 2026-01-26 (every day from there
to 2026-09-12 confirmed published, 31.5 GB); btc-1h 2026-01-26 per PMData's
docs. Today's archive only appears after the day closes.
"""

from __future__ import annotations

import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from .. import db

BASE = "https://api.pmdata.dev/v1/polymarket"
SERIES_START = {           # first day each series has an archive for
    "btc-5m": date(2026, 2, 13),
    "btc-15m": date(2026, 1, 26),
    "btc-1h": date(2026, 1, 26),
}
DATA_TYPES = ("poly_l2", "poly_trade", "onchain_fills")
# Local name (archive paths, pmdata_day rows) -> the v1 API's data_type segment.
# The local names predate the v1 API and are kept so existing archives and
# pmdata_day rows stay valid.
API_DATA_TYPE = {"poly_l2": "l2", "poly_trade": "trades", "onchain_fills": "onchain_fills"}
# Quota units one full archive download costs, per PMData's API reference.
QUOTA_PER_DAY = {"5m": 144, "15m": 48, "1h": 12}
TIMEOUT = (30, 300)        # (connect, read) seconds — a day archive is ~330 MB


class PMDataError(RuntimeError):
    pass


class PMDataQuotaError(PMDataError):
    """HTTP 429: the account's download quota is used up. Every later request
    will fail the same way, so a backfill should stop rather than keep asking."""


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
    """v1 ZIP API: ``/v1/polymarket/{l2|trades|onchain_fills}/YYYY/MM/DD/{asset}-{tf}.zip``.

    ``series`` is ``<asset>-<timeframe>`` (e.g. ``btc-15m``), which is exactly the
    archive's file stem on the v1 API.
    """
    api_type = API_DATA_TYPE.get(data_type, data_type)
    return f"{BASE}/{api_type}/{day:%Y/%m/%d}/{series}.zip"


def quota_cost(series: str, days: int = 1) -> "int | None":
    """Quota units a full download of ``days`` archives of ``series`` costs."""
    unit = QUOTA_PER_DAY.get(series.rsplit("-", 1)[-1])
    return None if unit is None else unit * days


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
        if r.status_code == 429:
            raise PMDataQuotaError(
                f"{series}/{data_type} {day}: download quota used up ({r.text[:200]})")
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


__all__ = ["PMDataError", "PMDataQuotaError", "api_key", "archive_root", "day_file",
           "day_url", "quota_cost", "day_range", "download_day", "local_days",
           "archive_size", "SERIES_START", "DATA_TYPES"]
