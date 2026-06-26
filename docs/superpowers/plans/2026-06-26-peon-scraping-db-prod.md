# Peon Scraping + DB Prod Deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Field Desk scraping pipeline + its Postgres database 24/7 in GCP project `peon-489013`, with the local data migrated in and the Mac-hosted API/web repointed at the prod DB.

**Architecture:** A single Compute Engine VM (`e2-medium`, europe-west1) runs the existing pipeline via `docker compose` — `worker` + `minio` + `cron` + a `cloud-sql-proxy` sidecar. The worker image is built by Cloud Build (amd64) and stored in Artifact Registry; the VM pulls it. State lives in a new dedicated Cloud SQL Postgres 17 instance (managed backups/PITR). Secrets come from Secret Manager. The deploy is additive — the existing `granini` app in peon is left untouched.

**Tech Stack:** GCP (Compute Engine, Cloud SQL for Postgres, Artifact Registry, Cloud Build, Secret Manager, Cloud SQL Auth Proxy), Docker Compose, Python pipeline (`agf.*`), MinIO, gcloud CLI.

## Global Constraints

- Project: `peon-489013`. Region: `europe-west1`. Zone: `europe-west1-b`.
- Leave `granini` resources untouched: Cloud Run `granini-app`, GKE `peon-cluster`, Cloud SQL `peon-db`, bucket `granini-gallery-peon-489013`. **Do not** create/modify/delete anything named `granini*`/`peon-cluster`/`peon-db`.
- DB schema is Python-owned; the Go API only reads. No DB extensions are needed (embeddings are `REAL[]`, not pgvector).
- Meta egress goes through DataImpulse residential proxy via in-process `AGF_PROXIES` — there is no gateway container.
- Secrets (`AGF_PROXIES`, `GEMINI_API_KEY`, DB password) NEVER get committed to git or pasted into plan/spec/RUNBOOK files. They live only in Secret Manager and the current launchd plist (`~/Library/LaunchAgents/com.fielddesk.worker.plist`) / `ops/.env`.
- All provisioning scripts are idempotent: guard every create with a `gcloud ... describe` existence check.
- Work on `main` (repo is solo, local-only). Stage explicit paths only. Commit after each task.
- Product copy rules (CLAUDE.md) do not apply here — no user-facing UI strings change.

---

### Task 1: Prod compose + VM payload files

Author the prod-only compose and its supporting payload under `ops/prod/`. Derived from `ops/docker-compose.yml`: drop `postgres` (Cloud SQL replaces it), drop `api`/`api-py` (API stays on the Mac), add a `cloud-sql-proxy` sidecar, keep `minio`/`minio-init`/`worker`/`cron`. The `worker`/`cron` reference a prebuilt Artifact Registry image (built in Task 2) instead of `build:`.

**Files:**
- Create: `ops/prod/docker-compose.yml`
- Create: `ops/prod/crontab`
- Create: `ops/prod/.env.example`
- Create: `ops/prod/.gcloudignore`
- Create: `ops/prod/README.md` (one-paragraph pointer to the plan + RUNBOOK)

**Interfaces:**
- Produces: image ref `europe-west1-docker.pkg.dev/peon-489013/agf/worker:latest` (consumed/created by Task 2); compose service `cloud-sql-proxy` listening on `cloud-sql-proxy:5432`; `AGF_DATABASE_URL` form `postgresql://agf:${DB_PASSWORD}@cloud-sql-proxy:5432/agf`.

- [ ] **Step 1: Write `ops/prod/docker-compose.yml`**

