# Ad re-scrape in enrich + saved ads — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dossier's "Refresh data" button re-scrapes the gem's Meta Ad Library listing before enriching, and individual ads can be saved with a heart and browsed from /saved.

**Architecture:** Feature A adds a fail-soft scrape phase inside the existing enrich_one CLI (no new endpoints or daemons). Feature B clones the saved_gems stack one layer down: migration 164 `frontend.saved_ads`, mirror Go repo/controller/routes, a flat savedAds store on the web, a heart on dossier ad cards, and a pinned "Ads" tile on /saved.

**Tech Stack:** Python (SQLAlchemy, pytest, asyncio), Go (gin, gorm, testify), Next.js/React (useSyncExternalStore), Postgres.

**Spec:** `docs/superpowers/specs/2026-08-05-ad-refresh-saved-ads-design.md`

## Global Constraints

- Tests: Python full suite must end 0 failed with `AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it`; Go suite runs with `AGF_IT_DB_URL` set to the same DB (skips cleanly unset). ONE test consumer per shared test DB at a time; the zero-failed gate runs last, solo.
- Verification commands run BARE — never piped through tail/head/grep in the same call as the verdict.
- Migrations: next free number is **164**. Migrations run via `exec_driver_sql` with no params — never a bare `%` (use `make_interval`); guard test `tests/db/test_migrations_placeholder_safe.py`.
- Product copy: plain, clear, professional. Exact strings from the spec: tile label `Ads`; empty state `No saved ads yet. Save one with the heart on a gem's ads.`; aria-labels `Save ad` / `Remove from saved`.
- Product nouns intact: **Field Desk**, **GemScore**, **cohort**.
- Timeout ledger (cross-referenced constants, do not drift): Go `enrichTimeout` 540s ≥ queue pickup 120s + enrich `--timeout` 300s. The new scrape phase must stay inside the 300s budget (its own cap, default 120s).
- All commits on `main`, pathspec-limited (`git commit -- <paths>`), one logical unit per commit. Another session may share the tree: re-read before every edit, stage explicit paths only.
- Frontend has NO unit test runner — its gates are `npm run lint` and `npm run build` (run from `web/`).

---

### Task 1: Migration 164 + Go saved-ads API (model, repo, controller, routes)

**Files:**
- Create: `db/schema/164_saved_ads.sql`
- Modify: `backend/models/models.go` (after `SavedGemRemoveIn`, ~line 533)
- Create: `backend/repositories/saved_ads_repo.go`
- Create: `backend/controllers/saved_ads_controller.go`
- Modify: `backend/routers/routers.go` (routes after the saved-gems block ~line 66; controller instantiation near the other `New*Controller()` calls earlier in the file — find `saved :=` and mirror)
- Test: `backend/repositories/saved_ads_repo_test.go`

