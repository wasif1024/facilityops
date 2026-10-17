#!/usr/bin/env bash
# Run FacilityOps AI locally: backend (API), frontend (web app), or both.
#
# Usage: scripts/dev.sh [command] [--entra]     (run from anywhere)
#
#   (no command)   Same as "all"
#   all            Backend and frontend together in one terminal; Ctrl+C stops both
#   backend        Backend only: apply migrations, then the API on http://localhost:8000
#   frontend       Frontend only: the web app on http://localhost:5173 (needs a running backend)
#   help           Show this summary
#
#   --entra        Microsoft Entra sign-in instead of the local identity switcher
#                  (backend AUTH_ENABLED=true, frontend VITE_AUTH_MODE=entra; see docs/identity-setup.md)
#
# The backend reads .env; the frontend reads frontend/.env.local (created from .env.example if missing).
# Ports: API_PORT (default 8000) and WEB_PORT (default 5173).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

ENTRA=0
ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--entra" ]; then
    ENTRA=1
  else
    ARGS+=("$arg")
  fi
done
set -- ${ARGS[@]+"${ARGS[@]}"}

die() { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

port_free() {
  ! lsof -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
}

require_port() {
  port_free "$1" || die "port $1 is already in use ($2). Stop that process, or set $3 to another port."
}

check_backend() {
  [ -x "$ROOT/.venv/bin/uvicorn" ] || die "no Python environment yet. Run once:
  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt"
  [ -f "$ROOT/.env" ] || die "no .env yet. Run once: cp .env.example .env   (then set DATABASE_URL)"
  require_port "$API_PORT" "backend" API_PORT
}

check_frontend() {
  # nvm is a shell function, so it has to be loaded into this script (Homebrew or standard install).
  local nvm_sh="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [ ! -s "$nvm_sh" ] && command -v brew >/dev/null 2>&1; then
    nvm_sh="$(brew --prefix nvm 2>/dev/null)/nvm.sh"
  fi
  if [ -s "$nvm_sh" ]; then
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    # shellcheck disable=SC1090
    . "$nvm_sh"
    nvm use "$(cat frontend/.nvmrc)" >/dev/null \
      || die "Node $(cat frontend/.nvmrc) is not installed. Run: nvm install $(cat frontend/.nvmrc)"
  fi
  command -v node >/dev/null 2>&1 || die "Node.js not found. Install nvm and Node 24 (see README, What to install first)."
  local major
  major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" -ge 24 ] || die "Node 24 or newer is needed (found $(node --version)). Run: nvm install 24 && nvm use 24"
  if [ ! -d frontend/node_modules ]; then
    info "installing frontend packages (first run)"
    (cd frontend && npm install)
  fi
  if [ ! -f frontend/.env.local ]; then
    info "creating frontend/.env.local from .env.example"
    cp frontend/.env.example frontend/.env.local
  fi
  require_port "$WEB_PORT" "frontend" WEB_PORT
}

run_backend() {
  info "applying database migrations"
  "$ROOT/scripts/db.sh"
  local auth=false
  [ "$ENTRA" = 1 ] && auth=true
  info "backend on http://localhost:$API_PORT (AUTH_ENABLED=$auth)"
  AUTH_ENABLED="$auth" exec "$ROOT/.venv/bin/uvicorn" backend.app.main:create_app \
    --factory --reload --port "$API_PORT"
}

run_frontend() {
  local mode=dev
  [ "$ENTRA" = 1 ] && mode=entra
  info "frontend on http://localhost:$WEB_PORT (VITE_AUTH_MODE=$mode, API http://localhost:$API_PORT)"
  cd "$ROOT/frontend"
  VITE_AUTH_MODE="$mode" VITE_API_BASE_URL="http://localhost:$API_PORT" \
    exec npx vite --port "$WEB_PORT" --strictPort
}

# Prefix every line so the two logs can be told apart in one terminal.
prefixed() {
  local label="$1"
  shift
  "$@" 2>&1 | awk -v label="$label" '{ print label " " $0; fflush() }'
}

run_all() {
  check_backend
  check_frontend
  # Ctrl+C (or either side stopping) stops everything this script started.
  trap 'trap - INT TERM EXIT; kill 0 2>/dev/null' INT TERM EXIT
  prefixed "[api]" run_backend &
  local api=$!
  prefixed "[web]" run_frontend &
  local web=$!
  echo
  echo "    Web app: http://localhost:$WEB_PORT     API: http://localhost:$API_PORT     Ctrl+C stops both"
  echo
  # Plain polling: macOS ships bash 3.2, which has no "wait -n".
  while kill -0 "$api" 2>/dev/null && kill -0 "$web" 2>/dev/null; do
    sleep 1
  done
  kill -0 "$api" 2>/dev/null || echo "==> backend stopped; stopping the frontend" >&2
  kill -0 "$web" 2>/dev/null || echo "==> frontend stopped; stopping the backend" >&2
  exit 1
}

command="${1:-all}"
case "$command" in
  all) run_all ;;
  backend) check_backend && run_backend ;;
  frontend) check_frontend && run_frontend ;;
  help | -h | --help) usage 0 ;;
  *) echo "unknown command: $command" >&2; usage 1 ;;
esac
