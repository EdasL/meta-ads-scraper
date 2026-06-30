#!/usr/bin/env bash
# Provision the GKE web+API deploy infra (idempotent). Run once.
set -euo pipefail
PROJECT=peon-489013
REGION=europe-west1
SA=agf-api@${PROJECT}.iam.gserviceaccount.com
BUCKET=agf-media-peon-489013
ZONE=peon-zone

gcloud config set project "$PROJECT"

# 1. Global static IP for the Ingress LB
gcloud compute addresses describe agf-ip --global >/dev/null 2>&1 \
  || gcloud compute addresses create agf-ip --global
IP=$(gcloud compute addresses describe agf-ip --global --format='value(address)')
echo "agf-ip = $IP"

# 2. Service account for the API (Cloud SQL proxy + GCS reads via Workload Identity)
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create agf-api --display-name="Field Desk API (Cloud SQL + media)"

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${SA}" --role="roles/cloudsql.client" --condition=None

# bucket-scoped object read
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}" --role="roles/storage.objectViewer"

# Workload Identity binding: KSA agf/agf-api -> this GSA
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:${PROJECT}.svc.id.goog[agf/agf-api]"

# 3. DNS A-records -> agf-ip (apex/granini untouched)
for host in api app; do
  fqdn="${host}.peon.works."
  if ! gcloud dns record-sets describe "$fqdn" --zone="$ZONE" --type=A >/dev/null 2>&1; then
    gcloud dns record-sets create "$fqdn" --zone="$ZONE" --type=A --ttl=300 --rrdatas="$IP"
  else
    echo "record $fqdn exists — leaving as-is"
  fi
done

echo "Provisioning complete. Static IP: $IP"
