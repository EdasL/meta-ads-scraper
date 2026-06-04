#!/usr/bin/env bash
# ops/cron/prune.sh — drop crawl_log + ad_metrics older than the 365-day retention.
set -euo pipefail
: "${AGF_DATABASE_URL:?set AGF_DATABASE_URL}"
PSQL="${PSQL:-psql}"
"$PSQL" "$AGF_DATABASE_URL" -v ON_ERROR_STOP=1 \
  -c "DELETE FROM logging.crawl_log WHERE crawled_at < now() - interval '365 days';" \
  -c "DELETE FROM adtech.ad_metrics WHERE snapshot_date < (now() - interval '365 days')::date;"