**Interfaces:**
- Consumes: existing `db.WithReadTx`/`db.WithWriteTx`, `middlewares.RequireToken()`, `helpers.Detail`, test-side `ratingsDB` + `must` from `backend/repositories/ratings_repo_test.go` (the package's one TestMain; tests skip when `AGF_IT_DB_URL` unset).
- Produces: wire contract used by Task 3:
  - `GET  /api/saved-ads?user_email=…` → `[]SavedAdRow` (JSON keys below), newest first
  - `POST /api/saved-ads/:external_ad_id` body `SavedAdIn` → `{"status":"ok","external_ad_id":…}`
  - `POST /api/saved-ads/:external_ad_id/remove` body `{"user_email":…}` → `{"status":"ok"}`

- [ ] **Step 1: Write the migration**

```sql
-- 164_saved_ads.sql — per-user saved ads (the ad-level sibling of saved_gems).
-- Snapshot columns are denormalized so a saved ad still renders if the
-- underlying public.ads row is pruned or re-imported; media_s3_key points at
-- our stored media (Meta CDN URLs expire). No FKs for the same reason.
CREATE TABLE IF NOT EXISTS frontend.saved_ads (
    user_email      TEXT NOT NULL,
    external_ad_id  TEXT NOT NULL,
    advertiser_id   BIGINT,
    page_name       TEXT,
    app_name        TEXT,
    title           TEXT,
    body_text       TEXT,
    media_type      TEXT,
    media_s3_key    TEXT,
    link_url        TEXT,
    cta_text        TEXT,
    ad_started_on   DATE,
    ad_active       BOOLEAN,
    saved_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_email, external_ad_id)
);

CREATE INDEX IF NOT EXISTS saved_ads_user_saved_idx
    ON frontend.saved_ads (user_email, saved_at DESC);
```

- [ ] **Step 2: Apply to the test DB and verify**

Run (bare): `AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; import os; print(apply_migrations(get_engine(os.environ['AGF_TEST_DB_URL'])))"`
Expected: list containing `164_saved_ads.sql`, no error.

- [ ] **Step 3: Add the Go models**

In `backend/models/models.go`, directly after `SavedGemRemoveIn`:

```go
// SavedAdIn is the upsert body for POST /api/saved-ads/:external_ad_id. The
// client sends a render snapshot (saved_gems precedent); on a re-save the
// original snapshot and saved_at win — there are no mutable annotations.
type SavedAdIn struct {
	UserEmail    string  `json:"user_email" binding:"required"`
	AdvertiserID *int64  `json:"advertiser_id"`
	PageName     *string `json:"page_name"`
	AppName      *string `json:"app_name"`
	Title        *string `json:"title"`
	BodyText     *string `json:"body_text"`
	MediaType    *string `json:"media_type"`
	MediaS3Key   *string `json:"media_s3_key"`
	LinkURL      *string `json:"link_url"`
	CtaText      *string `json:"cta_text"`
	AdStartedOn  *string `json:"ad_started_on" binding:"omitempty,datetime=2006-01-02"`
	AdActive     *bool   `json:"ad_active"`
}

// SavedAdRemoveIn carries the owner for an unsave (mirrors SavedGemRemoveIn).
type SavedAdRemoveIn struct {
	UserEmail string `json:"user_email" binding:"required"`
}

// SavedAdRow is one saved ad as served by GET /api/saved-ads.
type SavedAdRow struct {
	UserEmail    string    `json:"user_email"`
	ExternalAdID string    `json:"external_ad_id"`
	AdvertiserID *int64    `json:"advertiser_id"`
	PageName     *string   `json:"page_name"`
	AppName      *string   `json:"app_name"`
	Title        *string   `json:"title"`
	BodyText     *string   `json:"body_text"`
	MediaType    *string   `json:"media_type"`
	MediaS3Key   *string   `json:"media_s3_key"`
	LinkURL      *string   `json:"link_url"`
	CtaText      *string   `json:"cta_text"`
	AdStartedOn  *string   `json:"ad_started_on"`
	AdActive     *bool     `json:"ad_active"`
	SavedAt      time.Time `json:"saved_at"`
}
```

(`time` is already imported in models.go.)

- [ ] **Step 4: Write the failing repo test**

`backend/repositories/saved_ads_repo_test.go` (package `repositories_test`; `ratingsDB`/`must` come from ratings_repo_test.go):

```go
// Package repositories_test exercises saved_ads_repo.go against a real
// Postgres database. Skipped unless AGF_IT_DB_URL is set.
package repositories_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

func seedSavedAds(t *testing.T) {
	t.Helper()
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	must(ratingsDB.Exec(`DELETE FROM frontend.saved_ads WHERE user_email LIKE 'sa-test-%@t.local'`).Error)
}

func strp(s string) *string { return &s }

func TestSavedAds_UpsertListRoundtrip(t *testing.T) {
	seedSavedAds(t)
	ctx := context.Background()

	in := models.SavedAdIn{
		UserEmail: "sa-test-a@t.local",
		PageName:  strp("Femrun"),
		Title:     strp("Run challenge"),
		MediaType: strp("image"),
	}
	require.NoError(t, repositories.UpsertSavedAd(ctx, ratingsDB, in, "111111"))
	require.NoError(t, repositories.UpsertSavedAd(ctx, ratingsDB, models.SavedAdIn{
		UserEmail: "sa-test-a@t.local", MediaType: strp("video"),
	}, "222222"))

	rows, err := repositories.ListSavedAds(ctx, ratingsDB, "sa-test-a@t.local")
	require.NoError(t, err)
	require.Len(t, rows, 2)
	// Newest save first.
	require.Equal(t, "222222", rows[0].ExternalAdID)
	require.Equal(t, "111111", rows[1].ExternalAdID)
	require.Equal(t, "Femrun", *rows[1].PageName)
}

func TestSavedAds_ResaveKeepsOriginalSnapshot(t *testing.T) {
	seedSavedAds(t)
	ctx := context.Background()

	require.NoError(t, repositories.UpsertSavedAd(ctx, ratingsDB, models.SavedAdIn{
		UserEmail: "sa-test-b@t.local", Title: strp("original"),
	}, "333333"))
	require.NoError(t, repositories.UpsertSavedAd(ctx, ratingsDB, models.SavedAdIn{
		UserEmail: "sa-test-b@t.local", Title: strp("clobbered?"),
	}, "333333"))

	rows, err := repositories.ListSavedAds(ctx, ratingsDB, "sa-test-b@t.local")
	require.NoError(t, err)
	require.Len(t, rows, 1)
	require.Equal(t, "original", *rows[0].Title)
}

func TestSavedAds_DeleteAndUserScoping(t *testing.T) {
	seedSavedAds(t)
	ctx := context.Background()

	require.NoError(t, repositories.UpsertSavedAd(ctx, ratingsDB, models.SavedAdIn{UserEmail: "sa-test-c@t.local"}, "444444"))
	require.NoError(t, repositories.UpsertSavedAd(ctx, ratingsDB, models.SavedAdIn{UserEmail: "sa-test-d@t.local"}, "444444"))

	require.NoError(t, repositories.DeleteSavedAd(ctx, ratingsDB, "sa-test-c@t.local", "444444"))

	rowsC, err := repositories.ListSavedAds(ctx, ratingsDB, "sa-test-c@t.local")
	require.NoError(t, err)
	require.Len(t, rowsC, 0)
	rowsD, err := repositories.ListSavedAds(ctx, ratingsDB, "sa-test-d@t.local")
	require.NoError(t, err)
	require.Len(t, rowsD, 1)
}
```

- [ ] **Step 5: Run the test to verify it fails**

Run (bare, from `backend/`): `AGF_IT_DB_URL=postgres://agf:agf@localhost:55433/agf_it go test ./repositories/ -run TestSavedAds -v`
Expected: compile FAIL — `undefined: repositories.UpsertSavedAd` etc.
(Check the exact `AGF_IT_DB_URL` scheme ratings_repo_test.go expects and reuse it verbatim.)

- [ ] **Step 6: Write the repo**

`backend/repositories/saved_ads_repo.go`:

```go
package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// ListSavedAds returns a user's saved ads, newest save first. ad_started_on is
// served as YYYY-MM-DD text so the wire shape matches the save body.
func ListSavedAds(ctx context.Context, g *gorm.DB, userEmail string) ([]models.SavedAdRow, error) {
	rows := make([]models.SavedAdRow, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT user_email, external_ad_id, advertiser_id, page_name, app_name,
			        title, body_text, media_type, media_s3_key, link_url, cta_text,
			        to_char(ad_started_on, 'YYYY-MM-DD') AS ad_started_on,
			        ad_active, saved_at
			 FROM frontend.saved_ads
			 WHERE user_email = ?
			 ORDER BY saved_at DESC, external_ad_id`,
			userEmail,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.SavedAdRow, 0)
	}
	return rows, err
}

