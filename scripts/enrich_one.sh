#!/usr/bin/env bash
# Wrapper the Go API execs for on-demand enrichment. Sources ops/.env so the
# subprocess gets proxies and S3/MinIO creds, then runs the one-shot CLI.
# Keeps the Go API ignorant of Python env internals.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# A DB URL provided by the caller (the host launchd env) targets a
# host-resolvable address; ops/.env's value targets the container network and
# won't resolve on the host. Preserve the caller's value if present.
_CALLER_DB_URL="${AGF_DATABASE_URL:-}"

if [ -f ops/.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ops/.env
  set +a
fi

if [ -n "$_CALLER_DB_URL" ]; then
  export AGF_DATABASE_URL="$_CALLER_DB_URL"
fi

# Package is not installed editable — make `agf` importable from src/.
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"

exec python -m agf.enrich_full.enrich_one "$@"