```yaml
# Field Desk — PROD on peon-489013 (europe-west1). Worker/minio/cron only;
# Postgres is Cloud SQL (reached via the cloud-sql-proxy sidecar), API/web stay
# on the Mac. Image is built by Cloud Build → Artifact Registry (ops/prod, Task 2).
name: field-desk-prod

services:
  cloud-sql-proxy:
    image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.14.1
    restart: unless-stopped
    command:
      - "--address=0.0.0.0"
      - "--port=5432"
      - "${CLOUD_SQL_CONNECTION_NAME}"
    # On the VM the proxy uses the instance service account (ADC) — no key file.

  minio:
    image: minio/minio:RELEASE.2025-04-08T15-41-24Z
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 10s
      timeout: 5s
      retries: 5

  minio-init:
    image: minio/mc:RELEASE.2025-04-08T15-39-49Z
    depends_on:
      minio:
        condition: service_started
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 $${MINIO_ROOT_USER} $${MINIO_ROOT_PASSWORD} &&
      mc mb --ignore-existing local/$${S3_BUCKET} &&
      echo 'bucket ready';"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
      S3_BUCKET: ${S3_BUCKET}

  worker:
    image: europe-west1-docker.pkg.dev/peon-489013/agf/worker:latest
    restart: unless-stopped
    command: python -m agf.workers.main
    environment:
      AGF_DATABASE_URL: ${AGF_DATABASE_URL}
      AGF_PROXIES: ${AGF_PROXIES}
      GEMINI_API_KEY: ${GEMINI_API_KEY}
      S3_ENDPOINT: ${S3_ENDPOINT}
      MINIO_ACCESS_KEY: ${MINIO_ROOT_USER}
      MINIO_SECRET_KEY: ${MINIO_ROOT_PASSWORD}
      S3_BUCKET: ${S3_BUCKET}
      AGF_CONTINUOUS_DISCOVERY: "1"
      AGF_SWEEP_PAGES: ${AGF_SWEEP_PAGES:-4}
      AGF_KW_DELAY: ${AGF_KW_DELAY:-3.0}
      AGF_DISCOVERY_ENRICH_BUDGET: ${AGF_DISCOVERY_ENRICH_BUDGET:-600}
      AGF_SCORE_AFTER_SWEEP: ${AGF_SCORE_AFTER_SWEEP:-1}
      AGF_SWEEP_INTERVAL_SEC: ${AGF_SWEEP_INTERVAL_SEC:-60}
      AGF_FLYWHEEL_EVERY_SEC: ${AGF_FLYWHEEL_EVERY_SEC:-7200}
      AGF_RELEASE_MINT: ${AGF_RELEASE_MINT:-1}
      AGF_RELEASE_MINT_MAX_LISTINGS: ${AGF_RELEASE_MINT_MAX_LISTINGS:-40}
    healthcheck:
      test: ["CMD", "python", "-m", "agf.workers.main", "--healthcheck"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s
    depends_on:
      cloud-sql-proxy:
        condition: service_started
      minio-init:
        condition: service_completed_successfully

  cron:
    image: europe-west1-docker.pkg.dev/peon-489013/agf/worker:latest
    restart: unless-stopped
    entrypoint: >
      /bin/sh -c "env >> /etc/environment && crontab /app/ops/prod/crontab && cron -f"
    environment:
      AGF_DATABASE_URL: ${AGF_DATABASE_URL}
    depends_on:
      cloud-sql-proxy:
        condition: service_started

volumes:
  minio_data:
```

- [ ] **Step 2: Write `ops/prod/crontab`**

Replicates the local cron jobs MINUS `backup.sh` (Cloud SQL has managed backups) PLUS a `*/30` scoring run (replicating the `com.fielddesk.scoring` launchd job). Note the absolute `psql`/venv paths inside the image (`/app`, `PYTHONPATH=/app/src`).

```cron
# ops/prod/crontab — Field Desk PROD scheduled jobs (UTC). Installed into the
# `cron` container. AGF_DATABASE_URL is exported into /etc/environment at launch.
*/30 * * * *  cd /app && PYTHONPATH=/app/src python -m agf.scoring.run_scoring >> /var/log/agf/scoring.log 2>&1
0    * * * *  psql "$AGF_DATABASE_URL" -v ON_ERROR_STOP=1 -c "REFRESH MATERIALIZED VIEW CONCURRENTLY frontend.emerging_scaling_apps;" >> /var/log/agf/refresh.log 2>&1
30   3 * * *  /app/ops/cron/prune.sh >> /var/log/agf/prune.log 2>&1
```

- [ ] **Step 3: Write `ops/prod/.env.example`**

