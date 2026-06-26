#!/usr/bin/env bash
# VM startup: install docker, render /opt/agf/.env from Secret Manager, fetch the
# compose payload from the repo tarball baked into the image, run compose.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
PROJECT=peon-489013
REGION=europe-west1
IMAGE="$REGION-docker.pkg.dev/$PROJECT/agf/worker:latest"
CONN="peon-489013:europe-west1:agf-db"

if ! command -v docker >/dev/null 2>&1; then
  apt-get update && apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release; echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
  apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

secret() { gcloud secrets versions access latest --secret="$1" --project="$PROJECT"; }
mkdir -p /opt/agf
DB_PASSWORD="$(secret agf-db-password)"
MINIO_PASSWORD="$(secret agf-minio-password)"
cat > /opt/agf/.env <<EOF
CLOUD_SQL_CONNECTION_NAME=$CONN
AGF_DATABASE_URL=postgresql://agf:${DB_PASSWORD}@cloud-sql-proxy:5432/agf
AGF_PROXIES=$(secret agf-proxies)
GEMINI_API_KEY=$(secret agf-gemini-api-key)
S3_ENDPOINT=http://minio:9000
S3_BUCKET=agf
MINIO_ROOT_USER=agfadmin
MINIO_ROOT_PASSWORD=${MINIO_PASSWORD}
EOF
chmod 600 /opt/agf/.env

# Authenticate to Artifact Registry, pull the image, then extract the compose file
# (which ships inside the image at /app/ops/prod/docker-compose.yml) to the host so
# `docker compose` can read it. Order matters: auth BEFORE any pull. No `|| true` —
# a failure here must abort the boot (set -e) rather than silently start nothing.
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
docker pull "$IMAGE"
cid="$(docker create "$IMAGE")"
docker cp "$cid:/app/ops/prod/docker-compose.yml" /opt/agf/docker-compose.yml
docker rm "$cid"

docker compose -f /opt/agf/docker-compose.yml --env-file /opt/agf/.env up -d
