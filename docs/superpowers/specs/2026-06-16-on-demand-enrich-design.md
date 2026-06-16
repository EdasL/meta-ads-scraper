# On-demand "Refresh data" for a gem — design

**Date:** 2026-06-16
**Status:** Approved for planning

## Goal

A button on a gem's detail page that force-refreshes that one app on demand —
re-pulling its ads, store metadata, ad media, and funnel — bypassing the
background sweep's TTL, then re-scoring it so the page shows fresh numbers.
The user waits with a live status indicator (~30–90s).

## Decisions (locked)

| Question | Decision |
|---|---|
| Input | Existing app, force-refresh (not new-URL ingestion) |
| UX | Wait with live status (poll job state, refresh page on done) |
| Placement | Detail page header only, next to Save |
| Runner | Go API spawns a one-shot Python subprocess (no new daemon) |
| Auth | Reuse the existing review token gating `POST /review/:id/decision` |
| Scope | Full `enrich_gem` — ads + store + media + funnel capture |
| Re-score | Yes — re-score this advertiser + refresh matview after enrich |

## Flow

```
Detail page "Refresh data"
  → POST /api/enrich/:advertiser_id        (Go, review-token gated)
      → dedupe: running row <180s old? return it
      → insert enrich_jobs row (status=running)
      → spawn scripts/enrich_one.sh --advertiser-id N --job-id M  (detached)
      → return {job_id, status:"running"}
  → web polls GET /api/enrich/:advertiser_id every ~3s
  → subprocess: enrich_gem → score(only_ids=[N]) → refresh matview
      → marks job done / error (+ error_text)
  → on done: router.refresh() refetches the dossier → fresh numbers
```

## 1. Data model — `public.enrich_jobs`

New migration (next number after current head).

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `advertiser_id` | bigint, indexed | the gem being refreshed |
| `status` | text | `running` \| `done` \| `error` |
| `error_text` | text null | populated on failure |
| `started_at` | timestamptz default now() | |
| `finished_at` | timestamptz null | |

Index on `(advertiser_id, started_at desc)` — the GET endpoint reads the
latest row per advertiser. No `queued` state: Go spawns immediately, so a job
is `running` from insertion.

## 2. Python one-shot — `python -m agf.enrich_full.enrich_one`

New thin module: `src/agf/enrich_full/enrich_one.py`.

Args: `--advertiser-id N` (required), `--job-id M` (required),
`--top-n 5`, `--media-cap 120`, `--timeout 180`.

Responsibilities:
1. Mark the job `running` (idempotent — Go already inserted it; this is a
   no-op guard / ownership claim).
2. Build deps mirroring `production_deps_factory` (main.py:171):
   `Extractor()`, `httpx.Client`, `get_s3()`/`put_bytes`, `whois`,
   `EnrichDeps(page=None, proxy=$AGF_FUNNEL_PROXY, capture=True)`.
   Creative-URL prefetch: reuse the worker's prefetch helper; if it can't be
   built standalone, pass `creative_urls={}` (ad media still recovers via the
   media stage inside enrich).
3. `asyncio.run(enrich_gem(conn, advertiser_id, top_n, media_cap, deps))`.
4. On success: re-score this advertiser only — `score`/`snapshot` with
   `only_ids=[advertiser_id]` (the `only_ids` path already exists), then
   refresh `frontend.emerging_scaling_apps` so the dossier reflects new data.
5. Mark the job `done` (set `finished_at`). On any exception, mark `error`
   with `error_text` (truncated) and `finished_at`. Wrap in `--timeout` so a
   hung Playwright render can't wedge the row.

Playwright launches per-funnel on demand (`page=None`) — no persistent
browser, clean teardown per call. `enrich_gem` itself is unchanged.

## 3. Go API — `backend/controllers/enrich_controller.go`

Registered in `backend/routers/routers.go` under `/api`.

