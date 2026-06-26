#!/usr/bin/env bash
# Create Secret Manager entries for the Field Desk prod deploy. Idempotent:
# existing secrets are left as-is (only created if missing). Pulls AGF_PROXIES and
# GEMINI_API_KEY from the live launchd worker plist so prod == local.
set -euo pipefail
PROJECT=peon-489013
PLIST="$HOME/Library/LaunchAgents/com.fielddesk.worker.plist"

gcloud services enable secretmanager.googleapis.com --project="$PROJECT"

plist_env() { /usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:$1" "$PLIST"; }

ensure_secret() {  # name value
  local name="$1" value="$2"
  if ! gcloud secrets describe "$name" --project="$PROJECT" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets create "$name" --project="$PROJECT" --data-file=- --replication-policy=automatic
    echo "created $name"
  else
    echo "exists  $name (left unchanged)"
  fi
}

ensure_secret agf-db-password       "$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
ensure_secret agf-postgres-password "$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
ensure_secret agf-minio-password    "$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
ensure_secret agf-proxies           "$(plist_env AGF_PROXIES)"
ensure_secret agf-gemini-api-key    "$(plist_env GEMINI_API_KEY)"
