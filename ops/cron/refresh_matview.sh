#!/usr/bin/env bash
# ops/cron/refresh_matview.sh — REFRESH the product matview CONCURRENTLY (needs the
# Plan 5 UNIQUE index; cannot run in a txn block, so psql -c is one autocommit stmt).
set -euo pipefail
: "${AGF_DATABASE_URL:?set AGF_DATABASE_URL}"
PSQL="${PSQL:-psql}"
"$PSQL" "$AGF_DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c "REFRESH MATERIALIZED VIEW CONCURRENTLY frontend.emerging_scaling_apps;"
