#!/usr/bin/env bash
# The incident team's knowledge base (Foundry IQ on Azure AI Search), from any folder.
#
# Usage: scripts/knowledge.sh <command> [args]
#
#   setup                      Create or update the index, upload the synthetic manuals and
#                              procedures (backend/app/fixtures/knowledge/) and the knowledge base
#   search "<query>"           What search_manuals returns: passages with citations, after Prompt Shields
#   search "<query>" --procedures   Search safety procedures instead of manuals
#   search "<query>" --model HX-200 Prefer passages for one equipment model
#
# Needs AZURE_SEARCH_ENDPOINT and CONTENT_SAFETY_ENDPOINT in .env, `az login`, and on the search
# service the Search Service Contributor + Search Index Data Contributor roles (see docs/commands.md).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3

case "${1:-help}" in
  setup | search) exec "$PYTHON" -m backend.app.agents.knowledge_cli "$@" ;;
  help | -h | --help) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) echo "unknown command: $1" >&2; exit 1 ;;
esac
