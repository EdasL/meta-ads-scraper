# Web + API GKE Production Deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Host the Go read-only API and Next.js dashboard on GKE at
`api.peon.works` / `app.peon.works`, mirroring the femrun deployment, so the gem
feed is reachable over HTTPS without the Mac.

**Architecture:** Add an `agf` namespace to the existing `peon-cluster` (GKE
Autopilot, europe-west1). `agf-api` = existing `backend/Dockerfile` + a Cloud SQL
Auth Proxy sidecar (Workload Identity). `agf-web` = new Next-standalone Dockerfile.
One GCE Ingress + global static IP + two ManagedCertificates route the two
subdomains. Images in Artifact Registry `…/peon-489013/agf`. Manual build/push/apply.

**Tech Stack:** GKE Autopilot, kubectl, Cloud DNS, Artifact Registry, Cloud SQL
Auth Proxy v2, Docker (linux/amd64), Next.js 16 standalone, Go (Gin), gcloud CLI.

## Global Constraints

- Project `peon-489013`, region `europe-west1`, cluster `peon-cluster`, namespace `agf`.
- Cloud SQL connection name `peon-489013:europe-west1:agf-db`, db `agf`, user `agf`.
- Media bucket `agf-media-peon-489013`; reads via ADC/Workload Identity (no HMAC).
- Artifact Registry repo: `europe-west1-docker.pkg.dev/peon-489013/agf` (reuse existing).
- Build ALL images `--platform linux/amd64` (Autopilot is x86, Mac is ARM).
- `NEXT_PUBLIC_API_URL` MUST end in `/api` → `https://api.peon.works/api`.
- API CORS exact origin `https://app.peon.works` (`AllowCredentials=true` forbids `*`).
- Apex `peon.works` (granini, A→34.120.57.217) and `www` MUST stay untouched.
- Commit to `main` directly, explicit paths only. Secrets are NEVER committed.
- Product copy rule (CLAUDE.md): plain, clear, professional — applies to any new
  user-facing string (e.g. a health route returns no marketing copy).

---

### Task 1: Web container — standalone build, health route, env, Dockerfile

Makes the Next.js app buildable as a self-contained container image with a probe
endpoint and the prod API URL baked in.

**Files:**
- Modify: `web/next.config.ts` (ensure `output: "standalone"`)
- Create: `web/src/app/api/health/route.ts`
- Create: `web/.env.production`
- Create: `web/Dockerfile`
- Create/Modify: `web/.dockerignore`

**Interfaces:**
- Produces: a Docker image that runs `node server.js` on `:8080`, serving the
  dashboard and `GET /api/health` → `200 {"status":"ok"}`. Consumed by Task 3
  (`web/k8s/web.yaml`) and Task 6 (build/push).

- [ ] **Step 1: Add the health route (failing check first)**

Create `web/src/app/api/health/route.ts`:

```ts
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({ status: "ok" });
}
```

- [ ] **Step 2: Verify the route locally**

Run: `cd web && npm run build && npx next start -p 3010 & sleep 6 && curl -s localhost:3010/api/health; kill %1`
Expected: `{"status":"ok"}`

- [ ] **Step 3: Ensure standalone output**

In `web/next.config.ts`, confirm/add `output: "standalone"` in the config object
(top-level, alongside existing keys). If the project wraps the config (e.g.
Sentry), keep the wrapper; just add the key to the base object.

- [ ] **Step 4: Verify standalone artifact is produced**

Run: `cd web && rm -rf .next && npm run build && test -f .next/standalone/server.js && echo STANDALONE_OK`
Expected: `STANDALONE_OK`

- [ ] **Step 5: Create the build-time public env file**

Create `web/.env.production` (no secrets — public values only):

```bash
# Read by Next.js at build time. Public values only — no secrets here.
NEXT_PUBLIC_API_URL=https://api.peon.works/api
NEXT_PUBLIC_DATA_MODE=live
```

- [ ] **Step 6: Create the Dockerfile**

Create `web/Dockerfile`:

```dockerfile
FROM node:20-alpine AS base

FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* read from .env.production at build time
RUN npm run build

FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=8080
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 8080
CMD ["node", "server.js"]
```

