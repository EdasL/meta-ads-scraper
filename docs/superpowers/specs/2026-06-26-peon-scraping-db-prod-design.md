# Field Desk — Scraping + DB production deploy to `peon-489013`

**Date:** 2026-06-26
**Status:** Approved design, pending implementation plan
**Owner:** elakavicius

## Goal

Move the **scraping pipeline + database** off the local Mac (launchd + docker
Postgres) and into Google Cloud project **`peon-489013`**, so the corpus keeps
growing 24/7 without the Mac needing to stay awake. The Go read-only API and the
Next.js web dashboard **stay on the Mac**, repointed at the prod database.

## Scope

**In scope (moves to prod):**
- `worker` — the warm → discover → score → enrich loop (`agf.workers.main`)
- scoring — `agf.scoring.run_scoring` (every 30 min)
- cron — matview refresh (hourly), prune (daily)
- Postgres database — with the existing local data migrated in
- creative media object store (MinIO)

**Out of scope (stays on the Mac, unchanged role):**
- Go read-only API (`backend/`)
- Next.js web dashboard (`web/`)

Both repoint from the local docker Postgres to the prod Cloud SQL instance via a
local Cloud SQL Auth Proxy.

## Existing peon state (do not touch)

peon already runs an unrelated app, **granini**. Per decision 2026-06-26 these are
**left running** — the deploy is purely additive:
- Cloud Run `granini-app` (us-central1)
- GKE Autopilot `peon-cluster` (europe-west1)
- Cloud SQL `peon-db` (Postgres 16, db-f1-micro, holds the `peon` database)
- Bucket `granini-gallery-peon-489013`

