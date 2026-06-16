# On-demand "Refresh data" for a gem — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a detail-page button that force-refreshes one app on demand — re-pulling its ads, store metadata, media, and funnel, then re-scoring it — with a live status indicator while it runs.

**Architecture:** The web button calls Go `POST /api/enrich/:advertiser_id` (review-token gated). Go inserts a `public.enrich_jobs` row and spawns a detached one-shot Python subprocess (`scripts/enrich_one.sh` → `python -m agf.enrich_full.enrich_one`). The subprocess runs the existing `enrich_gem`, re-scores via `score_all(only_ids=...)`, refreshes the matview, and writes its own status to the job row. The web polls Go `GET /api/enrich/:advertiser_id` every ~3s and refetches the dossier on `done`.

**Tech Stack:** Postgres (raw SQL migration), Python (pytest, SQLAlchemy, asyncio, Playwright), Go (Gin + GORM-raw), Next.js (client component, no test framework).

**Design spec:** `docs/superpowers/specs/2026-06-16-on-demand-enrich-design.md`

---

## File structure

| File | Responsibility | New/Modify |
|---|---|---|
| `db/schema/128_enrich_jobs.sql` | `public.enrich_jobs` table + index | New |
| `src/agf/enrich_full/enrich_one.py` | one-shot CLI: status state machine, deps build, enrich + re-score | New |
| `tests/enrich_full/test_enrich_one.py` | unit tests for the job state machine | New |
| `scripts/enrich_one.sh` | env-sourcing wrapper Go execs | New |
| `backend/models/models.go` | `EnrichJobRow` + `EnrichJobOut` shapes | Modify |
| `backend/repositories/enrich_repo.go` | insert job, fetch latest job | New |
| `backend/controllers/enrich_controller.go` | POST trigger (dedupe + spawn), GET status (stale-guard) | New |
| `backend/routers/routers.go` | register the two routes | Modify |
| `backend/internal/integration/enrich_test.go` | integration tests for POST/GET | New |
| `web/src/lib/api.ts` | `postEnrich`, `getEnrichStatus`, optional bearer token | Modify |
| `web/src/components/detail/RefreshDataButton.tsx` | client button + polling state machine | New |
| `web/src/components/detail/GemHeader.tsx` | render the button; accept `advertiserId` prop | Modify |
| `web/src/components/detail/GemDetail.tsx` | pass `advertiserId` to `GemHeader` | Modify |

---

## Task 1: Migration — `public.enrich_jobs`

**Files:**
- Create: `db/schema/128_enrich_jobs.sql`

- [ ] **Step 1: Write the migration**

```sql
-- On-demand single-app enrichment jobs. One row per "Refresh data" click.
-- Go inserts a 'running' row and spawns a Python subprocess; the subprocess
-- updates the row to 'done'/'error'. The API reads the latest row per
-- advertiser for live status polling.
CREATE TABLE IF NOT EXISTS public.enrich_jobs (
    id            bigserial PRIMARY KEY,
    advertiser_id bigint      NOT NULL,
    status        text        NOT NULL DEFAULT 'running',  -- running | done | error
    error_text    text,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);

CREATE INDEX IF NOT EXISTS enrich_jobs_adv_started_idx
    ON public.enrich_jobs (advertiser_id, started_at DESC);
```

- [ ] **Step 2: Apply the migration**

Run: `python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; apply_migrations(get_engine())"`
Expected: no error; `128_enrich_jobs.sql` recorded in `public.schema_migrations`.

- [ ] **Step 3: Verify the table exists**

Run: `python -c "from agf.db.engine import begin; from sqlalchemy import text; \
import sys; \
[print(r[0]) for r in begin().__enter__().execute(text(\"select to_regclass('public.enrich_jobs')\"))]"`
Expected: prints `public.enrich_jobs`.

- [ ] **Step 4: Commit**

```bash
git add db/schema/128_enrich_jobs.sql
git commit -m "feat(db): enrich_jobs table for on-demand enrichment"
```

---

