#!/usr/bin/env bash
# Launch the BTC 10-Strategy backtester dashboard.
#   PORT=8100 ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8100}"
HOST="${HOST:-0.0.0.0}"

# Use an existing venv if present, otherwise system python.
if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python3 -c "import fastapi, uvicorn" 2>/dev/null || {
  echo "Installing dependencies…"
  python3 -m pip install -r requirements.txt
}

echo "Dashboard → http://localhost:${PORT}"
exec python3 -m uvicorn backend.main:app --host "$HOST" --port "$PORT"