// UpsertSavedAd inserts a saved ad with its render snapshot. A re-save is a
// no-op: the snapshot and saved_at keep their original values (there are no
// mutable annotations on an ad, unlike saved gems).
func UpsertSavedAd(ctx context.Context, g *gorm.DB, in models.SavedAdIn, externalAdID string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(
			`INSERT INTO frontend.saved_ads
			   (user_email, external_ad_id, advertiser_id, page_name, app_name,
			    title, body_text, media_type, media_s3_key, link_url, cta_text,
			    ad_started_on, ad_active, saved_at)
			 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, clock_timestamp())
			 ON CONFLICT (user_email, external_ad_id) DO NOTHING`,
			in.UserEmail, externalAdID, in.AdvertiserID, in.PageName, in.AppName,
			in.Title, in.BodyText, in.MediaType, in.MediaS3Key, in.LinkURL,
			in.CtaText, in.AdStartedOn, in.AdActive,
		).Error
	})
}

// DeleteSavedAd removes one saved ad for a user.
func DeleteSavedAd(ctx context.Context, g *gorm.DB, userEmail, externalAdID string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(
			`DELETE FROM frontend.saved_ads WHERE user_email = ? AND external_ad_id = ?`,
			userEmail, externalAdID,
		).Error
	})
}
```

- [ ] **Step 7: Run the repo tests to verify they pass**

Run (bare, from `backend/`): `AGF_IT_DB_URL=… go test ./repositories/ -run TestSavedAds -v`
Expected: 3 PASS.

- [ ] **Step 8: Write the controller**

`backend/controllers/saved_ads_controller.go` (mirror saved_gems_controller.go exactly; `reqCtx` is package-local there):

```go
package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// SavedAdsController serves a user's saved ads (list + save + remove) — the
// ad-level sibling of SavedGemsController. Writes are token-guarded the same way.
type SavedAdsController struct{}

func NewSavedAdsController() *SavedAdsController { return &SavedAdsController{} }

// externalAdID validates the :external_ad_id path param — a Meta ad archive id
// (digits in practice; bounded to keep junk out of the PK).
func externalAdID(ctx *gin.Context) (string, bool) {
	id := ctx.Param("external_ad_id")
	if id == "" || len(id) > 64 {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "external_ad_id is required.")
		return "", false
	}
	return id, true
}

func (c *SavedAdsController) List(ctx *gin.Context) {
	email, ok := ctx.GetQuery("user_email")
	if !ok || email == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "user_email is required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ListSavedAds(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load saved ads.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}

func (c *SavedAdsController) Save(ctx *gin.Context) {
	id, ok := externalAdID(ctx)
	if !ok {
		return
	}
	var body models.SavedAdIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.UpsertSavedAd(rctx, db.DB(), body, id); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save ad.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "external_ad_id": id})
}

func (c *SavedAdsController) Remove(ctx *gin.Context) {
	id, ok := externalAdID(ctx)
	if !ok {
		return
	}
	var body models.SavedAdRemoveIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.DeleteSavedAd(rctx, db.DB(), body.UserEmail, id); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not remove saved ad.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok"})
}
```

- [ ] **Step 9: Wire the routes**

In `backend/routers/routers.go`: instantiate `savedAds := controllers.NewSavedAdsController()` next to the existing `saved := …` line, and add after the saved-gems route block:

```go
		// Saved ads (the ad-level sibling of saved gems). Same auth shape.
		api.GET("/saved-ads", savedAds.List)
		api.POST("/saved-ads/:external_ad_id", middlewares.RequireToken(), savedAds.Save)
		api.POST("/saved-ads/:external_ad_id/remove", middlewares.RequireToken(), savedAds.Remove)