## Task 2: Python one-shot — `enrich_one.py`

The module is factored so the orchestrator `run_job` takes injectable
`enrich_call` and `score_call` functions, making the status state machine
testable without network/Playwright/scoring. `main()` wires the real
implementations and an alarm-based timeout.

**Files:**
- Create: `src/agf/enrich_full/enrich_one.py`
- Test: `tests/enrich_full/test_enrich_one.py`

- [ ] **Step 1: Write failing tests for the state machine**

```python
# tests/enrich_full/test_enrich_one.py
from sqlalchemy import text
from agf.db.engine import begin, get_engine
from agf.enrich_full import enrich_one


def _new_job(conn, advertiser_id=999001):
    row = conn.execute(text(
        "INSERT INTO public.enrich_jobs (advertiser_id) VALUES (:a) RETURNING id"
    ), {"a": advertiser_id}).first()
    return row[0]


def _status(conn, job_id):
    return conn.execute(text(
        "SELECT status, error_text, finished_at FROM public.enrich_jobs WHERE id=:i"
    ), {"i": job_id}).first()


def test_run_job_marks_done_on_success():
    engine = get_engine()
    calls = {}
    with begin(engine) as conn:
        job_id = _new_job(conn)

    def fake_enrich(engine_, advertiser_id, **kw):
        calls["enrich"] = advertiser_id

    def fake_score(engine_, advertiser_id, as_of):
        calls["score"] = (advertiser_id, as_of)

    enrich_one.run_job(engine, advertiser_id=999001, job_id=job_id,
                       enrich_call=fake_enrich, score_call=fake_score)

    with begin(engine) as conn:
        status, err, finished = _status(conn, job_id)
    assert status == "done"
    assert err is None
    assert finished is not None
    assert calls["enrich"] == 999001
    assert calls["score"][0] == 999001


def test_run_job_marks_error_on_exception():
    engine = get_engine()
    with begin(engine) as conn:
        job_id = _new_job(conn)

    def boom(engine_, advertiser_id, **kw):
        raise RuntimeError("render exploded")

    try:
        enrich_one.run_job(engine, advertiser_id=999001, job_id=job_id,
                           enrich_call=boom, score_call=lambda *a, **k: None)
    except RuntimeError:
        pass  # run_job re-raises after recording

    with begin(engine) as conn:
        status, err, finished = _status(conn, job_id)
    assert status == "error"
    assert "render exploded" in err
    assert finished is not None


def test_run_job_skips_score_when_enrich_fails():
    engine = get_engine()
    scored = []
    with begin(engine) as conn:
        job_id = _new_job(conn)

    def boom(engine_, advertiser_id, **kw):
        raise RuntimeError("nope")

    try:
        enrich_one.run_job(engine, advertiser_id=999001, job_id=job_id,
                           enrich_call=boom, score_call=lambda *a, **k: scored.append(a))
    except RuntimeError:
        pass
    assert scored == []  # score never runs if enrich raised
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/enrich_full/test_enrich_one.py -v`
Expected: FAIL — `AttributeError: module 'agf.enrich_full.enrich_one' has no attribute 'run_job'` (module does not exist yet).

- [ ] **Step 3: Implement `enrich_one.py`**

