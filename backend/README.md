# backend — Field Desk API (Go)

The read-only HTTP API, in Go (Gin + GORM). It replaces the Python FastAPI
service (`src/agf/api`) and is a second client of the same Postgres + MinIO.
**Python keeps everything that produces data** — scraping, discovery,
enrichment, scoring/GemScore, the worker loop, and the DB migrations. This
service only reads the schema (a frozen materialized view + joins) and writes a
single row: the human review decision.

## Endpoints (bare JSON, mirrors the Python contract)

| Method | Path | Notes |
|--------|------|-------|
| GET  | `/api/shortlist?cohort=&min_score=0.0&limit=50&offset=0` | matview list |
| GET  | `/api/gems/{advertiser_id}` | dossier; 404 if absent |
| GET  | `/api/gems/{advertiser_id}/scaling` | weekly metrics |
| GET  | `/api/review/queue?min_score=70.0&limit=50&offset=0` | undecided alerts |
| POST | `/api/review/{advertiser_id}/decision` | the only write |
| GET  | `/api/health/metrics` | crawl-log metrics + rotation alerts |
| GET  | `/api/media/*key` | streams creative/funnel bytes from S3 (key-allowlisted) |
| GET  | `/healthz` | liveness |

## Layout

```
config/        env loading (+ DSN normalize: postgresql+psycopg:// → postgres://)
db/            GORM pool, read-only/write tx helpers, boot sanity probe
models/        response shapes + APIDate/APITime (Pydantic-exact null & date/time)
repositories/  raw-SQL ports of src/agf/api/queries.py + monitoring/metrics.py
controllers/   gin handlers (one per route group)
storage/       MinIO/S3 client (path-style, custom endpoint)
middlewares/   CORS
helpers/        bare-JSON + {"detail":...} error helpers, media-key allowlist
routers/       /api group + /healthz
internal/integration  full-stack test vs a real Postgres
internal/parity       semantic diff Go vs Python (cutover gate)
```

See `DEVIATIONS.md` for where this departs from the sibling Go repos' house style
(bare JSON, GORM-in-raw-SQL-mode, null/format fidelity).

## Run locally

```bash
cp .env.example .env            # point AGF_DATABASE_URL at your Postgres
go run .                        # serves on :8000 (PORT to override)
```

## Test

```bash
go test ./...                   # unit tests (no DB needed)

# Integration (full stack vs a real Postgres with the schema applied):
AGF_IT_DB_URL="postgres://postgres:postgres@localhost:55432/agf?sslmode=disable" \
  go test ./internal/integration/ -v

# Parity gate (run both APIs against the SAME seeded DB, then diff):
#   python: AGF_DATABASE_URL=postgresql+psycopg://… PYTHONPATH=src \
#             uvicorn agf.api.asgi:app --port 8001
#   go:     AGF_DATABASE_URL=postgres://…           PORT=8000 ./agf-api
PARITY_PY_URL=http://localhost:8001 PARITY_GO_URL=http://localhost:8000 \
  go test ./internal/parity/ -v
```

## Cutover & rollback (ops/docker-compose.yml)

The compose `api` service now builds from `backend/` and runs the Go binary.
The original Python API is preserved as `api-py` behind the `fallback` profile:

```bash
docker compose up -d api                      # Go API on :8000
# rollback:
docker compose stop api
docker compose --profile fallback up -d api-py  # FastAPI on :8000
```

The web frontend needs no change — same URL, port, and wire shapes.