```bash
# ops/prod/.env — rendered on the VM at boot from Secret Manager (see ops/prod/startup.sh).
# NEVER commit a filled-in copy. Placeholders only here.
CLOUD_SQL_CONNECTION_NAME=peon-489013:europe-west1:agf-db
AGF_DATABASE_URL=postgresql://agf:CHANGEME@cloud-sql-proxy:5432/agf
AGF_PROXIES=http://USER:PASS@gw.dataimpulse.com:823
GEMINI_API_KEY=CHANGEME
S3_ENDPOINT=http://minio:9000
S3_BUCKET=agf
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=CHANGEME
```

- [ ] **Step 4: Write `ops/prod/.gcloudignore`**

Keeps the Cloud Build upload context small (the repo root holds large screenshots, `.venv`, `.next`, `.playwright-mcp`). This file is used in Task 2's `gcloud builds submit`.

```gitignore
.git
.venv
.next
.playwright-mcp
.firecrawl
node-compile-cache
nanobanana-output
design-review
*.jpeg
*.png
*.pyc
__pycache__
.pytest_cache
tests
docs
web
```

- [ ] **Step 5: Write `ops/prod/README.md`**

```markdown
# ops/prod — Field Desk production (peon-489013)

Provisioning scripts + the VM compose payload for the scraping + DB deploy.
See `docs/superpowers/plans/2026-06-26-peon-scraping-db-prod.md` for the full
runbook and `ops/RUNBOOK.md` for day-2 ops. Scripts are idempotent; run them in
numeric order: `00_provision_sql.sh`, `02_secrets.sh`, `01_provision_vm.sh`.
```

- [ ] **Step 6: Validate the compose parses**

Run: `docker compose -f ops/prod/docker-compose.yml --env-file ops/prod/.env.example config >/dev/null && echo OK`
Expected: `OK` (no YAML/interpolation errors).

- [ ] **Step 7: Commit**

```bash
git add ops/prod/docker-compose.yml ops/prod/crontab ops/prod/.env.example ops/prod/.gcloudignore ops/prod/README.md
git commit -m "feat(prod): ops/prod compose + VM payload for peon scraping deploy"
```

---

### Task 2: Build + push the worker image (Cloud Build → Artifact Registry)

The VM is amd64 and the repo has no git remote, so build remotely with Cloud Build (native amd64) from the repo root using the existing `ops/Dockerfile`, and store the image in a new Artifact Registry repo.

**Files:**
- Create: `ops/prod/02_build_image.sh`

**Interfaces:**
- Consumes: `ops/Dockerfile`, `ops/prod/.gcloudignore` (Task 1).
- Produces: image `europe-west1-docker.pkg.dev/peon-489013/agf/worker:latest` (consumed by the VM in Task 5).

- [ ] **Step 1: Write `ops/prod/02_build_image.sh`**

```bash
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
GCLOUD_IGNORE=.gcloudignore.agfprod gcloud builds submit \
  --project="$PROJECT" --region="$REGION" \
  --tag="$IMAGE" --gcs-log-dir="gs://${PROJECT}_cloudbuild/logs" \
  --ignore-file=.gcloudignore.agfprod \
  --substitutions=_DOCKERFILE=ops/Dockerfile . \
  || { rm -f .gcloudignore.agfprod; exit 1; }
rm -f .gcloudignore.agfprod
echo "pushed $IMAGE"
```

Note: `gcloud builds submit --tag` uses a root `Dockerfile` by default. Because the Dockerfile lives at `ops/Dockerfile`, either (a) pass a `cloudbuild.yaml` that sets `-f ops/Dockerfile`, or (b) keep it simple — symlink/copy is fragile, so prefer the cloudbuild.yaml form. Replace the `gcloud builds submit` line with a config build:

```bash
gcloud builds submit --project="$PROJECT" --region="$REGION" \
  --ignore-file=.gcloudignore.agfprod \
  --config=ops/prod/cloudbuild.yaml --substitutions=_IMAGE="$IMAGE" .
```

- [ ] **Step 2: Write `ops/prod/cloudbuild.yaml`**

```yaml
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "ops/Dockerfile", "-t", "${_IMAGE}", "."]
images: ["${_IMAGE}"]
options:
  machineType: E2_HIGHCPU_8
```

(Add `ops/prod/cloudbuild.yaml` to the Task-2 commit and the file list.)