```

- [ ] **Step 10: Full Go gate (bare)**

Run from `backend/`: `AGF_IT_DB_URL=… go test ./...`
Expected: 0 failed. Then `go build ./...` — clean.

- [ ] **Step 11: Commit**

```bash
git add db/schema/164_saved_ads.sql backend/models/models.go backend/repositories/saved_ads_repo.go backend/repositories/saved_ads_repo_test.go backend/controllers/saved_ads_controller.go backend/routers/routers.go
git commit -m "feat(api): saved ads — migration 164 + list/save/remove endpoints" -- db/schema/164_saved_ads.sql backend/models/models.go backend/repositories/saved_ads_repo.go backend/repositories/saved_ads_repo_test.go backend/controllers/saved_ads_controller.go backend/routers/routers.go
```

---

### Task 2: Fresh-ads scrape phase in enrich_one (Python)

**Files:**
- Modify: `src/agf/enrich_full/enrich_one.py`
- Test: `tests/enrich_full/test_enrich_one_ads_refresh.py` (new)

**Interfaces:**
- Consumes: `refresh_one_advertiser(engine, extractor, advertiser_id, page_id, *, hist_pages=12, weeks=16, flip_vanished=False) -> dict` from `agf.discovery.keeper_history` (raises `ExtractError` on listing failure); `begin` from `agf.db.engine`; test fakes `FakeExtractor` (`listing_ads: dict page_id -> [AdRecord]`) and `make_ad` from `tests.discovery.conftest`; fixtures `pg_conn` / `_migrated_engine` from root `conftest.py`.
- Produces: `_refresh_ads_listing(engine, advertiser_id, *, extractor, pages=None, timeout=None) -> dict | None` in `enrich_one.py`, called first inside `_default_enrich`.

- [ ] **Step 1: Write the failing tests**

`tests/enrich_full/test_enrich_one_ads_refresh.py`:

```python
"""Fresh-ads phase of enrich_one ("Refresh data" now re-scrapes the listing):
page gate, upsert + vanish flip, fail-soft on extract error and timeout, and
the _default_enrich ordering. ENRICHADS* sentinels; engine tests COMMIT
(refresh_one_advertiser owns its txn), so each test cleans up first."""
import asyncio
from types import SimpleNamespace

from sqlalchemy import text

from agf.db import repo
from agf.db.engine import begin
from agf.enrich_full import enrich_one
from agf.enrich_full.enrich_one import _refresh_ads_listing
from agf.extract.errors import ExtractError
from tests.discovery.conftest import FakeExtractor, make_ad


def _cleanup(engine):
    with begin(engine) as conn:
        ids = conn.execute(text(
            "SELECT id FROM public.advertisers WHERE page_id LIKE 'ENRICHADS%'"
        )).scalars().all()
        if not ids:
            return
        ad_ids = conn.execute(text(
            "SELECT id FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids}
        ).scalars().all()
        if ad_ids:
            conn.execute(text("DELETE FROM public.creatives WHERE ad_id = ANY(:i)"), {"i": ad_ids})
        conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM adtech.app_advertiser_metrics WHERE advertiser_id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})


def _seed_advertiser(engine, page_id):
    with begin(engine) as conn:
        return repo.upsert_advertiser(
            conn, SimpleNamespace(page_id=page_id, page_name="EnrichAds", page_like_count=10))


def test_refresh_upserts_and_flips_vanished(_migrated_engine):
    _cleanup(_migrated_engine)
    adv = _seed_advertiser(_migrated_engine, "ENRICHADSA")

    ext = FakeExtractor(listing_ads={"ENRICHADSA": [
        make_ad("ea-1", "ENRICHADSA", "EnrichAds", "https://x/app"),
        make_ad("ea-2", "ENRICHADSA", "EnrichAds", "https://x/app"),
    ]})
    got = _refresh_ads_listing(_migrated_engine, adv, extractor=ext)
    assert got is not None and got["ads_stored"] == 2

    with begin(_migrated_engine) as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM public.ads WHERE advertiser_id=:a"), {"a": adv}).scalar()
    assert n == 2

    # Second pull: ea-2 vanished from the listing -> flipped inactive.
    ext2 = FakeExtractor(listing_ads={"ENRICHADSA": [
        make_ad("ea-1", "ENRICHADSA", "EnrichAds", "https://x/app"),
    ]})
    got2 = _refresh_ads_listing(_migrated_engine, adv, extractor=ext2)
    assert got2 is not None and got2["flipped"] == 1
    with begin(_migrated_engine) as conn:
        active = conn.execute(text(
            "SELECT external_ad_id FROM public.ads WHERE advertiser_id=:a AND is_active"),
            {"a": adv}).scalars().all()
    assert active == ["ea-1"]
    _cleanup(_migrated_engine)


def test_missing_advertiser_skips_without_calling_extractor(_migrated_engine):
    class Boom:
        async def search_listing(self, **kw):
            raise AssertionError("must not be called")
    assert _refresh_ads_listing(_migrated_engine, -1, extractor=Boom()) is None


def test_extract_error_is_fail_soft(_migrated_engine):
    _cleanup(_migrated_engine)
    adv = _seed_advertiser(_migrated_engine, "ENRICHADSB")

    class Failing:
        async def search_listing(self, **kw):
            raise ExtractError("meta said no")
    assert _refresh_ads_listing(_migrated_engine, adv, extractor=Failing()) is None
    _cleanup(_migrated_engine)


def test_slow_listing_hits_budget_and_fails_soft(_migrated_engine):
    _cleanup(_migrated_engine)
    adv = _seed_advertiser(_migrated_engine, "ENRICHADSC")

    class Slow:
        async def search_listing(self, **kw):
            await asyncio.sleep(1)
            return [], False
    assert _refresh_ads_listing(_migrated_engine, adv, extractor=Slow(), timeout=0.05) is None
    _cleanup(_migrated_engine)


