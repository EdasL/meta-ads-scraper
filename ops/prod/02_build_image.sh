#!/usr/bin/env bash
# Build the worker/cron image (amd64) via Cloud Build → Artifact Registry.
# Run from the repo root. Idempotent (repo create is guarded).
set -euo pipefail
PROJECT=peon-489013
REGION=europe-west1
REPO=agf
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/worker:latest"

gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com --project="$PROJECT"

gcloud artifacts repositories describe "$REPO" --location="$REGION" --project="$PROJECT" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" --repository-format=docker \
       --location="$REGION" --project="$PROJECT" --description="Field Desk worker images"

# ops/Dockerfile expects the repo root as build context. Copy it to the upload root
# under a Cloud Build config so .gcloudignore (ops/prod/.gcloudignore) trims the upload.
cp ops/prod/.gcloudignore .gcloudignore.agfprod
gcloud builds submit --project="$PROJECT" --region="$REGION" \
  --ignore-file=.gcloudignore.agfprod \
  --config=ops/prod/cloudbuild.yaml --substitutions=_IMAGE="$IMAGE" . \
  || { rm -f .gcloudignore.agfprod; exit 1; }
rm -f .gcloudignore.agfprod
echo "pushed $IMAGE"
