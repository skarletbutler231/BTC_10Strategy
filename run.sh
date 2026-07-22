#!/usr/bin/env bash
# Launch the BTC 10-Strategy backtester dashboard.
#
# Port/host live in .env, which is gitignored — so each checkout keeps its own
# port and branches never conflict over it. Start from the template:
#   cp .env.example .env
# Precedence: inline env var > .env > built-in default.
#   PORT=9000 ./run.sh      # one-off override, ignores .env
set -euo pipefail
cd "$(dirname "$0")"

# Remember inline overrides so .env can't clobber them.
_env_port="${PORT:-}"
_env_host="${HOST:-}"

# Load .env if present (exports every key so the app sees them too).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PORT="${_env_port:-${PORT:-8100}}"
HOST="${_env_host:-${HOST:-0.0.0.0}}"

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