def test_default_enrich_runs_listing_phase_first(monkeypatch, _migrated_engine):
    calls = []
    monkeypatch.setattr(enrich_one, "_refresh_ads_listing",
                        lambda *a, **k: calls.append("listing"))
    monkeypatch.setattr(enrich_one, "prefetch_creative_urls",
                        lambda *a, **k: (calls.append("prefetch"), {})[1])
    monkeypatch.setattr(enrich_one, "_build_deps", lambda *a, **k: None)

    async def fake_enrich_gem(conn, aid, **kw):
        calls.append("enrich")
    monkeypatch.setattr(enrich_one, "enrich_gem", fake_enrich_gem)

    enrich_one._default_enrich(_migrated_engine, 1, extractor=None, http_client=None)
    assert calls == ["listing", "prefetch", "enrich"]
```

- [ ] **Step 2: Run to verify failure**

Run (bare): `AGF_TEST_DB_URL=… python -m pytest tests/enrich_full/test_enrich_one_ads_refresh.py -v`
Expected: ImportError / AttributeError — `_refresh_ads_listing` not defined.

- [ ] **Step 3: Implement the phase**

In `src/agf/enrich_full/enrich_one.py`, add imports `from sqlalchemy import text` (already present) and `from agf.discovery.keeper_history import refresh_one_advertiser`, then above `_default_enrich`:

```python
def _refresh_ads_listing(engine, advertiser_id, *, extractor,
                         pages=None, timeout=None):
    """Fresh-listing phase for "Refresh data": re-scrape the advertiser's Ad
    Library listing so new ads appear and vanished ones flip inactive, before
    enrich_gem reads public.ads. Fail-soft by design — a Meta hiccup must never
    fail the enrich job (it could not before this phase existed). The wait_for
    budget keeps the phase inside enrich_one's 300s wall clock; EnrichDeadline
    is a BaseException, so the broad `except Exception` cannot swallow the
    process-wide deadline."""
    pages = pages if pages is not None else int(os.environ.get("AGF_ENRICH_ADS_PAGES", "12"))
    timeout = timeout if timeout is not None else float(os.environ.get("AGF_ENRICH_ADS_TIMEOUT", "120"))
    with begin(engine) as conn:
        page_id = conn.execute(
            text("SELECT page_id FROM public.advertisers WHERE id=:a"),
            {"a": advertiser_id}).scalar()
    if not page_id:
        return None
    try:
        return asyncio.run(asyncio.wait_for(
            refresh_one_advertiser(engine, extractor, advertiser_id, page_id,
                                   hist_pages=pages, flip_vanished=True),
            timeout))
    except Exception as e:
        print(f"[enrich_one] ads listing refresh skipped: {type(e).__name__}: {e}",
              file=sys.stderr)
        return None
```

And make it the first statement of `_default_enrich`:

```python
def _default_enrich(engine, advertiser_id, *, extractor, http_client,
                    top_n=5, media_cap=120):
    """Production enrichment for one advertiser — same call shape as
    make_enrich_handler (asyncio.run around the async enrich_gem)."""
    _refresh_ads_listing(engine, advertiser_id, extractor=extractor)
    # fbcdn signed URLs expire — prefetch fresh, OUTSIDE the enrich write txn.
    with begin(engine) as conn:
        creative_urls = prefetch_creative_urls(extractor, conn, [advertiser_id])
    ...
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run (bare): `AGF_TEST_DB_URL=… python -m pytest tests/enrich_full/test_enrich_one_ads_refresh.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agf/enrich_full/enrich_one.py tests/enrich_full/test_enrich_one_ads_refresh.py
git commit -m "feat(enrich): Refresh data re-scrapes the ad listing before enriching" -- src/agf/enrich_full/enrich_one.py tests/enrich_full/test_enrich_one_ads_refresh.py
```

---

### Task 3: Web plumbing — contracts, api client, savedAds store, proxy allowlist

**Files:**
- Modify: `web/src/types/contracts.ts` (Creative + new Api types)
- Modify: `web/src/lib/adapters.ts` (`adaptCreative` gains `s3_key`)
- Modify: `web/src/lib/api.ts` (three client calls)
- Create: `web/src/lib/savedAds.ts`
- Create: `web/src/lib/useSavedAds.ts`
- Modify: `web/src/components/SavedUserSync.tsx` (also set savedAds user)
- Modify: `web/src/app/backend/[...path]/route.ts` (allowlist)

**Interfaces:**
- Consumes: Task 1's wire contract; existing `live<T>()` helper in api.ts (see `getSavedGems` at api.ts:183 for the idiom).
- Produces (used by Tasks 4-5):
  - `ApiSavedAd` (mirrors `SavedAdRow` JSON), `ApiSavedAdIn` (mirrors `SavedAdIn`)
  - `Creative.s3_key?: string`
  - Store: `saveAd(externalAdId: string, snapshot: Omit<ApiSavedAdIn, "user_email">): void`, `unsaveAd(externalAdId: string): void`, `setSavedAdsUser(email: string | null): void`
  - Hooks: `useSavedAdsList(): ApiSavedAd[]`, `useIsAdSaved(externalAdId: string | undefined): boolean`

- [ ] **Step 1: Types**

In `web/src/types/contracts.ts`, add `s3_key?: string;` to the `Creative` interface (it exists on `ApiCreativeInfo` already — verify), and add:

