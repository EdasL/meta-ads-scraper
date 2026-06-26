# Deployment

How to ship a new version of the scrapers and the rest of Field Desk.

This is the **task-oriented** guide ("I changed X, how do I deploy it?"). For the
full topology, day-2 ops, and rollback, see `ops/RUNBOOK.md` → "Production on
peon-489013". For a from-scratch re-provision, see `ops/prod/`.

---

## Where things run

| Part | Runs | Deploy = |
|---|---|---|
| **Scraper** (worker: sweep / discover / enrich) | VM `agf-worker` in GCP `peon-489013` (docker) | rebuild image → pull on VM |
| **Scoring + cron** (scoring /30min, matview hourly, prune daily) | same VM, same image (`cron` container) | same as worker |
| **Database** | Cloud SQL `agf-db` (managed) | apply migrations |
| **Creative media store** (MinIO) | same VM | rarely changes; image/compose |
| **API** (Go, read-only) | your **Mac** (launchd `com.fielddesk.api`) | rebuild Go binary + kickstart |
| **Web** (Next.js dashboard) | your **Mac** (launchd `com.fielddesk.web`) | rebuild + kickstart |

The scraper + DB run **24/7 in the cloud, independent of your Mac**. The API + web
run only while your Mac is on (they're the dashboard, not the pipeline).

**Constants used below**
- Project `peon-489013` · region `europe-west1` · zone `europe-west1-b`
- Image `europe-west1-docker.pkg.dev/peon-489013/agf/worker:latest`
- VM compose file on the box: `/opt/agf/docker-compose.yml`
- SSH: `gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013`

> Tip: export a helper once per shell so the SSH commands below are shorter:
> ```bash
> agfssh() { gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command "$1"; }
> DC="sudo docker compose -f /opt/agf/docker-compose.yml"
> ```

---

## Prerequisites (one-time per machine)

```bash
gcloud auth login                              # gcloud CLI (you = owner on peon-489013)
gcloud auth application-default login          # ADC — needed by the local cloud-sql-proxy
gcloud config set project peon-489013          # optional; the scripts pass --project explicitly
# Tools the migration / proxy steps use (already installed on this Mac):
#   /opt/homebrew/opt/libpq/bin/{psql,pg_dump,pg_restore}   (brew install libpq)
#   /opt/homebrew/bin/cloud-sql-proxy                       (v2.x)
```

The Mac's local Cloud SQL proxy (`com.fielddesk.sqlproxy`, port 5433) must be running
for any local DB access — it's a launchd agent, so it's always up while the Mac is on.

---

## 1. Deploy a new scraper / scoring / cron version (the common case)

All three (worker, scoring, cron) share **one image**, so any change to the Python
pipeline (`src/agf/**`, `scripts/**`, cron scripts) ships the same way.

**If your change includes a new DB migration** (`db/schema/NNN_*.sql`), do
[section 2](#2-deploy-a-database-migration) **first**, then come back here.

```bash
# 0. commit your change on main first (repo is solo / local-only)
git add -A path/to/changed && git commit -m "..."

# 1. build the new image (Cloud Build → Artifact Registry, amd64). ~3 min.
bash ops/prod/02_build_image.sh
#    => "pushed europe-west1-docker.pkg.dev/peon-489013/agf/worker:latest"

# 2. roll it on the VM: pull the new :latest and recreate worker + cron
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo docker compose -f /opt/agf/docker-compose.yml pull worker cron && \
   sudo docker compose -f /opt/agf/docker-compose.yml up -d worker cron"
```

`up -d` recreates only the containers whose image digest changed; `cloud-sql-proxy`
and `minio` keep running untouched. The worker restarts into a fresh sweep cycle and
re-heals its Meta token/`doc_id` automatically.

Then **verify** — see [section 6](#6-verify-a-deploy).

> The image tag is `:latest`. That's fine for this single-box setup. If you ever want
> reproducible rollbacks, tag by commit instead: edit `ops/prod/02_build_image.sh` and
> `ops/prod/cloudbuild.yaml` to use `worker:$(git rev-parse --short HEAD)` and pin the
> same tag in `ops/prod/docker-compose.yml`.

---

## 2. Deploy a database migration

Schema is Python-owned; `db/schema/*.sql` files are applied by the **idempotent**
`apply_migrations()` (a file is applied once, tracked in `public.schema_migrations`).
There is **no auto-runner** — apply it explicitly. Do this **before** rolling new code
that depends on the new columns.

Run it from the Mac against Cloud SQL (through the local proxy on :5433), using the
repo's current `db/schema/`:

```bash
cd /Users/edaslakavicius/Projects/emerging-app-finder
DB_PW="$(gcloud secrets versions access latest --secret=agf-db-password --project=peon-489013)"
AGF_DATABASE_URL="postgresql://agf:${DB_PW}@localhost:5433/agf" \
  PYTHONPATH=src .venv/bin/python -c \
  "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; \
   print('applied:', apply_migrations(get_engine()))"
# => applied: ['142_my_new_migration.sql']   (or [] if nothing new)
```

Idempotent: re-running prints `applied: []`. If a migration changes the product
matview, refresh it (or wait for the hourly cron):

```bash
AGF_DATABASE_URL="postgresql://agf:${DB_PW}@localhost:5433/agf" \
  /opt/homebrew/opt/libpq/bin/psql "postgresql://agf:${DB_PW}@localhost:5433/agf" \
  -c "REFRESH MATERIALIZED VIEW CONCURRENTLY frontend.emerging_scaling_apps;"
```

After the schema is in place, ship the code via [section 1](#1-deploy-a-new-scraper--scoring--cron-version-the-common-case).

---

## 3. Change config / env / secrets

### Worker env tunables (sweep pages, budgets, intervals, etc.)
These live in `/opt/agf/.env` on the VM (rendered at boot from `ops/prod/`). The
defaults are in `ops/prod/docker-compose.yml`. To change one live:

```bash
# edit /opt/agf/.env on the VM, then recreate the affected services
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013
  sudo nano /opt/agf/.env
  sudo docker compose -f /opt/agf/docker-compose.yml up -d worker cron
```
To make the change permanent across a VM rebuild, also update the default in
`ops/prod/docker-compose.yml` and commit.

### Rotate a secret (DB password, DataImpulse proxy, Gemini key)
Secrets live in Secret Manager and are read into `/opt/agf/.env` at VM boot.

```bash
printf '%s' 'NEW_VALUE' | gcloud secrets versions add agf-proxies \
  --project=peon-489013 --data-file=-
# re-render /opt/agf/.env and restart: simplest is to re-run the boot path
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo google_metadata_script_runner startup && \
   sudo docker compose -f /opt/agf/docker-compose.yml up -d"
```
Secret names: `agf-db-password`, `agf-postgres-password`, `agf-minio-password`,
`agf-proxies` (DataImpulse), `agf-gemini-api-key`. If you rotate `agf-db-password`,
also update the Mac API plist's `AGF_DATABASE_URL` (see section 4) and the Cloud SQL
user: `gcloud sql users set-password agf --instance=agf-db --project=peon-489013 --password=...`.

### DataImpulse out of traffic (`407 TRAFFIC_EXHAUSTED`)
The worker logs `sweep circuit-breaker: 5 consecutive search errors` and stops
producing. Top up the **same** account at dataimpulse.com (creds don't change), then
restart the worker: `… up -d worker` is not enough — force a fresh token heal:
```bash
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo docker compose -f /opt/agf/docker-compose.yml restart worker"
```

---

## 4. Deploy the Mac API (Go, read-only)

The API is a prebuilt binary at `~/bin/agf-api`, run by launchd
(`com.fielddesk.api`). After changing `backend/**`:

```bash
cd /Users/edaslakavicius/Projects/emerging-app-finder/backend
go build -o ~/bin/agf-api .
launchctl kickstart -k gui/$(id -u)/com.fielddesk.api
```
It reads `AGF_DATABASE_URL` (→ `localhost:5433` → Cloud SQL) and `S3_ENDPOINT` (local
MinIO) from its plist. If a parallel session has WIP in the tree, build from a clean
worktree: `git worktree add /tmp/agf-build HEAD && cd /tmp/agf-build/backend && go build -o ~/bin/agf-api .`.

---

## 5. Deploy the Mac web (Next.js dashboard)

The web is built **once** in live mode and served on :3000 by launchd
(`com.fielddesk.web` → `~/bin/agf-web.sh`). The build is gated by a marker file so
KeepAlive restarts are fast — so after changing `web/**` you must **clear the marker**
to force a rebuild:

```bash
rm -f /Users/edaslakavicius/Projects/emerging-app-finder/web/.next/.livebuild
launchctl kickstart -k gui/$(id -u)/com.fielddesk.web
# tail the rebuild: tail -f ~/Library/Logs/fielddesk-web.log   (path per the plist)
```
`agf-web.sh` writes `web/.env.local` itself (`NEXT_PUBLIC_API_URL=http://localhost:8000/api`,
baked at build time). You don't normally edit it. A plain restart **without** removing
the marker just re-serves the existing build (use that to pick up the running API,
not code changes).

---

## 6. Verify a deploy

```bash
# worker came back and is healthy
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo docker compose -f /opt/agf/docker-compose.yml ps"
#   worker => Up (healthy); cloud-sql-proxy/minio/cron => Up; minio-init => Exited (0, normal)

# end-to-end Meta egress smoke (one keyword, real search → parse → triage)
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo docker compose -f /opt/agf/docker-compose.yml exec -T worker python scripts/sweep_smoke.py 2>&1 | tail -5"
#   expect: raw_ads > 0, errors: 0, "SMOKE OK"

# worker logs (look for a clean sweep, no circuit-breaker)
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo docker compose -f /opt/agf/docker-compose.yml logs --tail=40 worker"

# DB is serving (from the Mac, via the :5433 proxy)
curl -s "http://localhost:8000/api/shortlist?limit=1" -D - -o /dev/null | grep -i x-total-count
```

For DB migrations also confirm `apply_migrations` returned the expected filename and
that dependent queries work.

---

## 7. Rollback

- **Bad scraper image:** rebuild from the previous commit and re-pull, or (if you
  adopted commit tags) `docker compose pull` the prior tag. The DB is unaffected by a
  worker rollback.
- **Bad migration:** migrations are forward-only here. Write a new `NNN_*.sql` that
  reverses it and apply via section 2. (Cloud SQL also has automated backups + PITR if
  you need a point-in-time restore — last resort, coordinate downtime.)
- **Whole deploy gone wrong / back to local scraping:** see `ops/RUNBOOK.md` →
  "Rollback to local" (re-`bootstrap` the `com.fielddesk.worker`/`scoring` launchd
  agents, `docker start db-postgres-1`, revert the API plist DB URL). The local docker
  Postgres still holds the pre-cutover snapshot.

---

## 8. Quick reference

```bash
# SSH to the scraper box
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013

# on the box — status / logs / restart
sudo docker compose -f /opt/agf/docker-compose.yml ps
sudo docker compose -f /opt/agf/docker-compose.yml logs --tail=80 worker
sudo docker compose -f /opt/agf/docker-compose.yml restart worker

# build + roll a new scraper version (from the Mac, repo root)
bash ops/prod/02_build_image.sh
gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013 --command \
  "sudo docker compose -f /opt/agf/docker-compose.yml pull worker cron && \
   sudo docker compose -f /opt/agf/docker-compose.yml up -d worker cron"

# Mac API redeploy
( cd backend && go build -o ~/bin/agf-api . ) && launchctl kickstart -k gui/$(id -u)/com.fielddesk.api

# Mac web redeploy (force rebuild)
rm -f web/.next/.livebuild && launchctl kickstart -k gui/$(id -u)/com.fielddesk.web

# from-scratch provision (new project / DR) — dependency order, NOT numeric:
bash ops/prod/02_secrets.sh && bash ops/prod/00_provision_sql.sh && \
  bash ops/prod/02_build_image.sh && bash ops/prod/01_provision_vm.sh
```
