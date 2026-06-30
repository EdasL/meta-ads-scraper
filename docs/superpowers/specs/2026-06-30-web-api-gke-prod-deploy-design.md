# Field Desk — Web + API production deploy to GKE (`peon-489013`)

**Date:** 2026-06-30
**Status:** Approved design, pending implementation plan
**Owner:** elakavicius

## Goal

Move the **Go read-only API** (`backend/`) and the **Next.js web dashboard**
(`web/`) off the local Mac (launchd) and into Google Cloud, so the gem feed is
reachable from any device over HTTPS without the Mac staying awake. This
completes the prod migration started by the scraping+DB deploy
(`2026-06-26-peon-scraping-db-prod-design.md`): worker + Cloud SQL + media are
already in `peon-489013`; only the read path still depends on the Mac.

**Audience:** single user (the owner). No public signup, no new accounts — the
existing NextAuth login gates access. This is a lift-and-shift to a URL, not a
multi-tenant product.

## Approach: mirror the femrun GKE deployment

The owner already runs `femrun` on **GKE Autopilot** with this exact shape, and
`peon-489013` already has an Autopilot cluster (`peon-cluster`, europe-west1)
running granini. We add an **`agf` namespace** to that existing cluster — purely
additive, granini untouched. Pattern copied 1:1 from `femrun/{api,web}/k8s/`:

- API + Web each a single-replica `Deployment` + `Service` (ClusterIP).
- One GCE `Ingress` + global static IP, host-routing two subdomains.
- Two `ManagedCertificate`s for automatic HTTPS.
- API pod runs a **Cloud SQL Auth Proxy sidecar** (Workload Identity, no key file).
- Images in Artifact Registry; manual `docker build --platform linux/amd64` →
  push → `kubectl apply`/`set image` (same as femrun's DEPLOYMENT.md).

Cloud Run was considered and rejected only to **match the femrun runbook the
owner already operates** — one mental model, one set of `kubectl` muscle memory.

## Architecture

```
peon-489013 · europe-west1 · GKE Autopilot peon-cluster · namespace agf

 Browser ──HTTPS──> GCP HTTP(S) Load Balancer  (global static IP: agf-ip)
                       │
        Ingress agf-ingress ─┬─ host app.peon.works ─> Service agf-web  :8080
                             └─ host api.peon.works ─> Service agf-api  :8000
                                                          │
   Deployment agf-web (Next.js standalone, node server.js :8080)
   Deployment agf-api (Go agf-api :8000)
        └─ sidecar cloud-sql-proxy 127.0.0.1:5432 ─> Cloud SQL peon-489013:europe-west1:agf-db
        └─ GCS bucket agf-media-peon-489013  (read via ADC = Workload Identity, no HMAC)

 Cloud DNS zone peon-zone:
   app.peon.works  A -> agf-ip
   api.peon.works  A -> agf-ip
   (apex peon.works -> 34.120.57.217 = granini, UNTOUCHED)
```

## Components

### 1. Go API — `agf-api` (existing `backend/Dockerfile`, no code change)
- Image `europe-west1-docker.pkg.dev/peon-489013/agf/api:TAG` (reuses the
  existing `agf` Artifact Registry repo that already holds the worker image).
- `containerPort: 8000` (`PORT` default; Dockerfile already `EXPOSE 8000`).
- **Sidecar** `gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.14.3`, args
  `--address=127.0.0.1 --port=5432 peon-489013:europe-west1:agf-db`.
- Env (from `agf-secrets` + inline):
  - `AGF_DATABASE_URL=postgresql://agf:<pw>@127.0.0.1:5432/agf` (points at the
    sidecar — no DSN/code change vs the Mac proxy).
  - `AGF_CORS_ORIGINS=https://app.peon.works` (exact origin; `AllowCredentials=true`
    forbids `*`).
  - `S3_ENDPOINT=https://storage.googleapis.com`, `S3_BUCKET=agf-media-peon-489013`,
    `S3_REGION=europe-west1` — selects the native-GCS storage backend, which reads
    via **ADC** (Workload Identity). **No `MINIO_ACCESS_KEY/SECRET` needed for
    reads** (HMAC is worker-write-only) — to be confirmed by a build/probe in the
    plan; included as a guarded fallback if Init requires them.
  - `GEMINI_API_KEY` — the Go explainer (`gemini-2.0-flash`) uses it; include so
    on-demand explainers work (was unset on the Mac plist).
  - `GIN_MODE=release`, `PORT=8000`.
- `Service agf-api` :8000 with `cloud.google.com/backend-config` →
  `agf-backend-config` (longer LB timeout + connection draining).
- Probes: readiness + liveness `GET /healthz` (the API's existing health route).

### 2. Next.js web — `agf-web` (NEW `web/Dockerfile`)
- `web/Dockerfile` mirrors `femrun/web/Dockerfile` (node:20-alpine, `npm ci`,
  `npm run build`, copy `.next/standalone` + `.next/static` + `public`,
  `node server.js`, `PORT=8080`).
- Requires `output: "standalone"` in `web/next.config.ts` (add it).
- **Build-time** `NEXT_PUBLIC_*` come from a committed `web/.env.production`
  (no secrets — only public values):
  - `NEXT_PUBLIC_API_URL=https://api.peon.works/api` (must end in `/api`).
  - `NEXT_PUBLIC_DATA_MODE=live`.
- **Runtime** env (from `agf-web-secrets` + inline):
  - `AUTH_SECRET` (strong, generated), `AUTH_URL=https://app.peon.works`,
    `NEXTAUTH_URL=https://app.peon.works`, `AUTH_TRUST_HOST=true`.
  - `AUTH_USERS` — JSON `[{email,name,passwordHash}]` with the owner's real
    login (bcrypt hash), replacing the default `founder@fielddesk.local`/`fielddesk`.
  - `PORT=8080`, `HOSTNAME=0.0.0.0`, `NODE_ENV=production`.
- Add a trivial `web/src/app/api/health/route.ts` returning `200` for the
  k8s probes (mirrors femrun's `/api/health`).
- `Service agf-web` :8080.

### 3. Ingress + TLS — `backend/k8s/ingress.yaml`
- `ManagedCertificate agf-app-cert` → `app.peon.works`.
- `ManagedCertificate agf-api-cert` → `api.peon.works`.
- `Ingress agf-ingress` (`ingress.class: gce`), annotation
  `global-static-ip-name: agf-ip`, `managed-certificates: agf-app-cert,agf-api-cert`;
  host rules route app→agf-web, api→agf-api; `defaultBackend` = agf-web.
- `BackendConfig agf-backend-config`.

### 4. Identity / IAM (Workload Identity)
- GCP SA `agf-api@peon-489013.iam.gserviceaccount.com`:
  - `roles/cloudsql.client` (proxy sidecar).
  - `roles/storage.objectViewer` on bucket `agf-media-peon-489013` (media reads).
- KSA `agf-api` (namespace `agf`) annotated
  `iam.gke.io/gcp-service-account: agf-api@…`; WI binding
  `serviceAccount:peon-489013.svc.id.goog[agf/agf-api]` →
  `roles/iam.workloadIdentityUser`.
- The web pod needs no GCP identity (default KSA).

### 5. Secrets (`kubectl create secret`, namespace `agf`)
- `agf-secrets` (API): `AGF_DATABASE_URL`, `GEMINI_API_KEY`,
  optionally `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` only if a build proves reads
  need them.
- `agf-web-secrets` (web): `AUTH_SECRET`, `AUTH_USERS`.
- DB password is already in Secret Manager (`agf-db-url` from the prior deploy);
  reuse that value.

### 6. DNS — Cloud DNS zone `peon-zone`
- Add A-records `app.peon.works` and `api.peon.works` → the reserved `agf-ip`.
- Apex/`www` left as-is (granini).

### 7. Provisioning + manifests (committed)
- `backend/k8s/` : `api.yaml` (SA + Deployment + Service), `ingress.yaml`
  (2 certs + Ingress + BackendConfig).
- `web/k8s/` : `web.yaml` (Deployment + Service).
- `web/Dockerfile`, `web/.env.production`, `web/next.config.ts` (add standalone),
  `web/src/app/api/health/route.ts`.
- `ops/prod/03_provision_web_api.sh` — idempotent gcloud: reserve `agf-ip`,
  create SA + IAM, add DNS records (guarded on `describe`).
- `DEPLOYMENT.md` (repo root) — femrun-style runbook: build/push/apply, cert
  wait, update, rollback, common commands.

## Deploy / cutover flow

1. `gcloud config set project peon-489013`; `get-credentials peon-cluster --region europe-west1`.
2. Run `ops/prod/03_provision_web_api.sh`: reserve `agf-ip`, create `agf-api` SA
   + IAM + WI binding, add the two A-records.
3. `kubectl create namespace agf`; create `agf-secrets` + `agf-web-secrets`.
4. Build + push both images (`--platform linux/amd64`) to the `agf` repo.
5. `kubectl apply -f backend/k8s/ -f web/k8s/`; wait for pods Ready.
6. Wait for ManagedCertificates `Active` (~10–15 min; DNS must resolve to `agf-ip` first).
7. Verify: `curl https://api.peon.works/healthz`, load `https://app.peon.works`,
   log in, confirm the gem feed + creative media render from Cloud SQL/GCS.
8. Decommission the Mac read path: `launchctl bootout` `com.fielddesk.api`,
   `com.fielddesk.web`, `com.fielddesk.sqlproxy` (kept disabled as rollback for a
   few days, then removed).

## Error handling / day-2 ops
- Pod crash: Deployment restarts; `kubectl logs -n agf -l app=agf-api`.
- DB egress: stable via the proxy sidecar + Workload Identity (no node-IP allowlist).
- Update: `docker build/push :TAG` → `kubectl set image deployment/agf-{api,web} …`;
  rollback `kubectl rollout undo`.
- Cert/DNS: certs need DNS→`agf-ip` resolving first; `kubectl describe managedcertificate`.
- Cost: Autopilot bills per-pod requests (~small for 2 tiny pods + sidecar) + 1
  LB + 1 static IP. No second control plane (reused cluster).

## Testing / verification
- `go build ./...` (api) and `next build` (web) green locally before images.
- Docker images build for `linux/amd64`.
- Provisioning script idempotent (guard on `gcloud … describe`).
- **Live gate (project rule):** not "done" until, against real GKE + Cloud SQL +
  GCS, `https://app.peon.works` loads, login succeeds, and a gem row renders with
  its creative media (proves API→Cloud SQL→GCS-via-WI end-to-end). Verified in a
  browser, not just `curl`.

## Open items / future
- Confirm (build/probe) whether the GCS read path needs HMAC keys under ADC, or
  ADC alone suffices (expected: ADC alone).
- Auto-deploy via Cloud Build trigger / GitHub Action (manual for now, like femrun).
- API GET endpoints are currently unauthenticated; access is gated by it being a
  private dashboard + NextAuth on the web. If `api.peon.works` being publicly
  reachable is a concern, add the existing `AGF_API_TOKEN` guard to reads or an
  IAP/ingress restriction later (out of scope for this single-user lift-and-shift).
```
