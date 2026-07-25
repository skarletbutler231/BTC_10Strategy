#!/usr/bin/env bash
# Unified market.db updater — one entry point for every ingest job.
#
# Each job keeps its OWN flock lock, so the fast per-minute job and the slow
# 30-minute job never block one another, and a rare slow run is a no-op instead
# of colliding with the next tick. Cron schedules each job at its own cadence:
#
#   * * * * *    <proj>/run_updaters.sh stream  >> <proj>/data/ingest_stream.log 2>&1
#   */30 * * * * <proj>/run_updaters.sh binance >> <proj>/data/binance_ingest.log 2>&1
#
# Jobs:
#   stream   Chainlink BTCUSD_CL candles + Polymarket pm_window/pm_quote,
#            tailed from the pmqb capture (backend.data.ingest_stream).
#   binance  Binance BTCUSDT 1m candles from data.binance.vision (backend.data.ingest).
#   all      run both, sequentially (manual convenience; default).
set -euo pipefail
cd "$(dirname "$0")"

# Load .env so MARKET_DB / STREAM_FILE apply under cron's bare environment.
if [[ -f .env ]]; then
  set -a; # shellcheck disable=SC1091
  source .env; set +a
fi

STREAM_LOCK=/tmp/btc10_ingest_stream.lock
BINANCE_LOCK=/tmp/btc10_binance_ingest.lock

case "${1:-all}" in
  stream)
    exec /usr/bin/flock -n "$STREAM_LOCK" python3 -m backend.data.ingest_stream ;;
  binance)
    exec /usr/bin/flock -n "$BINANCE_LOCK" python3 -m backend.data.ingest ;;
  all)
    # `|| true`: a held lock (job already running) is an expected skip, not a failure.
    /usr/bin/flock -n "$STREAM_LOCK"  python3 -m backend.data.ingest_stream || true
    /usr/bin/flock -n "$BINANCE_LOCK" python3 -m backend.data.ingest || true ;;
  *)
    echo "usage: $0 {stream|binance|all}" >&2; exit 2 ;;
esac
