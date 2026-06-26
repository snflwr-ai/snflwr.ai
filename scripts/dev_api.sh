#!/usr/bin/env bash
#
# dev_api.sh — lightweight dev runner for the snflwr.ai API server alone.
#
# Why this exists:
#   * Hot-reload — sets API_RELOAD=true, so the server re-loads on any .py
#     change. No more manual restarts for backend edits. (Static dashboard
#     files under api/static/ already serve from disk; just hard-reload the
#     browser.)
#   * Clean stop — terminates ALL listeners on the API port before starting,
#     so leftover uvicorn workers from a previous run never wedge the port
#     with "Address already in use".
#
# For the full stack (Ollama + Open WebUI via Docker, model build, etc.) use
# ./start_snflwr.sh instead. This script is intentionally API-only for fast
# backend iteration.
#
# Usage:  ./scripts/dev_api.sh        (Ctrl+C to stop)

set -euo pipefail

# Repo root, regardless of where this is invoked from.
cd "$(cd "$(dirname "$0")/.." && pwd)"

PORT="${API_PORT:-39150}"

stop_existing() {
  command -v lsof >/dev/null 2>&1 || { echo "lsof not found — skipping pre-clean"; return 0; }
  local pids
  pids="$(lsof -ti :"$PORT" 2>/dev/null || true)"
  [ -z "$pids" ] && return 0
  echo "Stopping existing API on :$PORT (pids: $pids)"
  # Graceful first — uvicorn drains its workers on SIGTERM…
  kill $pids 2>/dev/null || true
  for _ in $(seq 1 10); do
    pids="$(lsof -ti :"$PORT" 2>/dev/null || true)"
    [ -z "$pids" ] && return 0
    sleep 1
  done
  # …then force any survivors (incl. orphaned workers) so the port frees.
  pids="$(lsof -ti :"$PORT" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "Force-killing survivors: $pids"
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi
}

# Activate a virtualenv if present (.venv preferred, then venv); otherwise
# fall back to whatever python is on PATH.
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [ -d venv ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# Load .env so required secrets (INTERNAL_API_KEY, DB_ENCRYPTION_KEY, …) are
# present — otherwise the server auto-generates ephemeral ones and breaks
# server-to-server auth (e.g. Open WebUI ↔ proxy).
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Safeguard: if the dev DB is an UNENCRYPTED SQLite file but encryption is on
# (the default), the server crashes at startup trying to SQLCipher-decrypt it
# ("file is not a database"). Detect that case and disable encryption for this
# run so a plaintext dev DB just works. An encrypted DB is left untouched.
DB_FILE="${DB_PATH:-data/snflwr.db}"
if [ -f "$DB_FILE" ] && head -c 16 "$DB_FILE" 2>/dev/null | grep -q "SQLite format 3" \
   && [ "${DB_ENCRYPTION_ENABLED:-true}" != "false" ]; then
  echo "Note: $DB_FILE is an unencrypted SQLite DB → setting DB_ENCRYPTION_ENABLED=false for this run."
  echo "      (Persist it in .env to make every launch path consistent.)"
  export DB_ENCRYPTION_ENABLED=false
fi

stop_existing

# Hot-reload mode. uvicorn runs a single worker under a file-watching reloader
# (see uvicorn.run(..., reload=API_RELOAD) in api/server.py).
export API_RELOAD=true

echo "Starting snflwr.ai API (hot-reload) on http://localhost:${PORT}/dashboard"
echo "Press Ctrl+C to stop."

# exec so Ctrl+C (SIGINT) goes straight to uvicorn, which tears down its
# reload child cleanly — no orphaned workers left holding the port.
exec python -m api.server
