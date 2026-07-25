#!/usr/bin/env bash
# Periodic incremental ingest of the pmqb capture (Chainlink BTCUSD_CL candles +
# Polymarket 5m windows/quotes) into data/market.db.
#
# Designed for a per-minute cron. `flock -n` makes overlapping runs a no-op, so a
# rare slow run (e.g. a cold backfill) can never collide with the next tick.
#
#   crontab:  * * * * * /work/david/PolyMarket/03_BTC_10Strategy/BTC_10Strategy_git/run_ingest.sh >> <proj>/data/ingest_stream.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"

# Load .env so STREAM_FILE / MARKET_DB overrides apply under cron's bare env.
if [[ -f .env ]]; then
  set -a; # shellcheck disable=SC1091
  source .env; set +a
fi

exec /usr/bin/flock -n /tmp/btc10_ingest_stream.lock \
  python3 -m backend.data.ingest_stream "$@"
