#!/usr/bin/env bash
# Unified market.db updater — one entry point for every ingest job.
#
# Each job keeps its OWN flock lock, so the fast per-minute job and the slow
# 30-minute job never block one another, and a rare slow run is a no-op instead
# of colliding with the next tick. Cron schedules each job at its own cadence:
#
#   * * * * *    <proj>/run_updaters.sh stream    >> <proj>/data/ingest_stream.log 2>&1
#   * * * * *    <proj>/run_updaters.sh binance1s >> <proj>/data/binance_1s.log 2>&1
#   * * * * *    <proj>/run_updaters.sh binance1m >> <proj>/data/binance_1m_tail.log 2>&1
#   * * * * *    <proj>/run_updaters.sh twap      >> <proj>/data/twap_ingest.log 2>&1
#   */30 * * * * <proj>/run_updaters.sh binance   >> <proj>/data/binance_ingest.log 2>&1
#   40 1 * * *   <proj>/run_updaters.sh pmdata    >> <proj>/data/pmdata_ingest.log 2>&1
#
# Jobs:
#   stream     Chainlink BTCUSD_CL candles + Polymarket pm_window/pm_quote,
#              tailed from the pmqb capture (backend.data.ingest_stream).
#   twap       Chainlink BTC/USD 30s + 60s TWAPs -> cl_twap, tailed from the same pmqb
#              capture (backend.data.ingest_twap). A SEPARATE job from `stream` on purpose:
#              it keeps its own byte cursor, so it could backfill TWAP history from byte 0
#              without forcing a full pm_quote rewrite through `stream --reset`.
#   binance1s  Binance BTCUSDT *1-second* candles, REST catch-up from the newest
#              stored second to now (backend.data.binance_1s_stream --backfill-only).
#              Per-minute rather than a daemon, so it needs no supervision; the
#              cost is that the 1s series trails now by up to ~a minute.
#   binance    Binance BTCUSDT 1m candles from data.binance.vision (backend.data.ingest).
#              Archive-only, so it necessarily stops at YESTERDAY 23:59 — the vision archive
#              publishes a day only once it has closed.
#   binance1m  The other half of that: REST-fills today's still-forming 1m candles
#              (backend.data.ingest --tail). backend/store.py already splices this tail on at
#              read time, but anything querying the `candles` table directly bypasses that and
#              would see the table end at yesterday 23:59. Cheap (one REST call, no-op when
#              current), and shares the binance lock so it can't race the archive loader.
#   pmdata     Polymarket L2 order book from pmdata.dev (backend.data.ingest_pmdata).
#              Daily, not per-minute: PMData publishes one archive per day once the
#              day has closed, so this picks up yesterday and is a no-op otherwise.
#   all        run every job, sequentially (manual convenience; default).
set -euo pipefail
cd "$(dirname "$0")"

# Load .env so MARKET_DB / STREAM_FILE apply under cron's bare environment.
if [[ -f .env ]]; then
  set -a; # shellcheck disable=SC1091
  source .env; set +a
fi

STREAM_LOCK=/tmp/btc10_ingest_stream.lock
BINANCE_LOCK=/tmp/btc10_binance_ingest.lock
PMDATA_LOCK=/tmp/btc10_pmdata_ingest.lock
# Its own lock, not the stream lock: the two read the same file but write different
# tables, and a slow TWAP backfill must not stall the per-minute pm_quote tail.
TWAP_LOCK=/tmp/btc10_ingest_twap.lock
# Deliberately the same path binance_1s_stream locks internally, so a manually
# started daemon and this cron job exclude each other. The child therefore runs
# --no-lock: it would otherwise deadlock against the flock we already hold here.
BINANCE1S_LOCK=/tmp/btc10_binance_1s_stream.lock

# Catch up at most this many days per pmdata run, so a long outage backfills
# steadily instead of pulling tens of GB in one go.
PMDATA_LOOKBACK_DAYS="${PMDATA_LOOKBACK_DAYS:-7}"
pmdata_args=(--from "$(date -u -d "${PMDATA_LOOKBACK_DAYS} days ago" +%F)" --workers 6)

case "${1:-all}" in
  stream)
    exec /usr/bin/flock -n "$STREAM_LOCK" python3 -m backend.data.ingest_stream ;;
  twap)
    exec /usr/bin/flock -n "$TWAP_LOCK" python3 -m backend.data.ingest_twap ;;
  binance1s)
    exec /usr/bin/flock -n "$BINANCE1S_LOCK" \
      python3 -m backend.data.binance_1s_stream --backfill-only --no-lock ;;
  binance)
    exec /usr/bin/flock -n "$BINANCE_LOCK" python3 -m backend.data.ingest ;;
  binance1m)
    exec /usr/bin/flock -n "$BINANCE_LOCK" python3 -m backend.data.ingest --tail ;;
  pmdata)
    exec /usr/bin/flock -n "$PMDATA_LOCK" python3 -m backend.data.ingest_pmdata "${pmdata_args[@]}" ;;
  all)
    # `|| true`: a held lock (job already running) is an expected skip, not a failure.
    /usr/bin/flock -n "$STREAM_LOCK"  python3 -m backend.data.ingest_stream || true
    /usr/bin/flock -n "$TWAP_LOCK"    python3 -m backend.data.ingest_twap || true
    /usr/bin/flock -n "$BINANCE1S_LOCK" \
      python3 -m backend.data.binance_1s_stream --backfill-only --no-lock || true
    /usr/bin/flock -n "$BINANCE_LOCK" python3 -m backend.data.ingest || true
    /usr/bin/flock -n "$BINANCE_LOCK" python3 -m backend.data.ingest --tail || true
    /usr/bin/flock -n "$PMDATA_LOCK"  python3 -m backend.data.ingest_pmdata "${pmdata_args[@]}" || true ;;
  *)
    echo "usage: $0 {stream|twap|binance1s|binance|binance1m|pmdata|all}" >&2; exit 2 ;;
esac