No teardown work: the only other resources are Cloud Build / run-sources buckets
(granini's build artifacts). Nothing else to remove.

## Architecture

Region: **europe-west1** (matches the project's existing resources; low latency to
LT for the Mac-hosted API/web).

```
peon-489013  ·  europe-west1

 Cloud SQL Postgres 17  (NEW, dedicated — e.g. agf-db, db-custom-1-3840, public IP)
   └─ stock Postgres, no extensions needed (embeddings are REAL[], not pgvector)
   └─ restored from local pg_dump (feed + ratings + embeddings + ad history)
   └─ automated backups + PITR  (replaces ops/cron/backup.sh)

 Compute Engine VM  (NEW — agf-worker, e2-medium, Debian + docker, europe-west1-b)
   /opt/agf/docker-compose.yml:
     ├─ cloud-sql-proxy   sidecar → Cloud SQL    (services connect to proxy:5432)
     ├─ worker            agf.workers.main · AGF_PROXIES=DataImpulse (Meta egress)
     ├─ minio + minio-init   creative media (the local pg_data volume is dropped)
     └─ cron              run_scoring */30m · refresh_matview hourly · prune daily

 Secret Manager: DB password · AGF_PROXIES (DataImpulse) · GEMINI_API_KEY
   → fetched into /opt/agf/.env at VM boot (startup script)

 Mac (role unchanged): Go API + Next web
   └─ local cloud-sql-proxy → 127.0.0.1:5433 → Cloud SQL
      (only the DB URL creds change; ports/app config stay)
```

### Why this shape

- **Single VM + docker-compose (Approach A):** the worker is a 24/7 watchdog
  daemon; docker `restart: unless-stopped` maps the launchd `KeepAlive` 1:1.
  Smallest lift — the existing `ops/docker-compose.yml` is reused with the
  `postgres` service removed and a `cloud-sql-proxy` service added. Cloud Run /
  GKE were rejected: a persistent sweep loop with an in-process watchdog does not
  fit request-driven or job-with-timeout models, and GKE is overkill for one daemon.
- **Cloud SQL (managed):** automated backups + PITR replace `backup.sh`; no DB ops.
- **MinIO-on-VM (not GCS):** `storage/s3.py` is plain boto3 (s3v4, path-style) and
  works unchanged against MinIO. Media is re-fetchable, so VM-local durability is
  acceptable; GCS interop is a possible later upgrade (needs a live probe first).
- **Cloud SQL public IP + Auth Proxy:** no VPC peering to configure; both the VM
  and the Mac reach the DB through the proxy. The proxy authenticates via a service
  account (`roles/cloudsql.client`), so the DB is not exposed by raw IP allowlist.

## Components

### 1. Cloud SQL instance (new)
- Postgres 17, `db-custom-1-3840` (1 vCPU / 3.75 GB) or `db-g1-small`, europe-west1.
- Public IP enabled; no authorized networks (access is via Auth Proxy + IAM only).
- Automated backups on, PITR (WAL) on.
- One database `agf`, one app user with a Secret Manager-stored password.
- No extensions required.

### 2. Compute Engine VM (new)
- `e2-medium`, Debian 12, europe-west1-b, docker + docker-compose installed via
  startup script.
- Service account with `roles/cloudsql.client` + `roles/secretmanager.secretAccessor`.
- Startup script: install docker, pull secrets → `/opt/agf/.env`, `git`/`gcloud`
  fetch the app image or repo, `docker compose up -d`.
- Firewall: no inbound needed (worker is outbound-only); SSH via IAP.

### 3. docker-compose (prod variant)
A `ops/prod/docker-compose.yml` derived from `ops/docker-compose.yml`:
- **remove** `postgres` service (Cloud SQL replaces it).
- **add** `cloud-sql-proxy` service; worker/cron/scoring point `AGF_DATABASE_URL`
  at `cloud-sql-proxy:5432`.
- keep `minio`, `minio-init`, `worker`, `cron`.
- **drop** the `api` / `api-py` services (API stays on the Mac).
- cron crontab gains a `*/30 * * * *` `run_scoring` entry (replicating the
  `com.fielddesk.scoring` launchd job) and **drops** `backup.sh` (Cloud SQL backups).

### 4. Secrets
Secret Manager entries, injected at boot:
- `agf-db-url` — full `postgresql://…@cloud-sql-proxy:5432/agf`
- `agf-proxies` — DataImpulse `AGF_PROXIES`
- `gemini-api-key` — `GEMINI_API_KEY`

### 5. Provisioning scripts
Committed under `ops/prod/` (gcloud CLI, imperative — matches the existing
shell/launchd/RUNBOOK style; Terraform rejected as overkill for one VM + one DB):
- `00_provision_sql.sh` — create the Cloud SQL instance, db, user.
- `01_provision_vm.sh` — create the VM + service account + IAM + startup script.
- `02_secrets.sh` — create/populate Secret Manager entries.
- `docker-compose.yml`, `startup.sh`, `crontab` — the VM payload.
- RUNBOOK section documenting the cutover + day-2 ops.

## Data flow (migration / cutover)

1. **Freeze local:** `launchctl bootout` the `com.fielddesk.worker` and
   `com.fielddesk.scoring` agents (stop writes).
2. **Provision** Cloud SQL + VM + secrets (scripts above), VM not yet started or
   started with worker scaled to 0.
3. **Dump:** `pg_dump` the local DB (`localhost:5433/agf`) to a file.
4. **Restore:** load the dump into Cloud SQL through a local Auth Proxy. Verify row
   counts on key tables (feed matview, app_ratings, candidate/app embeddings,
   public.ads) match local.
5. **Start prod worker:** `docker compose up -d` on the VM; confirm a heartbeat and
   one successful sweep in the logs.
6. **Repoint the Mac API/web:** start a local `cloud-sql-proxy` and change the API/web
   `AGF_DATABASE_URL` to the Cloud SQL creds @ `127.0.0.1:5433`; restart
   `com.fielddesk.api` / `com.fielddesk.web`. Confirm the dashboard loads from prod.
7. **Decommission local data plane:** leave local docker Postgres/MinIO stopped
   (kept as a rollback snapshot for a few days, then removed).

Prod is the source of truth from step 5 onward. The Mac no longer scrapes.

## Error handling / day-2 ops

- **Worker wedge:** docker `restart: unless-stopped` + the in-process watchdog
  (existing) SIGKILL-on-stale-heartbeat. VM-level: a `--healthcheck` compose
  healthcheck as today.
- **doc_id / proxy rotation:** unchanged — the RUNBOOK §1–3 procedures apply;
  restart the `worker` container to force a token re-heal.
- **Backups:** Cloud SQL automated + PITR. A manual `pg_dump` to a GCS bucket can be
  added later if an off-Cloud-SQL copy is wanted.
- **Secrets rotation:** update the Secret Manager version, re-run the boot fetch (or
  restart the VM).
- **Cost guard:** e2-medium (~$25/mo) + db-custom-1-3840 (~$50/mo) + egress. No GKE.

## Testing / verification

- Provisioning scripts are idempotent (guard on `gcloud … describe` before create).
- **Live gate (per project rules):** the deploy is not "done" until, on the VM
  against the real Cloud SQL + MinIO + DataImpulse, one full sweep cycle completes
  and writes ≥1 new advertiser, scoring runs, and the matview refreshes — verified
  in the container logs and by querying Cloud SQL.
- Mac dashboard loads the feed from Cloud SQL (not local) post-cutover.
- Row-count parity check between local dump and restored Cloud SQL before cutover.

## Open items / future
- GCS for media (replace MinIO) — needs a boto3↔GCS interop live probe.
- Off-Cloud-SQL backup copy to GCS if desired.
- Move secrets fetch from boot-script to a sidecar if rotation cadence increases.
```