- [ ] **Step 3: Run the build**

Run: `bash ops/prod/02_build_image.sh`
Expected: ends with `pushed europe-west1-docker.pkg.dev/peon-489013/agf/worker:latest`; Cloud Build status `SUCCESS`.

- [ ] **Step 4: Verify the image exists**

Run: `gcloud artifacts docker images list europe-west1-docker.pkg.dev/peon-489013/agf/worker --project=peon-489013`
Expected: one row for the `worker` image with a recent timestamp.

- [ ] **Step 5: Commit**

```bash
git add ops/prod/02_build_image.sh ops/prod/cloudbuild.yaml
git commit -m "feat(prod): Cloud Build → Artifact Registry worker image build"
```

---

### Task 3: Provision the Cloud SQL instance

Create a new dedicated Postgres 17 instance, the `agf` database, and the `agf` app user. Sized `db-custom-1-3840`; managed backups + PITR on.

**Files:**
- Create: `ops/prod/00_provision_sql.sh`

**Interfaces:**
- Produces: instance connection name `peon-489013:europe-west1:agf-db`; database `agf`; user `agf` (password from Task 4's secret, but the user is created here with a placeholder then aligned — see Step 1).

- [ ] **Step 1: Write `ops/prod/00_provision_sql.sh`**

```bash
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
```

- [ ] **Step 2: (Ordering note) Run Task 4 first**

This script consumes the `agf-db-password` and `agf-postgres-password` secrets. Execute `ops/prod/02_secrets.sh` (Task 4) BEFORE running this script. The plan lists SQL as Task 3 for narrative flow, but at execution time the order is: Task 4 (secrets) → Task 3 (SQL) → Task 5 (VM).

- [ ] **Step 3: Run it**

Run: `bash ops/prod/00_provision_sql.sh`
Expected: prints `connection name: peon-489013:europe-west1:agf-db`; instance STATUS becomes `RUNNABLE`.

- [ ] **Step 4: Verify**

Run: `gcloud sql instances describe agf-db --project=peon-489013 --format='value(state,databaseVersion,settings.tier)'`
Expected: `RUNNABLE  POSTGRES_17  db-custom-1-3840`.

- [ ] **Step 5: Commit**

```bash
git add ops/prod/00_provision_sql.sh
git commit -m "feat(prod): Cloud SQL Postgres 17 provisioning script"
```

---

### Task 4: Secret Manager entries

Create the secrets the SQL and VM steps consume. Passwords are generated; `AGF_PROXIES` and `GEMINI_API_KEY` are pulled from the live worker plist so prod matches local exactly.

**Files:**
- Create: `ops/prod/02_secrets.sh`

**Interfaces:**
- Produces secrets: `agf-db-password`, `agf-postgres-password`, `agf-proxies`, `agf-gemini-api-key`, `agf-minio-password` (consumed by Tasks 3 and 5).

- [ ] **Step 1: Write `ops/prod/02_secrets.sh`**

```bash
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
```

- [ ] **Step 2: Run it**

Run: `bash ops/prod/02_secrets.sh`
Expected: five `created` / `exists` lines, no errors.

- [ ] **Step 3: Verify (existence only — never print values)**

Run: `gcloud secrets list --project=peon-489013 --filter="name~agf-" --format='value(name)'`
Expected: lists `agf-db-password agf-postgres-password agf-minio-password agf-proxies agf-gemini-api-key`.

- [ ] **Step 4: Commit**

```bash
git add ops/prod/02_secrets.sh
git commit -m "feat(prod): Secret Manager provisioning for prod secrets"
```

---

### Task 5: Provision the VM + startup script

Create the worker VM, its service account (Cloud SQL client + secret accessor), and a startup script that renders `/opt/agf/.env` from secrets, logs into Artifact Registry, and runs `docker compose up -d`.

**Files:**
- Create: `ops/prod/01_provision_vm.sh`
- Create: `ops/prod/startup.sh`

**Interfaces:**
- Consumes: secrets (Task 4), Cloud SQL instance (Task 3), image (Task 2), compose payload (Task 1).
- Produces: running VM `agf-worker` in `europe-west1-b` with the pipeline up.

- [ ] **Step 1: Write `ops/prod/startup.sh`**

```bash
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

# The compose file ships INSIDE the image at /app/ops/prod/docker-compose.yml.
docker run --rm -v /opt/agf:/out "$IMAGE" \
  cp /app/ops/prod/docker-compose.yml /app/ops/prod/crontab /out/ 2>/dev/null || true
# Fallback: pull the compose via gsutil if not bundled (kept in the image build).
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
docker compose -f /opt/agf/docker-compose.yml --env-file /opt/agf/.env pull
docker compose -f /opt/agf/docker-compose.yml --env-file /opt/agf/.env up -d
```

- [ ] **Step 2: Write `ops/prod/01_provision_vm.sh`**

```bash
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
```

Egress decision (settled 2026-06-26): the VM gets an **ephemeral external IP** (no `--no-address`), so outbound to DataImpulse / Meta / Gemini / Artifact Registry works with zero VPC setup. DataImpulse still proxies Meta, so the VM's own IP is irrelevant to scraping. SSH uses the default `default-allow-ssh` rule on the `default` network — if SSH times out, add a firewall rule allowing `tcp:22` (e.g. `gcloud compute firewall-rules create allow-ssh --network=default --allow=tcp:22 --project=peon-489013`).

- [ ] **Step 3: Run secrets + sql + build first, then the VM**

Run (in order): `bash ops/prod/02_secrets.sh && bash ops/prod/00_provision_sql.sh && bash ops/prod/02_build_image.sh && bash ops/prod/01_provision_vm.sh`
Expected: each prints its success line; final prints the SSH command.

- [ ] **Step 4: Verify the VM is up and containers are starting**

Run: `gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command="sudo docker compose -f /opt/agf/docker-compose.yml ps"`
Expected: `cloud-sql-proxy`, `minio`, `worker`, `cron` present. (worker may be restarting until Task 6's data restore — that's expected; the schema must exist first.)

- [ ] **Step 5: Commit**

```bash
git add ops/prod/01_provision_vm.sh ops/prod/startup.sh
git commit -m "feat(prod): worker VM + startup script provisioning"
```

---

### Task 6: Migrate local data → Cloud SQL

Freeze the local pipeline, dump the local DB, restore into Cloud SQL through a local Auth Proxy, and verify row-count parity.

**Files:** (operational — no repo files; commands only)

**Interfaces:**
- Consumes: local DB `postgresql://agf:agf@localhost:5433/agf`; Cloud SQL `agf-db` (Task 3); secrets (Task 4).

- [ ] **Step 1: Freeze the local pipeline (stop writes)**

Run:
```bash
launchctl bootout gui/$(id -u)/com.fielddesk.worker 2>/dev/null || true
launchctl bootout gui/$(id -u)/com.fielddesk.scoring 2>/dev/null || true
```
Expected: no error; the local worker/scoring agents are stopped.

- [ ] **Step 2: Dump the local DB**

Run:
```bash
pg_dump "postgresql://agf:agf@localhost:5433/agf" --no-owner --no-acl -Fc \
  -f /private/tmp/claude-501/-Users-edaslakavicius-Projects-emerging-app-finder/59dd38cf-057f-4e29-9dcc-54916497706c/scratchpad/agf-local.dump
ls -lh /private/tmp/claude-501/-Users-edaslakavicius-Projects-emerging-app-finder/59dd38cf-057f-4e29-9dcc-54916497706c/scratchpad/agf-local.dump
```
Expected: a non-empty `.dump` file.

- [ ] **Step 3: Start a local Cloud SQL Auth Proxy on 5433**

Run (in a background shell; install the proxy binary if missing via `gcloud components install cloud-sql-proxy` or the standalone download):
```bash
cloud-sql-proxy peon-489013:europe-west1:agf-db --port 5433 &
```
Expected: `Listening on 127.0.0.1:5433`. (The local docker Postgres is already stopped via Task-6 Step 1's freeze and the compose being down; if port 5433 is taken, stop the local `field-desk` postgres container first.)

- [ ] **Step 4: Restore into Cloud SQL as the postgres user**

Run (password = `agf-postgres-password` secret):
```bash
PGPASSWORD="$(gcloud secrets versions access latest --secret=agf-postgres-password --project=peon-489013)" \
pg_restore --no-owner --no-acl --role=agf -d "postgresql://postgres@127.0.0.1:5433/agf" \
  -j 4 /private/tmp/.../scratchpad/agf-local.dump
```
Then grant the app user:
```bash
PGPASSWORD="$(gcloud secrets versions access latest --secret=agf-postgres-password --project=peon-489013)" \
psql "postgresql://postgres@127.0.0.1:5433/agf" -v ON_ERROR_STOP=1 -c \
 "GRANT ALL ON ALL TABLES IN SCHEMA public, adtech, frontend, logging, scoring TO agf; \
  GRANT USAGE ON ALL SEQUENCES IN SCHEMA public, adtech, frontend, logging, scoring TO agf; \
  GRANT ALL ON SCHEMA public, adtech, frontend, logging, scoring TO agf;"
```
Expected: restore completes (a few benign "already exists" notices on extensions/public schema are OK with `--no-owner`).

- [ ] **Step 5: Verify row-count parity on key tables**

Run, comparing local vs prod:
```bash
for DB in "postgresql://agf:agf@localhost:5433/agf" "postgresql://postgres:$(gcloud secrets versions access latest --secret=agf-postgres-password --project=peon-489013)@127.0.0.1:5433/agf"; do
  echo "== $DB =="
  psql "$DB" -tA -c "SELECT 'ads', count(*) FROM public.ads UNION ALL SELECT 'app_ratings', count(*) FROM frontend.app_ratings UNION ALL SELECT 'feed', count(*) FROM frontend.emerging_scaling_apps UNION ALL SELECT 'candidates', count(*) FROM discovery.candidates;"
done
```

Wait — local 5433 is now the proxy (prod), not local docker. To compare, run the local count BEFORE Step 3 (capture it), or bring the local docker Postgres up on a different port. **At execution:** capture local counts during Step 2 (before starting the proxy) and compare against prod here.
Expected: prod counts equal the captured local counts for each table.

- [ ] **Step 6: Commit (a note, no code)**

```bash
git commit --allow-empty -m "chore(prod): local DB migrated to Cloud SQL agf-db (row-count parity verified)"
```

---

### Task 7: Live gate — one full sweep in prod

Restart the worker now that the schema/data exist; confirm a heartbeat, one sweep that writes ≥1 advertiser, a scoring run, and a matview refresh, all against real Cloud SQL + MinIO + DataImpulse.

**Files:** (operational)

- [ ] **Step 1: Restart the worker on the VM**

Run:
```bash
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo docker compose -f /opt/agf/docker-compose.yml up -d && sleep 5 && sudo docker compose -f /opt/agf/docker-compose.yml ps"
```
Expected: all four services `Up` (worker no longer restart-looping).

- [ ] **Step 2: Tail worker logs for a heartbeat + sweep**

Run:
```bash
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo docker compose -f /opt/agf/docker-compose.yml logs --tail=200 worker"
```
Expected: a sweep cycle log line and a heartbeat; no proxy/auth errors. (DataImpulse + doc_id self-heal per RUNBOOK if needed.)

- [ ] **Step 3: Confirm new writes landed in Cloud SQL**

Run (through the local proxy from Step 6, or re-open it):
```bash
psql "postgresql://postgres:$(gcloud secrets versions access latest --secret=agf-postgres-password --project=peon-489013)@127.0.0.1:5433/agf" -tA -c \
 "SELECT max(first_seen) FROM public.advertisers; SELECT count(*) FROM frontend.emerging_scaling_apps;"
```
Expected: `max(first_seen)` is within minutes of now (new advertiser written); feed count ≥ migrated baseline.

- [ ] **Step 4: Confirm scoring + matview ran**

Run:
```bash
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo docker compose -f /opt/agf/docker-compose.yml exec -T cron cat /var/log/agf/scoring.log /var/log/agf/refresh.log 2>/dev/null | tail -20"
```
Expected: a `scored N advertisers` line and a successful matview refresh (or wait for the next `*/30` / hourly tick).

- [ ] **Step 5: Commit (note)**

```bash
git commit --allow-empty -m "chore(prod): live gate passed — sweep+score+matview verified on VM"
```

---

### Task 8: Repoint the Mac API/web at Cloud SQL + RUNBOOK

Point the local Go API + Next web at prod via a local Auth Proxy, and document the deploy + day-2 ops.

**Files:**
- Create: `ops/prod/com.fielddesk.sqlproxy.plist` (launchd agent running the local proxy on 5433)
- Modify: `~/Library/LaunchAgents/com.fielddesk.api.plist`, `com.fielddesk.web.plist` (DB URL creds — edited in place, not committed: they contain the prod password)
- Modify: `ops/RUNBOOK.md` (add a "Production on peon" section)

- [ ] **Step 1: Write `ops/prod/com.fielddesk.sqlproxy.plist`**

A launchd agent that keeps `cloud-sql-proxy peon-489013:europe-west1:agf-db --port 5433` running, so the Mac's API/web reach prod at `127.0.0.1:5433` exactly like the old local docker Postgres. (Template — no secrets; ADC via `gcloud auth application-default login`.)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.fielddesk.sqlproxy</string>
  <key>ProgramArguments</key><array>
    <string>/opt/homebrew/bin/cloud-sql-proxy</string>
    <string>peon-489013:europe-west1:agf-db</string>
    <string>--port</string><string>5433</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/Users/edaslakavicius/Library/Logs/fielddesk-sqlproxy.log</string>
  <key>StandardErrorPath</key><string>/Users/edaslakavicius/Library/Logs/fielddesk-sqlproxy.log</string>
</dict></plist>
```

- [ ] **Step 2: Stop the local docker Postgres, install the proxy agent**

Run:
```bash
docker compose -f ops/docker-compose.yml --env-file ops/.env stop postgres 2>/dev/null || true
gcloud auth application-default login   # one-time, for the local proxy's ADC
cp ops/prod/com.fielddesk.sqlproxy.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fielddesk.sqlproxy.plist
```
Expected: `fielddesk-sqlproxy.log` shows `Listening on 127.0.0.1:5433`.

- [ ] **Step 3: Repoint the API + web DB URL and restart them**

Edit the `AGF_DATABASE_URL` in `~/Library/LaunchAgents/com.fielddesk.api.plist` and `com.fielddesk.web.plist` to `postgresql://agf:<agf-db-password>@localhost:5433/agf` (fetch the password: `gcloud secrets versions access latest --secret=agf-db-password --project=peon-489013`). Then:
```bash
launchctl bootout gui/$(id -u)/com.fielddesk.api; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fielddesk.api.plist
launchctl bootout gui/$(id -u)/com.fielddesk.web; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fielddesk.web.plist
```
Expected: services restart cleanly.

- [ ] **Step 4: Verify the dashboard loads from prod**

Run: `curl -s localhost:8000/api/shortlist?limit=1 | head -c 300; echo`
Expected: a JSON row from Cloud SQL (not the local DB — confirm by a count that matches prod). Load the web dashboard and confirm the feed renders.

- [ ] **Step 5: Add the RUNBOOK section**

Append a "## Production on peon-489013" section to `ops/RUNBOOK.md` covering: the architecture (VM + Cloud SQL + Artifact Registry), how to SSH (`gcloud compute ssh agf-worker --zone=europe-west1-b`), redeploy a new image (`bash ops/prod/02_build_image.sh` then `docker compose pull && up -d` on the VM), how the Mac reaches prod (the sqlproxy agent), rollback (re-load the local launchd worker + restart the local docker Postgres; the local DB snapshot is the pre-cutover state), and that `granini` is intentionally left running.

- [ ] **Step 6: Commit**

```bash
git add ops/prod/com.fielddesk.sqlproxy.plist ops/RUNBOOK.md
git commit -m "feat(prod): Mac sqlproxy agent + RUNBOOK production section"
```

---

## Post-deploy notes
- Keep the local docker Postgres data volume as a rollback snapshot for ~1 week, then remove.
- The Mac API/web now depend on the `com.fielddesk.sqlproxy` agent being up.
- Outbound from the VM uses an ephemeral external IP (settled); confirm DataImpulse/Meta/Gemini/Artifact Registry traffic flows in the Task 7 live gate before declaring done.