```python
"""On-demand single-app enrichment (one-shot CLI).

Go spawns this per "Refresh data" click:
    python -m agf.enrich_full.enrich_one --advertiser-id N --job-id M

It mirrors the worker's production deps (main.py production_deps_factory),
runs the existing enrich_gem, re-scores just this advertiser, refreshes the
matview, and records its own status on the public.enrich_jobs row so the API
can report live progress. enrich_gem itself is unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import date

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.enrich_full.run_enrich import EnrichDeps, enrich_gem
from agf.scoring.run_scoring import refresh_emerging_matview, score_all
from agf.workers.main import prefetch_creative_urls


def set_status(engine, job_id, status, *, error_text=None, finished=False):
    """Write a terminal/in-flight status onto the job row (own committed txn)."""
    with begin(engine) as conn:
        conn.execute(
            text(
                "UPDATE public.enrich_jobs "
                "SET status=:s, error_text=:e, "
                "    finished_at = CASE WHEN :fin THEN now() ELSE finished_at END "
                "WHERE id=:i"
            ),
            {"s": status, "e": error_text, "fin": finished, "i": job_id},
        )


def _build_deps(extractor, http_client, creative_urls):
    """EnrichDeps mirroring workers.main.production_deps_factory."""
    from agf.storage.s3 import get_s3, put_bytes

    try:
        import whois as _whois
        whois_fn = _whois.whois
    except Exception:
        whois_fn = None

    def fetch_text(url):
        return http_client.get(url).text

    def fetch_bytes(url):
        r = http_client.get(url)
        return r.content, r.headers.get("content-type", "")

    return EnrichDeps(
        extractor=extractor,
        itunes_client=http_client,
        fetch_text=fetch_text,
        fetch_bytes=fetch_bytes,
        put=put_bytes,
        s3=get_s3(),
        whois_fn=whois_fn,
        page=None,  # Playwright launches per-funnel on demand
        proxy=os.environ.get("AGF_FUNNEL_PROXY"),
        capture=True,
        creative_urls=creative_urls,
    )


def _default_enrich(engine, advertiser_id, *, extractor, http_client,
                    top_n=5, media_cap=120):
    """Production enrichment for one advertiser — same call shape as
    make_enrich_handler (asyncio.run around the async enrich_gem)."""
    # fbcdn signed URLs expire — prefetch fresh, OUTSIDE the enrich write txn.
    with begin(engine) as conn:
        creative_urls = prefetch_creative_urls(extractor, conn, [advertiser_id])
    deps = _build_deps(extractor, http_client, creative_urls.get(advertiser_id, {}))
    with begin(engine) as conn:
        asyncio.run(enrich_gem(conn, advertiser_id, top_n=top_n,
                               media_cap=media_cap, deps=deps))


def _default_score(engine, advertiser_id, as_of):
    """Re-score just this advertiser, then refresh the matview (mirrors
    run_scoring but scoped via only_ids)."""
    from agf.tracker.growth import compute_advertiser_growth
    with begin(engine) as conn:
        score_all(conn, as_of, only_ids=[advertiser_id])
        compute_advertiser_growth(conn)
    refresh_emerging_matview(engine)


def run_job(engine, *, advertiser_id, job_id, enrich_call, score_call,
            as_of=None):
    """Orchestrate one enrichment job and record its status. enrich_call and
    score_call are injected so the state machine is testable in isolation."""
    as_of = as_of or date.today()
    try:
        enrich_call(engine, advertiser_id)
        score_call(engine, advertiser_id, as_of)
        set_status(engine, job_id, "done", finished=True)
    except BaseException as e:  # record ANY failure (incl. timeout) on the row
        set_status(engine, job_id, "error", error_text=str(e)[:500], finished=True)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--advertiser-id", type=int, required=True)
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--media-cap", type=int, default=120)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args(argv)

    engine = get_engine()

    # Best-effort wall-clock guard: SIGALRM interrupts a wedged render so the
    # row lands in 'error' rather than 'running' forever. The API stale-guard
    # is the UI-facing backstop if the signal can't interrupt a blocked call.
    def _on_alarm(signum, frame):
        raise TimeoutError(f"enrich exceeded {args.timeout}s")

    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(args.timeout)

    import httpx
    from agf.extract.facade import Extractor
    extractor = Extractor()
    http_client = httpx.Client(timeout=20, follow_redirects=True)
    try:
        run_job(
            engine,
            advertiser_id=args.advertiser_id,
            job_id=args.job_id,
            enrich_call=lambda eng, aid: _default_enrich(
                eng, aid, extractor=extractor, http_client=http_client,
                top_n=args.top_n, media_cap=args.media_cap),
            score_call=_default_score,
        )
    except BaseException:
        sys.exit(1)
    finally:
        signal.alarm(0)
        http_client.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/enrich_full/test_enrich_one.py -v`
