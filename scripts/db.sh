#!/usr/bin/env bash
# Database migration helper for FacilityOps AI. Wraps Alembic with safe defaults.
#
# Usage: scripts/db.sh [command] [args]     (run from anywhere)
#
#   (no command)        Set up or update: create the database if needed, then apply all migrations
#   create              Create the database in DATABASE_URL if it does not exist
#   up [target]         Apply migrations (default: all, "head")
#   down [steps|rev]    Roll back (default: 1 step; "base" removes everything, asks first)
#   status              Show the current revision and whether migrations are pending
#   history             List all migrations, oldest last
#   new "message"       Generate a migration from model changes (next id, e.g. 0004_add_vendors)
#   check               Fail if the models and migrations disagree
#   reset               Roll back everything and re-apply (DESTROYS ALL DATA, asks first)
#   sql [range]         Print the SQL instead of running it (default: "base:head")
#   remove-demo-accounts  Delete the demo accounts (alice, fm, tech, local-developer); do this
#                         before using the project for real. Add --force if no other admin exists
#   add-demo-accounts   Create the demo accounts again
#   purge-expired       Delete model inputs/answers and audit records past their retention period
#   help                Show this summary
#
# DATABASE_URL comes from the environment or .env. Destructive commands refuse to run
# when APP_ENV=production. Pass --yes to skip confirmation prompts (for scripts and CI).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -x "$ROOT/.venv/bin/alembic" ]; then
  ALEMBIC="$ROOT/.venv/bin/alembic"
  PYTHON="$ROOT/.venv/bin/python"
else
  ALEMBIC="alembic"
  PYTHON="python3"
fi

ASSUME_YES=0
ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--yes" ] || [ "$arg" = "-y" ]; then
    ASSUME_YES=1
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

# Settings as the app sees them (environment first, then .env), via the app's own config.
setting() {
  "$PYTHON" -c "from backend.app.core.config import get_settings; print(getattr(get_settings(), '$1') or '')"
}

# The database URL with any password masked, for messages.
safe_url() {
  "$PYTHON" -c "from sqlalchemy import make_url; from backend.app.core.config import get_settings; print(make_url(get_settings().database_url).render_as_string(hide_password=True))"
}

refuse_in_production() {
  if [ "$(setting app_env)" = "production" ]; then
    die "refusing to run '$1' with APP_ENV=production"
  fi
}

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -t 0 ] || die "$1 needs confirmation; re-run with --yes"
  printf '%s\n' "$1"
  printf 'Type the database name (%s) to continue: ' "$(db_name)"
  read -r answer
  [ "$answer" = "$(db_name)" ] || die "aborted"
}

db_name() {
  "$PYTHON" -c "from sqlalchemy import make_url; from backend.app.core.config import get_settings; print(make_url(get_settings().database_url).database)"
}

# libpq-style URL (no +psycopg driver) for psql and createdb.
libpq_url() {
  local database="${1:-}"
  "$PYTHON" -c "
from sqlalchemy import make_url
from backend.app.core.config import get_settings
url = make_url(get_settings().database_url).set(drivername='postgresql')
if '$database':
    url = url.set(database='$database')
print(url.render_as_string(hide_password=False))"
}

cmd_create() {
  local name
  name="$(db_name)"
  if psql "$(libpq_url)" -Atc "select 1" >/dev/null 2>&1; then
    info "database '$name' already exists"
    return 0
  fi
  info "creating database '$name'"
  psql "$(libpq_url postgres)" -v ON_ERROR_STOP=1 -qc "CREATE DATABASE \"$name\"" \
    || die "could not create '$name' (is PostgreSQL running? see docs/commands.md)"
}

cmd_up() {
  local target="${1:-head}"
  info "upgrading $(safe_url) to $target"
  "$ALEMBIC" upgrade "$target"
  cmd_status
}

cmd_down() {
  local target="${1:-1}"
  refuse_in_production down
  case "$target" in
    base)
      confirm "This rolls back EVERY migration on $(safe_url) and deletes all tables and data."
      ;;
    *[!0-9]*)
      confirm "This rolls back $(safe_url) to revision '$target'; data in dropped tables is lost."
      ;;
    *)
      confirm "This rolls back $target migration(s) on $(safe_url); data in dropped tables is lost."
      target="-$target"
      ;;
  esac
  info "downgrading to $target"
  "$ALEMBIC" downgrade "$target"
  cmd_status
}

cmd_status() {
  local current head
  current="$("$ALEMBIC" current 2>/dev/null | awk '{print $1}' | tail -1)"
  head="$("$ALEMBIC" heads 2>/dev/null | awk '{print $1}' | tail -1)"
  echo "database: $(safe_url)"
  echo "current:  ${current:-<none, empty schema>}"
  echo "latest:   ${head}"
  if [ "$current" = "$head" ]; then
    echo "status:   up to date"
  else
    echo "status:   migrations pending (run: scripts/db.sh up)"
  fi
}

cmd_history() {
  "$ALEMBIC" history --indicate-current
}

cmd_new() {
  local message="${1:-}"
  [ -n "$message" ] || die 'usage: scripts/db.sh new "describe the change"'
  local slug last next rev
  slug="$(printf '%s' "$message" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '_' | sed 's/^_*//; s/_*$//' | cut -c1-40)"
  last="$(ls backend/migrations/versions/ | grep -E '^[0-9]{4}_' | sort | tail -1 | cut -c1-4)"
  next="$(printf '%04d' $((10#${last:-0} + 1)))"
  rev="${next}_${slug}"
  info "generating $rev from model changes (database must be up to date first)"
  "$ALEMBIC" revision --autogenerate --rev-id "$rev" -m "$message"
  echo
  echo "Next steps:"
  echo "  1. Review backend/migrations/versions/$rev.py (autogenerate misses some changes, e.g. renames)"
  echo "  2. scripts/db.sh up && scripts/db.sh check"
}

cmd_check() {
  "$ALEMBIC" check
}

cmd_reset() {
  refuse_in_production reset
  confirm "This DELETES ALL DATA in $(safe_url) and rebuilds the schema from the migrations."
  info "rolling back everything"
  "$ALEMBIC" downgrade base
  info "re-applying all migrations"
  "$ALEMBIC" upgrade head
  cmd_status
}

cmd_remove_demo_accounts() {
  info "removing demo accounts from $(safe_url)"
  "$PYTHON" -m backend.app.cli remove-demo-accounts "$@"
}

cmd_add_demo_accounts() {
  refuse_in_production add-demo-accounts
  "$PYTHON" -m backend.app.cli add-demo-accounts
}

cmd_purge_expired() {
  info "purging expired transparency records from $(safe_url)"
  "$PYTHON" -m backend.app.cli purge-expired
}

cmd_sql() {
  "$ALEMBIC" upgrade "${1:-base:head}" --sql
}

command="${1:-}"
[ $# -gt 0 ] && shift

case "$command" in
  "") cmd_create && cmd_up ;;
  create) cmd_create ;;
  up) cmd_up "$@" ;;
  down) cmd_down "$@" ;;
  status) cmd_status ;;
  history) cmd_history ;;
  new) cmd_new "$@" ;;
  check) cmd_check ;;
  reset) cmd_reset ;;
  sql) cmd_sql "$@" ;;
  remove-demo-accounts) cmd_remove_demo_accounts "$@" ;;
  add-demo-accounts) cmd_add_demo_accounts ;;
  purge-expired) cmd_purge_expired ;;
  help | -h | --help) usage 0 ;;
  *) echo "unknown command: $command" >&2; usage 1 ;;
esac