```ts
/** One saved ad as served by GET /api/saved-ads (frontend.saved_ads row). */
export interface ApiSavedAd {
  user_email: string;
  external_ad_id: string;
  advertiser_id?: number | null;
  page_name?: string | null;
  app_name?: string | null;
  title?: string | null;
  body_text?: string | null;
  media_type?: string | null;
  media_s3_key?: string | null;
  link_url?: string | null;
  cta_text?: string | null;
  ad_started_on?: string | null;
  ad_active?: boolean | null;
  saved_at: string;
}

/** Upsert body for POST /api/saved-ads/:external_ad_id. */
export type ApiSavedAdIn = Omit<ApiSavedAd, "external_ad_id" | "saved_at">;
```

In `web/src/lib/adapters.ts` `adaptCreative`, add `s3_key: c.s3_key ?? undefined,` alongside the existing fields.

- [ ] **Step 2: API client**

In `web/src/lib/api.ts`, next to the saved-gems calls:

```ts
export async function getSavedAds(userEmail: string): Promise<ApiSavedAd[]> {
  return live<ApiSavedAd[]>(`/saved-ads?user_email=${encodeURIComponent(userEmail)}`);
}

export async function upsertSavedAd(externalAdId: string, body: ApiSavedAdIn): Promise<void> {
  await live(`/saved-ads/${encodeURIComponent(externalAdId)}`, { method: "POST", body: JSON.stringify(body) });
}

export async function removeSavedAd(externalAdId: string, userEmail: string): Promise<void> {
  await live(`/saved-ads/${encodeURIComponent(externalAdId)}/remove`, {
    method: "POST",
    body: JSON.stringify({ user_email: userEmail }),
  });
}
```