Expected: PASS (3 tests). Requires `AGF_TEST_DB_URL`/`AGF_DATABASE_URL` pointing at a DB with migration 128 applied.

- [ ] **Step 5: Smoke-test the CLI wiring (arg parsing only)**

Run: `python -m agf.enrich_full.enrich_one --help`
Expected: prints usage with `--advertiser-id`, `--job-id`, `--timeout`.

- [ ] **Step 6: Commit**

```bash
git add src/agf/enrich_full/enrich_one.py tests/enrich_full/test_enrich_one.py
git commit -m "feat(enrich): one-shot single-app enrichment CLI with job status"
```

---

## Task 3: Env-sourcing wrapper — `scripts/enrich_one.sh`

**Files:**
- Create: `scripts/enrich_one.sh`

- [ ] **Step 1: Write the wrapper**

```bash
#!/usr/bin/env bash
# Wrapper the Go API execs for on-demand enrichment. Sources ops/.env so the
# subprocess gets the DB URL, proxies, and S3/MinIO creds, then runs the
# one-shot CLI. Keeps the Go API ignorant of Python env internals.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Load environment (ops/.env is the canonical worker env on this host).
if [ -f ops/.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ops/.env
  set +a
fi

exec python -m agf.enrich_full.enrich_one "$@"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/enrich_one.sh`
Expected: no output.

- [ ] **Step 3: Verify it forwards args (DB write path)**

Insert a throwaway job row, then run the wrapper against a non-existent
advertiser and confirm it records `error` (proves env + Python + DB all wire):

Run:
```bash
JOB=$(python -c "from agf.db.engine import begin; from sqlalchemy import text; \
import sys; \
conn=begin().__enter__(); \
print(conn.execute(text(\"INSERT INTO public.enrich_jobs (advertiser_id) VALUES (-1) RETURNING id\")).first()[0])")
./scripts/enrich_one.sh --advertiser-id -1 --job-id "$JOB" || true
python -c "from agf.db.engine import begin; from sqlalchemy import text; \
print([r for r in begin().__enter__().execute(text('SELECT status FROM public.enrich_jobs WHERE id=$JOB'))])"
```
Expected: final line prints a row with status `error` or `done` (not `running`) — the subprocess ran to completion and recorded a terminal status.

- [ ] **Step 4: Commit**

```bash
git add scripts/enrich_one.sh
git commit -m "feat(ops): env-sourcing wrapper for on-demand enrichment"
```

---

## Task 4: Go models + repository

**Files:**
- Modify: `backend/models/models.go` (append near the other `*In`/`*Out` shapes)
- Create: `backend/repositories/enrich_repo.go`

- [ ] **Step 1: Add the Go shapes**

Append to `backend/models/models.go`:

```go
// EnrichJobRow is one public.enrich_jobs row (DB read shape).
type EnrichJobRow struct {
	ID         int      `gorm:"column:id"`
	Status     string   `gorm:"column:status"`
	ErrorText  *string  `gorm:"column:error_text"`
	StartedAt  APITime  `gorm:"column:started_at"`
	FinishedAt *APITime `gorm:"column:finished_at"`
}

// EnrichJobOut is the JSON the enrich endpoints return.
type EnrichJobOut struct {
	JobID      int      `json:"job_id"`
	Status     string   `json:"status"`
	ErrorText  *string  `json:"error_text"`
	StartedAt  APITime  `json:"started_at"`
	FinishedAt *APITime `json:"finished_at"`
}
```

(Reuse the existing `APITime` custom type used elsewhere in this file for
timestamps — confirm its name by grep `type APITime`; if the codebase calls it
something else, use that name consistently here.)

- [ ] **Step 2: Implement the repository**

