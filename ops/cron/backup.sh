#!/usr/bin/env bash
# ops/cron/backup.sh — nightly pg_dump to $BACKUP_DIR (timestamped, gzipped).
set -euo pipefail
: "${AGF_DATABASE_URL:?set AGF_DATABASE_URL}"
PG_DUMP="${PG_DUMP:-pg_dump}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/agf}"
mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
"$PG_DUMP" "$AGF_DATABASE_URL" | gzip > "$BACKUP_DIR/agf-$TS.sql.gz"
echo "backup written: $BACKUP_DIR/agf-$TS.sql.gz"