- [ ] **Step 7: Ensure .dockerignore keeps the build context lean but allows .env.production**

Create or update `web/.dockerignore`:

```
node_modules
.next
.git
npm-debug.log
.env.local
.env*.local
```

(Note: `.env.production` is NOT ignored — it must reach the build stage.)

- [ ] **Step 8: Build the image for amd64 (verifies the whole container)**

Run: `cd web && docker build --platform linux/amd64 -t agf-web:test .`
Expected: build completes; final stage copies `server.js`. (No push yet.)

- [ ] **Step 9: Commit**

```bash
git add web/next.config.ts web/src/app/api/health/route.ts web/.env.production web/Dockerfile web/.dockerignore
git commit -m "feat(web): containerize Next.js standalone for GKE (health route, prod env, Dockerfile)"
```

---

### Task 2: Verify the Go API container + GCS-read-under-ADC assumption

The `backend/Dockerfile` already exists. This task confirms it builds for amd64
and resolves the spec's open question: does the GCS media read path need HMAC
keys, or does ADC (Workload Identity) alone suffice? No code change expected.

**Files:**
- Read: `backend/storage/storage.go`, `backend/storage/gcs.go`, `backend/config/config.go`
- (Only modify if Step 3 proves a gap.)

**Interfaces:**
- Produces: confirmation that `agf/api` image runs with only
  `S3_ENDPOINT/S3_BUCKET/S3_REGION` + ADC (no `MINIO_ACCESS_KEY`), informing
  which keys Task 5 puts in `agf-secrets`.

- [ ] **Step 1: Build the API image for amd64**

Run: `cd backend && docker build --platform linux/amd64 -t agf-api:test .`
Expected: build succeeds; `ENTRYPOINT ["/app/agf-api"]`.

- [ ] **Step 2: Inspect the storage Init branch**

Read `backend/storage/storage.go` `Init`: confirm that when `S3_ENDPOINT`
contains `storage.googleapis.com` it constructs the GCS client via
`gcs.NewClient(ctx)` (ADC) and does NOT require `MINIO_ACCESS_KEY/SECRET`.

- [ ] **Step 3: Decide the secret set**

- If GCS branch uses ADC only → `agf-secrets` carries NO HMAC keys (preferred).
- If Init hard-requires the HMAC env even on the GCS path → record that
  `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` (GCS HMAC) must go in `agf-secrets`.

Write the decision as a one-line comment at the top of `backend/k8s/api.yaml`
(created in Task 3) so Task 5 matches it. No code commit unless a code gap is found.

- [ ] **Step 4: (Only if a code gap is found) commit the minimal fix**

```bash
git add backend/storage/storage.go
git commit -m "fix(api): allow GCS media reads via ADC without HMAC keys"
```

---

### Task 3: Kubernetes manifests (API, web, Ingress, certs, BackendConfig)

**Files:**
- Create: `backend/k8s/api.yaml`
- Create: `backend/k8s/ingress.yaml`
- Create: `web/k8s/web.yaml`

**Interfaces:**
- Consumes: images from Task 6, SA/IP/DNS from Task 4, secrets from Task 5.
- Produces: `Service agf-api` (:8000), `Service agf-web` (:8080),
  `Ingress agf-ingress` routing `api.peon.works`→agf-api, `app.peon.works`→agf-web.

- [ ] **Step 1: API manifest (SA + Deployment + Service)**