```go
// backend/repositories/enrich_repo.go
package repositories

import (
	"context"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// InsertEnrichJob creates a 'running' job row and returns its id.
func InsertEnrichJob(ctx context.Context, g *gorm.DB, advertiserID int) (int, error) {
	var id int
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`INSERT INTO public.enrich_jobs (advertiser_id)
			 VALUES (?) RETURNING id`,
			advertiserID,
		).Scan(&id).Error
	})
	return id, err
}

// LatestEnrichJob returns the most recent job row for an advertiser, or nil.
func LatestEnrichJob(ctx context.Context, g *gorm.DB, advertiserID int) (*models.EnrichJobRow, error) {
	var rows []models.EnrichJobRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT id, status, error_text, started_at, finished_at
			 FROM public.enrich_jobs
			 WHERE advertiser_id = ?
			 ORDER BY started_at DESC
			 LIMIT 1`,
			advertiserID,
		).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}
```

(`db.WithWriteTx` / `db.WithReadTx` already exist in `backend/db/db.go`.)

- [ ] **Step 3: Build to verify it compiles**

Run: `cd backend && go build ./...`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/models/models.go backend/repositories/enrich_repo.go
git commit -m "feat(api): enrich_jobs models + repository"
```

---

## Task 5: Go controller — POST trigger + GET status

The spawn function is a package-level `var` so integration tests can override
it with a no-op (no real subprocess during tests).

**Files:**
- Create: `backend/controllers/enrich_controller.go`

- [ ] **Step 1: Implement the controller**

```go
// backend/controllers/enrich_controller.go
package controllers

import (
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// enrichTimeout bounds how long a 'running' row is trusted before the status
// endpoint reports it as failed (matches the Python --timeout default).
const enrichTimeout = 180 * time.Second

// spawnEnrich launches the detached one-shot enrichment subprocess. Overridden
// in tests. Default execs scripts/enrich_one.sh (AGF_ENRICH_SCRIPT to override).
var spawnEnrich = func(advertiserID, jobID int) error {
	script := os.Getenv("AGF_ENRICH_SCRIPT")
	if script == "" {
		script = "scripts/enrich_one.sh"
	}
	cmd := exec.Command(script,
		"--advertiser-id", strconv.Itoa(advertiserID),
		"--job-id", strconv.Itoa(jobID))
	if err := cmd.Start(); err != nil {
		return err
	}
	go func() { _ = cmd.Wait() }() // reap the child without blocking the request
	return nil
}

type EnrichController struct{}

func NewEnrichController() *EnrichController { return &EnrichController{} }

func toEnrichOut(row *models.EnrichJobRow) models.EnrichJobOut {
	out := models.EnrichJobOut{
		JobID:      row.ID,
		Status:     row.Status,
		ErrorText:  row.ErrorText,
		StartedAt:  row.StartedAt,
		FinishedAt: row.FinishedAt,
	}
	// Stale-guard: a 'running' row past the timeout means the subprocess died
	// without recording a terminal status. Report it as error for the UI.
	if out.Status == "running" && time.Since(row.StartedAt.Time) > enrichTimeout {
		out.Status = "error"
		msg := "enrichment timed out"
		out.ErrorText = &msg
	}
	return out
}

// Trigger handles POST /api/enrich/:advertiser_id.
func (c *EnrichController) Trigger(ctx *gin.Context) {
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()

	// Dedupe: a fresh 'running' job → return it, don't spawn a second.
	latest, err := repositories.LatestEnrichJob(rctx, db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not read enrich status.")
		return
	}
	if latest != nil && latest.Status == "running" &&
		time.Since(latest.StartedAt.Time) <= enrichTimeout {
		ctx.JSON(http.StatusAccepted, toEnrichOut(latest))
		return
	}

	jobID, err := repositories.InsertEnrichJob(rctx, db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not start enrichment.")
		return
	}
	if err := spawnEnrich(id, jobID); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not launch enrichment.")
		return
	}
	ctx.JSON(http.StatusAccepted, gin.H{"job_id": jobID, "status": "running"})
}

// Status handles GET /api/enrich/:advertiser_id.
func (c *EnrichController) Status(ctx *gin.Context) {
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	latest, err := repositories.LatestEnrichJob(rctx, db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not read enrich status.")
		return
	}
	if latest == nil {
		ctx.JSON(http.StatusOK, gin.H{"job_id": 0, "status": "none"})
		return
	}
	ctx.JSON(http.StatusOK, toEnrichOut(latest))
}
```

