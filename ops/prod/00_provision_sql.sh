#!/usr/bin/env bash
# Create the dedicated Cloud SQL Postgres 17 instance for Field Desk. Idempotent.
# Reads the app-user password from Secret Manager (run 02_secrets.sh FIRST so the
# `agf-db-password` secret exists).
set -euo pipefail
PROJECT=peon-489013
REGION=europe-west1
INSTANCE=agf-db

gcloud services enable sqladmin.googleapis.com --project="$PROJECT"
DB_PASSWORD="$(gcloud secrets versions access latest --secret=agf-db-password --project="$PROJECT")"
POSTGRES_PASSWORD="$(gcloud secrets versions access latest --secret=agf-postgres-password --project="$PROJECT")"

gcloud sql instances describe "$INSTANCE" --project="$PROJECT" >/dev/null 2>&1 \
  || gcloud sql instances create "$INSTANCE" --project="$PROJECT" \
       --database-version=POSTGRES_17 --tier=db-custom-1-3840 --region="$REGION" \
       --storage-size=20 --storage-auto-increase \
       --backup --enable-point-in-time-recovery \
       --root-password="$POSTGRES_PASSWORD"

gcloud sql databases describe agf --instance="$INSTANCE" --project="$PROJECT" >/dev/null 2>&1 \
  || gcloud sql databases create agf --instance="$INSTANCE" --project="$PROJECT"

gcloud sql users describe agf --instance="$INSTANCE" --project="$PROJECT" >/dev/null 2>&1 \
  || gcloud sql users create agf --instance="$INSTANCE" --project="$PROJECT" --password="$DB_PASSWORD"

echo "connection name: $(gcloud sql instances describe "$INSTANCE" --project="$PROJECT" --format='value(connectionName)')"
