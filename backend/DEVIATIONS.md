# Deviations from the sibling Go house style

This service follows the conventions of `femrun`, `motifind`, and `bootstrap-api`
(Gin, layered `controllers`/`repositories`/`helpers`/`middlewares`/`db`,
`godotenv` config, multi-stage Docker, `gin-contrib/cors` v1.7.6 + gin v1.11.0 +
Go 1.24.2). Where it deliberately departs, here is why — so a teammate isn't
surprised.

## 1. Bare JSON responses — no `{success, data}` envelope
The sibling repos wrap responses with `helpers.SuccessJSON` / `ErrorJSON`
(`{"success":true,"data":...}`). This API does **not**. It returns the bare
shapes the Python FastAPI returned, because the web client
(`web/src/lib/adapters.ts`) calls `res.json()` and reads fields directly. Errors
use FastAPI's shape: `{"detail": "..."}`. Changing this would break the UI.

## 2. GORM in raw-SQL mode — no AutoMigrate, no ORM models
The siblings use GORM as an ORM and call `AutoMigrate` in `db.Init`. Here the
Postgres schema is **owned by the Python migrations** (`db/schema/*.sql` +
`db/migrate.py`) and is served primarily through a materialized view
(`frontend.emerging_scaling_apps`). So:

- We **never** call `AutoMigrate` and define **no** GORM table-mapped models.
- Every read is hand-ported parameterized SQL (a verbatim port of
  `src/agf/api/queries.py`) run via `db.Raw(...).Scan(...)`.
- The single write (`frontend.review_decisions`) is `db.Exec(...)` inside a
  committing transaction.

GORM is used purely as the connection pool + query runner, matching the house
DB library while staying safe against the Python-owned schema.

## 3. Null/format fidelity types
To reproduce Pydantic output exactly:

- Every nullable field is a pointer with **no `omitempty`**, so a missing value
  serializes as the literal `null` (present key), not an omitted key.
- Empty dossier slices are initialized to `[]T{}` so they render `[]`, not `null`.
- `models.APIDate` / `models.APITime` give Pydantic's `date` ("YYYY-MM-DD") and
  `datetime` (RFC3339 with numeric offset) formatting — a Postgres `date` column
  must not leak a time component.

## 4. Read-only transaction discipline
GET handlers run inside `db.WithReadTx`, which issues `SET TRANSACTION READ ONLY`
— the same default-deny guarantee the Python API gets in `src/agf/api/deps.py`.
Only the review POST uses a committing transaction (`db.WithWriteTx`).

## 5. CORS headers
`gin-contrib/cors` does not take a literal `*` for allow-headers the way
FastAPI's `allow_headers=["*"]` does, so we list the common request headers
explicitly (`Origin, Content-Type, Accept, Authorization, X-Requested-With`).
Functionally equivalent for the web client.

## Acceptable, semantically-irrelevant differences vs the Python API
JSON numbers may render as `74` vs Python's `74.0`, object key order may differ,
and a UTC datetime may print `+00:00` vs `Z` — all parse identically in JS/JSON.
The parity harness (`internal/parity`) compares **semantically** (parsed values),
not byte-for-byte, for exactly this reason.