(`reqCtx`, `helpers.Detail`, and `db.DB()` are existing helpers — `reqCtx` is
defined alongside the other controllers, e.g. in `review_controller.go`. If
`APITime`'s underlying field is not `.Time`, adjust `row.StartedAt.Time`
to the correct accessor — grep the `APITime` definition to confirm.)

- [ ] **Step 2: Build to verify it compiles**

Run: `cd backend && go build ./...`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/controllers/enrich_controller.go
git commit -m "feat(api): on-demand enrich controller (trigger + status)"
```

---

## Task 6: Register routes

**Files:**
- Modify: `backend/routers/routers.go`

- [ ] **Step 1: Register the two routes**

In `routers.go`, near the existing `api.POST("/review/:advertiser_id/decision", ...)`
line, add:

```go
enrich := controllers.NewEnrichController()
api.POST("/enrich/:advertiser_id", middlewares.RequireToken(), enrich.Trigger)
api.GET("/enrich/:advertiser_id", enrich.Status)
```

(POST is gated by the same `middlewares.RequireToken()` the review route uses
— it reads `AGF_API_TOKEN` and no-ops when unset. GET is open: status polling
needs no auth.)

- [ ] **Step 2: Build to verify it compiles**

Run: `cd backend && go build ./...`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/routers/routers.go
git commit -m "feat(api): register /api/enrich routes"
```

---

## Task 7: Go integration tests

Mirrors `backend/internal/integration/integration_test.go`: skipped unless
`AGF_IT_DB_URL` is set. Overrides `spawnEnrich` so no real subprocess runs.

**Files:**
- Create: `backend/internal/integration/enrich_test.go`

- [ ] **Step 1: Write the integration tests**

```go
package integration

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/controllers"
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
)

func TestEnrichTriggerInsertsAndSpawns(t *testing.T) {
	if db.DB() == nil {
		t.Skip("no DB")
	}
	var spawned [][2]int
	controllers.SetSpawnEnrichForTest(func(advertiserID, jobID int) error {
		spawned = append(spawned, [2]int{advertiserID, jobID})
		return nil
	})
	defer controllers.ResetSpawnEnrichForTest()

	r := router()
	w, body := post(t, r, "/api/enrich/"+itoa(advID), "{}")
	require.Equal(t, http.StatusAccepted, w.Code)
	require.Contains(t, string(body["status"]), "running")
	require.Len(t, spawned, 1, "spawn called exactly once")
}

func TestEnrichTriggerDedupesRunningJob(t *testing.T) {
	if db.DB() == nil {
		t.Skip("no DB")
	}
	count := 0
	controllers.SetSpawnEnrichForTest(func(advertiserID, jobID int) error {
		count++
		return nil
	})
	defer controllers.ResetSpawnEnrichForTest()

	r := router()
	w1, _ := post(t, r, "/api/enrich/"+itoa(adv2ID), "{}")
	require.Equal(t, http.StatusAccepted, w1.Code)
	w2, _ := post(t, r, "/api/enrich/"+itoa(adv2ID), "{}")
	require.Equal(t, http.StatusAccepted, w2.Code)
	require.Equal(t, 1, count, "second call must not spawn a new subprocess")
}

func TestEnrichStatusReturnsLatest(t *testing.T) {
	if db.DB() == nil {
		t.Skip("no DB")
	}
	controllers.SetSpawnEnrichForTest(func(int, int) error { return nil })
	defer controllers.ResetSpawnEnrichForTest()

	r := router()
	_, _ = post(t, r, "/api/enrich/"+itoa(adv3ID), "{}")
	w, body := get(t, r, "/api/enrich/"+itoa(adv3ID))
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, string(body["status"]), "running")
}
```

