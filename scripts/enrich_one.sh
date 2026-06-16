#!/usr/bin/env bash
# Wrapper the Go API execs for on-demand enrichment. Runs the one-shot CLI from
# the repo root with the project venv interpreter, mirroring how the worker
# launchd job runs enrichment (com.fielddesk.worker).
#
# Environment comes from the CALLER (the API launchd plist), which carries the
# host-correct AGF_DATABASE_URL, S3/MinIO creds, and AGF_PROXIES. We deliberately
# do NOT source ops/.env: that file holds container-network values
# (host.docker.internal) that don't resolve on the host. For a standalone run,
# export the needed vars yourself (same set the worker plist defines).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Package is not installed editable — make `agf` importable from src/.
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"

# Prefer the project venv interpreter; launchd has a minimal PATH so a bare
# `python` may not resolve.
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

exec "$PY" -m agf.enrich_full.enrich_one "$@"
