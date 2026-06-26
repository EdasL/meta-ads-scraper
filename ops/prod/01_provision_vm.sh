#!/usr/bin/env bash
# Create the worker VM + service account + IAM. Idempotent.
set -euo pipefail
PROJECT=peon-489013
REGION=europe-west1
ZONE=europe-west1-b
VM=agf-worker
SA="agf-worker-sa"
SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"

gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA" --project="$PROJECT" --display-name="Field Desk worker VM"

for ROLE in roles/cloudsql.client roles/secretmanager.secretAccessor roles/artifactregistry.reader; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA_EMAIL" --role="$ROLE" --condition=None >/dev/null
done

gcloud compute instances describe "$VM" --zone="$ZONE" --project="$PROJECT" >/dev/null 2>&1 \
  || gcloud compute instances create "$VM" --project="$PROJECT" --zone="$ZONE" \
       --machine-type=e2-medium --image-family=debian-12 --image-project=debian-cloud \
       --boot-disk-size=30GB \
       --service-account="$SA_EMAIL" \
       --scopes=https://www.googleapis.com/auth/cloud-platform \
       --metadata-from-file=startup-script=ops/prod/startup.sh

echo "VM created. SSH via: gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT"