(Use the existing integration helpers `router()`, `get()`, `post()`, and the
seeded `advID`/`adv2ID`/`adv3ID` from `integration_test.go`. `itoa` is
`strconv.Itoa`; if the suite already has a helper for path ids, use it. `post`
must accept a body string — confirm its signature in `integration_test.go` and
adapt the call if it differs.)

- [ ] **Step 2: Add test hooks to the controller**

Append to `backend/controllers/enrich_controller.go`:

```go
// Test hooks — let integration tests stub the subprocess spawn.
func SetSpawnEnrichForTest(fn func(advertiserID, jobID int) error) {
	spawnEnrich = fn
}

var defaultSpawnEnrich = spawnEnrich

func ResetSpawnEnrichForTest() { spawnEnrich = defaultSpawnEnrich }
```

- [ ] **Step 3: Run the integration tests**

Run: `cd backend && AGF_IT_DB_URL="$AGF_DATABASE_URL" go test ./internal/integration/ -run TestEnrich -v`
Expected: PASS (3 tests). Requires migration 128 applied to the integration DB.

- [ ] **Step 4: Commit**

```bash
git add backend/internal/integration/enrich_test.go backend/controllers/enrich_controller.go
git commit -m "test(api): integration tests for on-demand enrich"
```

---

## Task 8: Web API client

> **Before writing any web code:** read the relevant guide under
> `web/node_modules/next/dist/docs/` — `web/AGENTS.md` warns this Next.js
> version has breaking changes vs. training data.

**Files:**
- Modify: `web/src/lib/api.ts`

- [ ] **Step 1: Add optional bearer token to the `live` helper**

Near the top of `api.ts` (after `API_BASE`):

```ts
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN;
```

In the `live` helper's `headers`, include the token when present (this also
gives the existing write endpoints token parity):

```ts
    headers: {
      "Content-Type": "application/json",
      ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
      ...(init?.headers ?? {}),
    },
```

- [ ] **Step 2: Add the enrich client functions**

Append to `api.ts`:

```ts
export type EnrichStatus = {
  job_id: number;
  status: "none" | "running" | "done" | "error";
  error_text?: string | null;
};

export async function postEnrich(advertiserId: number): Promise<EnrichStatus> {
  return live<EnrichStatus>(`/enrich/${advertiserId}`, {
    method: "POST",
    body: "{}",
  });
}

export async function getEnrichStatus(advertiserId: number): Promise<EnrichStatus> {
  return live<EnrichStatus>(`/enrich/${advertiserId}`);
}
```

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/api.ts
git commit -m "feat(web): enrich API client + optional bearer token"
```

---

## Task 9: Web button + wiring

**Files:**
- Create: `web/src/components/detail/RefreshDataButton.tsx`
- Modify: `web/src/components/detail/GemHeader.tsx`
- Modify: `web/src/components/detail/GemDetail.tsx`

- [ ] **Step 1: Implement the button**

```tsx
// web/src/components/detail/RefreshDataButton.tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { postEnrich, getEnrichStatus, type EnrichStatus } from "@/lib/api";

type UiState = "idle" | "running" | "updated" | "error";