Create `backend/k8s/api.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: agf-api
  namespace: agf
  annotations:
    iam.gke.io/gcp-service-account: agf-api@peon-489013.iam.gserviceaccount.com
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agf-api
  namespace: agf
spec:
  replicas: 1
  selector:
    matchLabels:
      app: agf-api
  template:
    metadata:
      labels:
        app: agf-api
    spec:
      serviceAccountName: agf-api
      containers:
        - name: cloud-sql-proxy
          image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.14.3
          args:
            - "--structured-logs"
            - "--address=127.0.0.1"
            - "--port=5432"
            - "peon-489013:europe-west1:agf-db"
          securityContext:
            runAsNonRoot: true
          resources:
            requests: { memory: "64Mi", cpu: "50m" }
            limits: { memory: "128Mi", cpu: "200m" }
        - name: api
          image: europe-west1-docker.pkg.dev/peon-489013/agf/api:latest
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: agf-secrets
          env:
            - name: GIN_MODE
              value: "release"
            - name: PORT
              value: "8000"
            - name: AGF_CORS_ORIGINS
              value: "https://app.peon.works"
            - name: S3_ENDPOINT
              value: "https://storage.googleapis.com"
            - name: S3_BUCKET
              value: "agf-media-peon-489013"
            - name: S3_REGION
              value: "europe-west1"
          resources:
            requests: { memory: "128Mi", cpu: "100m" }
            limits: { memory: "512Mi", cpu: "1000m" }
          readinessProbe:
            httpGet: { path: /healthz, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /healthz, port: 8000 }
            initialDelaySeconds: 10
            periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: agf-api
  namespace: agf
  annotations:
    cloud.google.com/backend-config: '{"default": "agf-backend-config"}'
spec:
  selector:
    app: agf-api
  ports:
    - port: 8000
      targetPort: 8000
```

(Top of file: add the one-line HMAC decision comment from Task 2 Step 3.)

- [ ] **Step 2: Ingress + ManagedCertificates + BackendConfig**

Create `backend/k8s/ingress.yaml`:

```yaml
apiVersion: cloud.google.com/v1
kind: BackendConfig
metadata:
  name: agf-backend-config
  namespace: agf
spec:
  timeoutSec: 3600
  connectionDraining:
    drainingTimeoutSec: 60
---
apiVersion: networking.gke.io/v1
kind: ManagedCertificate
metadata:
  name: agf-api-cert
  namespace: agf
spec:
  domains:
    - api.peon.works
---
apiVersion: networking.gke.io/v1
kind: ManagedCertificate
metadata:
  name: agf-app-cert
  namespace: agf
spec:
  domains:
    - app.peon.works
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agf-ingress
  namespace: agf
  annotations:
    kubernetes.io/ingress.global-static-ip-name: agf-ip
    networking.gke.io/managed-certificates: agf-api-cert,agf-app-cert
    kubernetes.io/ingress.class: "gce"
spec:
  rules:
    - host: api.peon.works
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: agf-api
                port:
                  number: 8000
    - host: app.peon.works
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: agf-web
                port:
                  number: 8080
  defaultBackend:
    service:
      name: agf-web
      port:
        number: 8080
```

- [ ] **Step 3: Web manifest (Deployment + Service)**

Create `web/k8s/web.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agf-web
  namespace: agf
spec:
  replicas: 1
  selector:
    matchLabels:
      app: agf-web
  template:
    metadata:
      labels:
        app: agf-web
    spec:
      containers:
        - name: web
          image: europe-west1-docker.pkg.dev/peon-489013/agf/web:latest
          ports:
            - containerPort: 8080
          envFrom:
            - secretRef:
                name: agf-web-secrets
          env:
            - name: PORT
              value: "8080"
            - name: NODE_ENV
              value: "production"
            - name: HOSTNAME
              value: "0.0.0.0"
            - name: AUTH_URL
              value: "https://app.peon.works"
            - name: NEXTAUTH_URL
              value: "https://app.peon.works"
            - name: AUTH_TRUST_HOST
              value: "true"
          resources:
            requests: { memory: "128Mi", cpu: "100m" }
            limits: { memory: "256Mi", cpu: "500m" }
          readinessProbe:
            httpGet: { path: /api/health, port: 8080 }
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /api/health, port: 8080 }
            initialDelaySeconds: 10
            periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: agf-web
  namespace: agf
spec:
  selector:
    app: agf-web
  ports:
    - port: 8080
      targetPort: 8080
```

- [ ] **Step 4: Validate manifests offline**

Run: `kubectl apply --dry-run=client -f backend/k8s/api.yaml -f backend/k8s/ingress.yaml -f web/k8s/web.yaml`
Expected: each object prints `(dry run)` with no schema errors. (CRDs like
ManagedCertificate/BackendConfig may warn on client dry-run; use
`--validate=false` if needed — they validate live in Task 6.)