(Import `ApiSavedAd`, `ApiSavedAdIn` in the existing type import block. Match `live()`'s real call signature — copy the saved-gems idiom verbatim.)

- [ ] **Step 3: Store**

`web/src/lib/savedAds.ts` — flat, optimistic, module-level (deliberately simpler than saved.ts: no folders, no likes, no temp ids):

```ts
"use client";

/**
 * Saved ads store — the ad-level sibling of saved.ts, flat by design (no
 * folders, no ratings). Optimistic writes: the list updates immediately and
 * reverts if the API call fails.
 */
import { getSavedAds, removeSavedAd, upsertSavedAd } from "@/lib/api";
import type { ApiSavedAd, ApiSavedAdIn } from "@/types/contracts";

type Listener = () => void;

let userEmail: string | null = null;
let rows: ApiSavedAd[] = [];
let hydrated = false;
const listeners = new Set<Listener>();

const EMPTY: ApiSavedAd[] = [];

function emit() {
  for (const l of listeners) l();
}

export function subscribeSavedAds(l: Listener): () => void {
  listeners.add(l);
  if (!hydrated) void hydrate();
  return () => listeners.delete(l);
}

export function getSavedAdsSnapshot(): ApiSavedAd[] {
  return rows;
}

export function getSavedAdsServerSnapshot(): ApiSavedAd[] {
  return EMPTY;
}

async function hydrate() {
  if (!userEmail) return; // wait until the session user is known
  hydrated = true;
  try {
    rows = await getSavedAds(userEmail);
    emit();
  } catch {
    hydrated = false; // retry on the next subscribe
  }
}

/** Set the current user (saved ads are keyed by email) and refetch. */
export function setSavedAdsUser(email: string | null) {
  if (email === userEmail) return;
  userEmail = email;
  rows = [];
  hydrated = false;
  emit();
  if (email) void hydrate();
}

export function saveAd(externalAdId: string, snapshot: Omit<ApiSavedAdIn, "user_email">) {
  if (!userEmail || rows.some((r) => r.external_ad_id === externalAdId)) return;
  const optimistic: ApiSavedAd = {
    ...snapshot,
    user_email: userEmail,
    external_ad_id: externalAdId,
    saved_at: new Date().toISOString(),
  };
  const prev = rows;
  rows = [optimistic, ...rows];
  emit();
  upsertSavedAd(externalAdId, { ...snapshot, user_email: userEmail }).catch(() => {
    rows = prev;
    emit();
  });
}

export function unsaveAd(externalAdId: string) {
  if (!userEmail) return;
  const prev = rows;
  rows = rows.filter((r) => r.external_ad_id !== externalAdId);
  if (rows.length === prev.length) return;
  emit();
  removeSavedAd(externalAdId, userEmail).catch(() => {
    rows = prev;
    emit();
  });
}
```

`web/src/lib/useSavedAds.ts`:

```ts
"use client";

import { useSyncExternalStore } from "react";
import {
  getSavedAdsServerSnapshot,
  getSavedAdsSnapshot,
  subscribeSavedAds,
} from "./savedAds";
import type { ApiSavedAd } from "@/types/contracts";

export function useSavedAdsList(): ApiSavedAd[] {
  return useSyncExternalStore(subscribeSavedAds, getSavedAdsSnapshot, getSavedAdsServerSnapshot);
}

export function useIsAdSaved(externalAdId: string | undefined): boolean {
  const ads = useSavedAdsList();
  return !!externalAdId && ads.some((a) => a.external_ad_id === externalAdId);
}

export { saveAd, unsaveAd } from "./savedAds";
```

- [ ] **Step 4: Session wiring + allowlist**

In `web/src/components/SavedUserSync.tsx`, next to the existing `setUser(email)` call add `setSavedAdsUser(email);` (import from `@/lib/savedAds`).

In `web/src/app/backend/[...path]/route.ts` `ALLOWED_RESOURCES`, add:

```ts
  "saved-ads", // /saved-ads (+ /:id, /:id/remove)
```

- [ ] **Step 5: Gates (bare, from `web/`)**

Run: `npm run lint` — expected clean. Run: `npm run build` — expected success.

- [ ] **Step 6: Commit**

```bash
git add web/src/types/contracts.ts web/src/lib/adapters.ts web/src/lib/api.ts web/src/lib/savedAds.ts web/src/lib/useSavedAds.ts web/src/components/SavedUserSync.tsx "web/src/app/backend/[...path]/route.ts"
git commit -m "feat(web): saved-ads store, api client, proxy allowlist" -- web/src/types/contracts.ts web/src/lib/adapters.ts web/src/lib/api.ts web/src/lib/savedAds.ts web/src/lib/useSavedAds.ts web/src/components/SavedUserSync.tsx "web/src/app/backend/[...path]/route.ts"
```

---

### Task 4: Save heart on dossier ad cards

**Files:**
- Modify: `web/src/components/detail/EvidenceReel.tsx` (`AdCard` at ~line 72, `AdsSection` at ~line 287)

**Interfaces:**
- Consumes: `useIsAdSaved`, `saveAd`, `unsaveAd` from `@/lib/useSavedAds`; `Creative` (now with `s3_key`); the `gem` object already in `AdsSection` scope (verify its exact field names for advertiser id / page name / app name in `web/src/types/contracts.ts` — do not guess).
- Produces: nothing new for later tasks (Task 5 renders from store rows, not from this component).

- [ ] **Step 1: Add the heart**

Design constraints (clone the shipped gem-heart look, do not invent): read `web/src/components/saved/FolderPicker.tsx`'s heart variant for icon choice (`IconHeart` / `IconHeartFilled` from `@tabler/icons-react`), size (16), and color tokens; reuse them. Placement: in `AdCard`'s header row, next to the "Ad Library ↗" link. Render the button only when `c.external_ad_id` is present.

`AdCard` gains a snapshot-building prop. Change the signature to `AdCard({ c, gem }: { c: Creative; gem: Gem })` (use the actual type name `AdsSection` receives) and inside:

```tsx
const saved = useIsAdSaved(c.external_ad_id);
const onHeart = () => {
  if (!c.external_ad_id) return;
  if (saved) {
    unsaveAd(c.external_ad_id);
  } else {
    saveAd(c.external_ad_id, {
      advertiser_id: gem.advertiser_id,      // verify exact gem field names
      page_name: gem.page_name ?? null,
      app_name: gem.app_name ?? null,
      title: c.title ?? null,
      body_text: c.body_text ?? null,
      media_type: c.type,
      media_s3_key: c.s3_key ?? null,
      link_url: c.link_url ?? null,
      cta_text: c.cta_text ?? null,
      ad_started_on: c.ad_started_on ?? null,
      ad_active: c.ad_active ?? null,
    });
  }
};
```

Button JSX in the header row (styling mirrored from FolderPicker's heart, not re-invented):

```tsx
{c.external_ad_id && (
  <button
    type="button"
    onClick={onHeart}
    aria-label={saved ? "Remove from saved" : "Save ad"}
    aria-pressed={saved}
    style={{ background: "none", border: "none", padding: 2, cursor: "pointer", display: "inline-flex", color: saved ? "var(--ink)" : "var(--muted)" }}
  >
    {saved ? <IconHeartFilled size={16} aria-hidden /> : <IconHeart size={16} aria-hidden />}
  </button>
)}
```

Update the `<AdCard key={c.id} c={c} />` call in `AdsSection` to pass `gem`. Component type and key stay stable across the save transition (the recurring picker-unmount bug class from the gems rollout — same rule even without a picker).

- [ ] **Step 2: Gates (bare, from `web/`)**

`npm run lint` then `npm run build` — both clean.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/detail/EvidenceReel.tsx
git commit -m "feat(web): save heart on dossier ad cards" -- web/src/components/detail/EvidenceReel.tsx
```

---

### Task 5: /saved — pinned Ads tile + saved-ads view

**Files:**
- Modify: `web/src/components/saved/FolderTiles.tsx`
- Modify: `web/src/components/saved/SavedGems.tsx`
- Create: `web/src/components/saved/SavedAdCard.tsx`

**Interfaces:**
- Consumes: `useSavedAdsList`, `unsaveAd` from `@/lib/useSavedAds`; `ApiSavedAd`; the media URL helper the dossier uses to turn an s3 key into a proxy URL (grep `mediaUrl` in `web/src/lib/` — reuse it, do not rebuild); the `?folder=liked` routing pattern inside `SavedGems.tsx` (history.replaceState — mirror it for `?folder=ads`).
- Produces: nothing further.

- [ ] **Step 1: Ads tile**

In `FolderTiles.tsx`, add props `adsCount: number` and `onOpenAds: () => void`, and render a pinned tile between Liked and the folder list (import `IconAd` from `@tabler/icons-react`; if `IconAd` doesn't exist in the installed version, use `IconRectangle` — check the installed icon set before choosing):

```tsx
      <button type="button" onClick={onOpenAds} className="ft-press" style={tile}>
        <IconAd size={16} aria-hidden style={{ flexShrink: 0 }} />
        <span className="t-sm w-strong" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          Ads
        </span>
        <span className="nums t-xs" style={{ color: "var(--muted)", flexShrink: 0 }}>{adsCount}</span>
      </button>
```

- [ ] **Step 2: SavedAdCard**

`web/src/components/saved/SavedAdCard.tsx` — renders one `ApiSavedAd` in the card grid. Mirror `AdCard`'s media/body rendering (EvidenceReel.tsx:72-140) and `SavedCard`'s card frame:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { IconHeartFilled } from "@tabler/icons-react";
import type { ApiSavedAd } from "@/types/contracts";
import { unsaveAd } from "@/lib/useSavedAds";
// import the same mediaUrl helper adapters.ts uses (grep for it; reuse verbatim)

export function SavedAdCard({ ad }: { ad: ApiSavedAd }) {
  const [broken, setBroken] = useState(false);
  const src = ad.media_s3_key ? mediaUrl(ad.media_s3_key) : undefined;
  const hasMedia = !!src && !broken;
  return (
    <div style={{ border: "1px solid var(--line)", background: "var(--paper)", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "8px 10px" }}>
        {ad.advertiser_id ? (
          <Link href={`/gems/${ad.advertiser_id}`} className="t-sm w-strong" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {ad.page_name ?? ad.app_name ?? "Unknown page"}
          </Link>
        ) : (
          <span className="t-sm w-strong">{ad.page_name ?? ad.app_name ?? "Unknown page"}</span>
        )}
        <button
          type="button"
          onClick={() => unsaveAd(ad.external_ad_id)}
          aria-label="Remove from saved"
          style={{ background: "none", border: "none", padding: 2, cursor: "pointer", display: "inline-flex", color: "var(--ink)" }}
        >
          <IconHeartFilled size={16} aria-hidden />
        </button>
      </div>
      {hasMedia &&
        (ad.media_type === "video" ? (
          <video src={src} controls preload="metadata" playsInline onError={() => setBroken(true)} style={{ width: "100%", height: "auto", display: "block", borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }} />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- media proxy thumbnails
          <img src={src} alt={ad.title ?? ad.body_text ?? "ad creative"} loading="lazy" onError={() => setBroken(true)} style={{ width: "100%", height: "auto", display: "block", borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }} />
        ))}
      {ad.body_text && (
        <div style={{ fontSize: "var(--fs-xs)", color: "var(--ink-soft)", padding: "8px 10px", lineHeight: 1.45, display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {ad.body_text}
        </div>
      )}
    </div>
  );
}
```

Verify the dossier route path (`/gems/[advertiser_id]` vs `/gem/…`) by looking in `web/src/app/` and match whatever `SavedCard` links to.

- [ ] **Step 3: Wire the view into SavedGems.tsx**

Mirror the existing `?folder=liked` branch exactly (state, `history.replaceState` URL sync, back control):

- `const savedAds = useSavedAdsList();`
- Pass `adsCount={savedAds.length}` and `onOpenAds` to `FolderTiles`.
- When the ads view is open, render the same back control as the Liked view, then:

```tsx
{savedAds.length === 0 ? (
  <p className="t-sm" style={{ color: "var(--muted)" }}>
    No saved ads yet. Save one with the heart on a gem&apos;s ads.
  </p>
) : (
  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 12 }}>
    {savedAds.map((a) => (
      <SavedAdCard key={a.external_ad_id} ad={a} />
    ))}
  </div>
)}
```

The tag filter does NOT apply to the ads view (ads have no tags) — hide the tag filter control in this branch if it renders above the grid.

- [ ] **Step 4: Gates (bare, from `web/`)**

`npm run lint` then `npm run build` — both clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/saved/FolderTiles.tsx web/src/components/saved/SavedGems.tsx web/src/components/saved/SavedAdCard.tsx
git commit -m "feat(web): Ads collection on /saved — pinned tile + saved-ads grid" -- web/src/components/saved/FolderTiles.tsx web/src/components/saved/SavedGems.tsx web/src/components/saved/SavedAdCard.tsx
```

---

## Final phase (controller-run, not a subagent task)

1. Whole-branch review (`scripts/review-package MERGE_BASE HEAD`), fix blockers, re-review.
2. Full gates, bare, one consumer on the test DB at a time: Python `python -m pytest` (0 failed), Go `go test ./...` (0 failed), web `npm run lint` + `npm run build`.
3. Local live e2e (harness from the saved-board memory): API `AGF_TEST_DB_URL=… PORT=8010 go run .` from `backend/`, web `AGF_API_INTERNAL_URL=http://localhost:8010/api npm run dev -- -p 3200` from `web/`; browser-drive: open a gem dossier → heart an ad → /saved → Ads tile → card renders → unsave.
4. Deploy, in order: migration 164 on prod → api image → web image → worker/enrich image (`docker compose build enrich && docker compose up -d enrich` on the VM — `up -d worker cron` does NOT touch the enrich daemon; VM compose at /opt/agf/docker-compose.yml drifts from ops/, check both, though this change needs no compose edits).
5. Live verify on prod: "Refresh data" on a gem with a page_id → job done inside deadline → `public.ads` rows for that advertiser gain a fresh `last_seen`/new ads; save an ad → `GET /api/saved-ads` returns it.
6. Update memory + suggest /reflect.