export function RefreshDataButton({ advertiserId }: { advertiserId: number }) {
  const router = useRouter();
  const [state, setState] = useState<UiState>("idle");
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, []);

  const apply = useCallback(
    (s: EnrichStatus) => {
      if (s.status === "running") {
        setState("running");
      } else if (s.status === "done") {
        stopPolling();
        setState("updated");
        router.refresh(); // refetch the dossier with fresh data
        setTimeout(() => setState("idle"), 2500);
      } else if (s.status === "error") {
        stopPolling();
        setError(s.error_text ?? "Enrichment failed");
        setState("error");
      }
    },
    [router, stopPolling],
  );

  const poll = useCallback(() => {
    if (timer.current) return;
    timer.current = setInterval(async () => {
      try {
        apply(await getEnrichStatus(advertiserId));
      } catch {
        /* transient — keep polling */
      }
    }, 3000);
  }, [advertiserId, apply]);

  // Resume polling if a job is already running when the page loads.
  useEffect(() => {
    let cancelled = false;
    getEnrichStatus(advertiserId)
      .then((s) => {
        if (!cancelled && s.status === "running") {
          setState("running");
          poll();
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [advertiserId, poll, stopPolling]);

  const onClick = useCallback(async () => {
    setError(null);
    setState("running");
    try {
      apply(await postEnrich(advertiserId));
      poll();
    } catch {
      setState("error");
      setError("Could not start refresh");
    }
  }, [advertiserId, apply, poll]);

  const label =
    state === "running"
      ? "Refreshing…"
      : state === "updated"
        ? "Updated"
        : state === "error"
          ? "Refresh failed"
          : "Refresh data";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={state === "running"}
      title={error ?? "Re-pull this app's ads, store data, and funnel"}
      className="t-sm"
      style={{
        border: "1.5px solid var(--ink)",
        background: "var(--surface-1)",
        padding: "5px 10px",
        cursor: state === "running" ? "default" : "pointer",
        opacity: state === "running" ? 0.6 : 1,
      }}
    >
      {label}
    </button>
  );
}
```

- [ ] **Step 2: Pass `advertiserId` into `GemHeader` and render the button**

In `GemDetail.tsx`, find where `<GemHeader gem={...} />` is rendered and add the
prop: `<GemHeader gem={dossier} advertiserId={advertiserId} />` (use whatever
the dossier variable is named in that file).

In `GemHeader.tsx`:
- Change the signature to:
  ```tsx
  export function GemHeader({ gem, advertiserId }: { gem: GemDossier; advertiserId: number }) {
  ```
- Import the button at the top:
  ```tsx
  import { RefreshDataButton } from "./RefreshDataButton";
  ```
- In the top-right action column (the `<div>` at ~line 83 holding `ScoreBadge`),
  add the button below the trend row:
  ```tsx
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
          <ScoreBadge score={gem.gem_score} variant="detail" />
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Trend glyph={g.glyph} label={trendLabel} tone={g.tone} />
            <InfoDot text={GEMSCORE_GLOSSARY} label="what GemScore means" />
          </div>
          <RefreshDataButton advertiserId={advertiserId} />
        </div>
  ```

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Manual verification (no web test framework)**

1. Apply migration 128, ensure the Go API and worker env are running.
2. Start the web app, open a gem detail page.
3. Click **Refresh data** → label becomes "Refreshing…", button disabled.
4. Watch `public.enrich_jobs`: a row appears `running` → `done`.
5. On `done`, the page refetches and the label flashes "Updated" then returns
   to idle. Confirm fresh ads/funnel/score visible.
6. Trigger against an advertiser that errors → label shows "Refresh failed",
   hover shows the error text.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/detail/RefreshDataButton.tsx \
        web/src/components/detail/GemHeader.tsx \
        web/src/components/detail/GemDetail.tsx
git commit -m "feat(web): Refresh data button on gem detail with live status"
```

---

## Task 10: Full verification

- [ ] **Step 1: Python suite**

Run: `pytest tests/enrich_full/test_enrich_one.py tests/test_enrich.py tests/test_score.py -v`
Expected: PASS.

- [ ] **Step 2: Go build + vet + tests**

Run: `cd backend && go build ./... && go vet ./... && AGF_IT_DB_URL="$AGF_DATABASE_URL" go test ./... `
Expected: PASS (integration enrich tests included).

- [ ] **Step 3: Web typecheck + lint**

Run: `cd web && npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 4: End-to-end manual run** — perform Task 9 Step 4 against the live stack and confirm the full loop.

---

## Notes / deferred (YAGNI)

- New-URL ingestion, Discover-row refresh, and a persistent daemon are out of
  scope (see spec).
- If `AGF_API_TOKEN` is set on the API, set `NEXT_PUBLIC_API_TOKEN` for the web
  build so POSTs carry the bearer token.
- The Go API process must run with a working directory at the repo root (or
  `AGF_ENRICH_SCRIPT` set to an absolute path) so `scripts/enrich_one.sh`
  resolves.