- [ ] **Step 5: Commit**

```bash
git add backend/k8s/api.yaml backend/k8s/ingress.yaml web/k8s/web.yaml
git commit -m "feat(deploy): k8s manifests for agf-api + agf-web + ingress on peon-cluster"
```

---

### Task 4: Provisioning script — static IP, service account, IAM, DNS

**Files:**
- Create: `ops/prod/03_provision_web_api.sh`

**Interfaces:**
- Produces: global static IP `agf-ip`, GCP SA `agf-api@…` with cloudsql.client +
  bucket objectViewer + WI binding, and A-records `api/app.peon.works`→agf-ip.
  Consumed by Tasks 3/6 (Ingress IP name, WI annotation).

- [ ] **Step 1: Write the idempotent provisioning script**

Create `ops/prod/03_provision_web_api.sh`:

```bash
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
```

- [ ] **Step 2: Lint the script**

Run: `bash -n ops/prod/03_provision_web_api.sh && echo SYNTAX_OK`
Expected: `SYNTAX_OK`

- [ ] **Step 3: Commit**

```bash
git add ops/prod/03_provision_web_api.sh
git commit -m "feat(deploy): idempotent provisioning for agf web+API (IP, SA, IAM, DNS)"
```

---

### Task 5: Secrets (live, not committed) — documented in the runbook

Secrets are created with `kubectl create secret` against the live cluster. They
are NOT committed. This task documents the exact commands in `DEPLOYMENT.md`
(Task 7) and is executed during the live deploy (Task 6).

**Interfaces:**
- Produces: `agf-secrets` (API) and `agf-web-secrets` (web) in namespace `agf`,
  consumed by the Deployments' `envFrom.secretRef`.

- [ ] **Step 1: Assemble the DB URL from Secret Manager**

The DB password already lives in Secret Manager (`agf-db-url` from the prior
deploy). Derive the proxy-pointed URL:
`AGF_DATABASE_URL=postgresql://agf:<pw>@127.0.0.1:5432/agf`.

- [ ] **Step 2: Generate the web auth secret + user hash**

```bash
AUTH_SECRET=$(openssl rand -hex 32)
# bcrypt hash for the owner's password (Node, project already has bcrypt):
node -e "console.log(require('bcryptjs').hashSync(process.argv[1],10))" 'YOUR_PASSWORD'
```
Build `AUTH_USERS` JSON: `[{"email":"<you>","name":"<you>","passwordHash":"<hash>"}]`.

- [ ] **Step 3: (live, Task 6) create the secrets** — see DEPLOYMENT.md §Secrets.
No commit (secrets are not stored in git). This task's deliverable is the
documented procedure in Task 7.

---

### Task 6: Live deploy + verification gate

Controller-executed against real GCP. This is the project's mandatory live gate.

**Steps:**

- [ ] **Step 1: Cluster context**

```bash
gcloud config set project peon-489013
gcloud container clusters get-credentials peon-cluster --region europe-west1
kubectl create namespace agf --dry-run=client -o yaml | kubectl apply -f -
```

- [ ] **Step 2: Run provisioning** — `bash ops/prod/03_provision_web_api.sh`
(creates IP, SA, IAM, DNS). Note the printed IP.

- [ ] **Step 3: Create secrets (live)**

```bash
kubectl create secret generic agf-secrets -n agf \
  --from-literal=AGF_DATABASE_URL='postgresql://agf:<pw>@127.0.0.1:5432/agf' \
  --from-literal=GEMINI_API_KEY='<key>'   # + MINIO_* only if Task 2 found they are required
kubectl create secret generic agf-web-secrets -n agf \
  --from-literal=AUTH_SECRET='<hex32>' \
  --from-literal=AUTH_USERS='[{"email":"...","name":"...","passwordHash":"..."}]'
```

- [ ] **Step 4: Build + push both images**

```bash
REG=europe-west1-docker.pkg.dev/peon-489013/agf
gcloud auth configure-docker europe-west1-docker.pkg.dev -q
TAG=$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -t $REG/api:$TAG -t $REG/api:latest backend && docker push $REG/api:$TAG && docker push $REG/api:latest
docker build --platform linux/amd64 -t $REG/web:$TAG -t $REG/web:latest web && docker push $REG/web:$TAG && docker push $REG/web:latest
```