- **`POST /api/enrich/:advertiser_id`** — review-token gated (mirror the
  middleware/check used by `POST /review/:advertiser_id/decision`).
  - Dedupe: if an `enrich_jobs` row for this advertiser is `running` and
    `started_at` is within the timeout window, return that row (no new spawn).
  - Insert a `running` row, capture `id`.
  - Spawn `scripts/enrich_one.sh --advertiser-id N --job-id <id>` **detached**
    (`cmd.Start()`, not `Run()`; release the process so an API restart doesn't
    kill it). Configurable interpreter/script path via env
    (`AGF_ENRICH_SCRIPT`, default `scripts/enrich_one.sh` relative to repo).
  - Return `{job_id, status:"running"}`.
- **`GET /api/enrich/:advertiser_id`** — return the latest job row:
  `{job_id, status, error_text, started_at, finished_at}`. If the latest row
  is `running` but older than the timeout, report it as `error`
  (stale-guard — covers a subprocess that died without writing).

## 4. Env / spawn wiring — `scripts/enrich_one.sh`

Go does not own the Python venv or its env. The wrapper:
1. Sources `ops/.env` (DB url, `AGF_PROXIES`, `AGF_FUNNEL_PROXY`, S3/MinIO
   creds + bucket).
2. Execs the venv Python: `exec python -m agf.enrich_full.enrich_one "$@"`.

Single place to fix environment; the Go API stays ignorant of Python
internals. (Matches the existing worker-plist env pattern.)

## 5. Web UI

`web/src/lib/api.ts`:
- `postEnrich(advertiserId)` → `POST /api/enrich/:id` (sends review token).
- `getEnrichStatus(advertiserId)` → `GET /api/enrich/:id`.

Detail header (next to Save): a `RefreshDataButton` client component — state
machine:
- **idle** → label "Refresh data".
- **running** → "Refreshing…", disabled; poll `getEnrichStatus` every ~3s.
- **done** → call `router.refresh()` to refetch the dossier; show brief
  "Updated", return to idle.
- **error** → "Refresh failed"; surface `error_text` on hover; allow retry.

Copy is plain/professional per `CLAUDE.md` voice rules. No marketing words.
On mount, if a `running` job already exists for this gem, resume polling
(survives a page reload mid-refresh).

## 6. Edge cases

- **Double-click / concurrent users:** dedupe on the `running` row prevents a
  second spawn.
- **Hung / dead subprocess:** `--timeout` in Python + stale-guard in the GET
  endpoint both resolve a stuck `running` row to `error`.
- **On-demand vs. scheduled sweep on the same advertiser:** per-advertiser row
  writes; concurrent enrich is low-risk and accepted (no cross-advertiser
  contention). Re-score uses `only_ids`, so it won't touch other apps.
- **API restart mid-job:** subprocess is detached and updates the row itself,
  so it completes independently.

## 7. Testing

- **Python:** `enrich_one` marks `done` on success and `error` (with
  `error_text`) on a raised exception (mock `enrich_gem`); timeout path marks
  `error`; re-score is invoked with `only_ids=[advertiser_id]`.
- **Go:** POST inserts a row + spawns (mock the exec/command runner); dedupe
  returns the existing running row without spawning; GET returns the latest
  row and applies the stale-guard; auth rejects a missing/invalid token.
- **Web:** button state machine (idle→running→done→idle; error path); resume
  polling when a running job exists on mount.

## Files touched

| Layer | File | Change |
|---|---|---|
| DB | `migrations/<next>_enrich_jobs.sql` | new `public.enrich_jobs` table |
| Python | `src/agf/enrich_full/enrich_one.py` | new one-shot CLI |
| Shell | `scripts/enrich_one.sh` | env-sourcing wrapper |
| Go | `backend/controllers/enrich_controller.go` | new POST + GET |
| Go | `backend/routers/routers.go` | register routes |
| Web | `web/src/lib/api.ts` | `postEnrich`, `getEnrichStatus` |
| Web | detail header component | `RefreshDataButton` |

## Out of scope (YAGNI)

- New-URL / page ingestion (force-refresh existing apps only).
- Discover-table per-row refresh (detail page only for v1).
- A persistent on-demand daemon / job queue (subprocess model chosen).