- [ ] **Step 5: Apply manifests**

```bash
kubectl apply -f backend/k8s/api.yaml -f backend/k8s/ingress.yaml -f web/k8s/web.yaml
kubectl rollout status deployment/agf-api -n agf
kubectl rollout status deployment/agf-web -n agf
```

- [ ] **Step 6: Wait for certs + backends (10–15 min)**

```bash
kubectl describe managedcertificate agf-api-cert -n agf | grep -i status
kubectl describe managedcertificate agf-app-cert -n agf | grep -i status
kubectl get ingress agf-ingress -n agf -o jsonpath='{.metadata.annotations.ingress\.kubernetes\.io/backends}'; echo
```
Expected eventually: both certs `Active`, backends `HEALTHY`.

- [ ] **Step 7: LIVE GATE — verify end-to-end in a browser**

- `curl -s https://api.peon.works/healthz` → healthy.
- Open `https://app.peon.works`, log in with the owner account.
- Confirm the gem feed loads AND at least one gem renders its creative media
  (proves API→Cloud SQL via proxy + GCS via Workload Identity end-to-end).

Use Playwright to load the page, authenticate, and screenshot a gem card with a
visible creative. Do not declare done until a real gem + media renders.

---

### Task 7: DEPLOYMENT.md runbook

**Files:**
- Create: `DEPLOYMENT.md` (repo root)

- [ ] **Step 1: Write the runbook** mirroring `femrun/DEPLOYMENT.md`: header
(project/region/cluster/IP table), prerequisites, the §Secrets commands from
Task 5, build/push/apply, cert wait, "Updating the API/Web" (`kubectl set image`
+ `rollout undo`), and a Common Commands + Gotchas section (amd64 platform; certs
need DNS→agf-ip first; `NEXT_PUBLIC_*` are build-time; reused cluster — never
`kubectl delete namespace` granini's).

- [ ] **Step 2: Commit**

```bash
git add DEPLOYMENT.md
git commit -m "docs(deploy): GKE runbook for agf web+API"
```

---

### Task 8: Cutover — retire the Mac read path

Only after Task 6's live gate passes.

- [ ] **Step 1: Stop the Mac agents**

```bash
launchctl bootout gui/501/com.fielddesk.web    2>/dev/null || true
launchctl bootout gui/501/com.fielddesk.api    2>/dev/null || true
launchctl bootout gui/501/com.fielddesk.sqlproxy 2>/dev/null || true
```

- [ ] **Step 2: Confirm the site still serves from the cloud**

Reload `https://app.peon.works` (Mac agents down) — feed still loads.
The plists remain on disk as a rollback for a few days.

- [ ] **Step 3: Update memory** — note the web+API are now on GKE
(`app/api.peon.works`), Mac launchd read path retired, deploy via
`DEPLOYMENT.md`. (Memory write, not a git commit.)

---

## Self-Review

**Spec coverage:** Architecture (Tasks 3/6) · agf-api + sidecar (Task 3) · agf-web
Dockerfile/standalone/env/health (Task 1) · Ingress + 2 certs + BackendConfig
(Task 3) · Workload Identity SA + IAM (Task 4) · secrets (Task 5/6) · DNS (Task 4)
· deploy/cutover flow (Tasks 6/8) · live gate (Task 6 Step 7) · GCS-ADC open item
(Task 2) · DEPLOYMENT.md (Task 7). All spec sections map to a task.

**Placeholders:** `<pw>`, `<key>`, `YOUR_PASSWORD`, owner email/hash are
intentional live secrets supplied at deploy time, not plan gaps — every file to
create has complete literal content.

**Type/name consistency:** namespace `agf`, SA `agf-api@peon-489013…`, KSA
`agf-api`, services `agf-api`(:8000)/`agf-web`(:8080), certs
`agf-api-cert`/`agf-app-cert`, IP `agf-ip`, repo `…/peon-489013/agf`, hosts
`api`/`app.peon.works` — consistent across Tasks 1–8 and the manifests.
```
