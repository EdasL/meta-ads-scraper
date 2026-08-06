# Add a gem from an Ad Library link — Implementation Plan

> **Execution:** inline in this session (this session is configured without
> subagent dispatch). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paste a Meta Ad Library URL for an advertiser into the Discover header
and have Field Desk scrape, triage, enrich, score and surface it as a gem.

**Architecture:** The Go API parses the page id out of the URL, upserts the
advertiser, and enqueues one `pipeline_jobs` row with `kind='add_page'`. The
existing keeper daemon claims it at top priority and runs a new one-shot CLI,
`agf.discovery.add_page_one`, which reuses `refresh_one_advertiser` →
`classify_url` → `run_triage(only_ids=…)` → `enrich_one.run_job`. The last of
those already scores the single advertiser and refreshes the gem matview, so the
gem is live when the job reports done. Progress and the triage note ride in the
job row's existing `payload jsonb` — no new columns beyond `manual_add`.

**Tech Stack:** Go 1.x + gin + gorm (API), Python 3 + SQLAlchemy Core `text()`
(pipeline), Next.js + React (web), PostgreSQL.

## Global Constraints

- **Copy rule (project CLAUDE.md):** all user-facing strings are plain, clear,
  professional. No marketing fluff, no exclamation spam. Product nouns stay
  intact: **Field Desk**, **GemScore**, **cohort**. Every new string in this
  plan runs the humanizer skill pass in Task 7 before the feature ships.
- **UI affordance (global CLAUDE.md):** a user-requested action gets a
  first-class control sized like the surface's existing primary buttons, never
  a small text link.
- **Python DB convention:** conn-first helpers, caller owns the transaction, all
  SQL via `text()` with bound params. Never a bare `%` in a migration — use
  `make_interval`.
- **No network calls inside a write transaction** (prod incident precedent).
- **Terminal-status contract:** every `*_one.py` CLI writes its own terminal
  status on the `pipeline_jobs` row on every exit path, plus a
  `logging.crawl_log` marker on failure so a bad entity cannot hot-loop.
- **Commit per logical unit** — each task below ends in its own commit.
- **Gate commands run bare**, never piped through `tail`/`grep` in the same
  Bash call as a success decision.

**Test commands** (bare, exit code read directly):
- Python: `AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it pytest tests/discovery/test_add_page_one.py -v`
- Go: `cd backend && go test ./...`
- Web typecheck: `cd web && npx tsc --noEmit`
- Web lint: `cd web && npm run lint`
- Web unit: `cd web && node --test src/lib/links.test.ts`

---

### Task 1: Migration 165 — `manual_add` column + matview escape hatch

**Files:**
- Create: `db/schema/165_manual_add.sql`

**Interfaces:**
- Produces: `public.advertisers.manual_add BOOLEAN NOT NULL DEFAULT FALSE`;
  `frontend.emerging_scaling_apps` gains columns `manual_add BOOLEAN` and
  `veto_reason TEXT`; `frontend.emerging_scaling_products` passes both through.

The matview's WHERE clause today is
`WHERE (s.vetoed = FALSE OR s.veto_reason = 'longevity_below_floor') AND starts_with(adv.page_id, 'DEMO') IS NOT TRUE`.
It gains `OR adv.manual_add`. The DEMO exclusion stays outside the OR group so a
manual add can never resurrect a DEMO page.

- [ ] **Step 1: Write the migration**

Copy `db/schema/163_matview_funnel_singlepass.sql` verbatim as the base, then
apply exactly three changes to the copy: add the `ALTER TABLE` at the top, add
the two SELECT columns, and extend the WHERE. Everything else — the
`web_funnels` CTE, all LATERALs, the unique index, and the recreated
`frontend.emerging_scaling_products` view — is reproduced unchanged.

Header comment for the new file:

```sql
-- Migration 165: manually-added advertisers.
--
-- A founder can paste a Meta Ad Library link and add that advertiser on demand
-- (spec docs/superpowers/specs/2026-08-06-add-gem-from-ad-library-link-design.md).
-- Those advertisers must surface even when scoring vetoes them — the vetoes
-- exist to filter breadth-sweep noise, not to overrule an explicit request.
-- This mirrors the existing 'incubating' escape hatch (migration 124/125):
-- surfaced, marked, reason visible. The veto thresholds are untouched, so the
-- automated sweeps keep filtering exactly as they do today.
--
-- Structure otherwise verbatim from 163. The dependent view
-- frontend.emerging_scaling_products must be dropped first and is recreated
-- below with manual_add + veto_reason passed through. The unique index on
-- advertiser_id is preserved for REFRESH CONCURRENTLY.
-- NOTE: applied via exec_driver_sql with NO params — never a bare percent sign.

ALTER TABLE public.advertisers
    ADD COLUMN IF NOT EXISTS manual_add BOOLEAN NOT NULL DEFAULT FALSE;
```

Then the DROP/CREATE block from 163. In the matview's SELECT list, immediately
after the existing `(s.vetoed AND s.veto_reason = 'longevity_below_floor') AS incubating,`
line, add:

```sql
    adv.manual_add,
    s.veto_reason,
```

and change the trailing WHERE from 163's version to:

```sql
WHERE (s.vetoed = FALSE
       OR s.veto_reason = 'longevity_below_floor'
       OR adv.manual_add)
  AND starts_with(adv.page_id, 'DEMO') IS NOT TRUE;
```

In the recreated `frontend.emerging_scaling_products` view, change the line
`r.store, r.store_id, r.incubating, r.funnel_url, r.web_only,` to:

```sql
  r.store, r.store_id, r.incubating, r.manual_add, r.veto_reason,
  r.funnel_url, r.web_only,
```

- [ ] **Step 2: Apply it to the integration DB and verify the escape hatch**

Run:

```bash
AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it \
  python -m agf.db.apply_migrations
```

Expected: applies without error. Then verify both directions in one psql call —
a manual-add advertiser with a hard veto IS in the matview, a non-manual one
with the same veto is NOT:

```bash
psql postgresql://agf:agf@localhost:55433/agf_it -c "
  SELECT count(*) FILTER (WHERE manual_add) AS manual_rows,
         count(*) AS total
  FROM frontend.emerging_scaling_apps;"
```

Expected: runs clean (the counts themselves depend on fixture state; the
behavioural assertion lives in Task 4's test, which seeds both rows).

- [ ] **Step 3: Commit**

```bash
git add db/schema/165_manual_add.sql
git commit -m "feat(db): migration 165 — advertisers.manual_add + matview escape hatch"
```

---

### Task 2: Go — Ad Library URL parser

**Files:**
- Create: `backend/helpers/adlibrary.go`
- Test: `backend/helpers/adlibrary_test.go`

**Interfaces:**
- Produces:
  ```go
  // ErrAdLibraryLink classifies why a pasted link could not be turned into a page id.
  type AdLibraryLinkError struct{ Reason, Message string }
  func (e *AdLibraryLinkError) Error() string

  // ParseAdLibraryPageID extracts a Meta page id from a pasted Ad Library link.
  // Returns ("", adID, nil) when the link identifies a single ad rather than a
  // page — the caller resolves adID against public.ads. Returns ("", "", err)
  // with a *AdLibraryLinkError the UI can render verbatim.
  func ParseAdLibraryPageID(raw string) (pageID string, adID string, err error)
  ```

Reason codes (stable, for tests and the UI): `not_ad_library`, `no_identifier`.
Messages are drafted here and finalised by the humanizer pass in Task 7.

- [ ] **Step 1: Write the failing tests**

```go
package helpers

import "testing"

func TestParseAdLibraryPageID_AcceptedForms(t *testing.T) {
	cases := []struct{ name, in, wantPage string }{
		{"canonical view_all_page_id",
			"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&view_all_page_id=123456789",
			"123456789"},
		{"page id first param",
			"https://www.facebook.com/ads/library/?view_all_page_id=987654321&country=US",
			"987654321"},
		{"no scheme",
			"www.facebook.com/ads/library/?view_all_page_id=555",
			"555"},
		{"m dot host",
			"https://m.facebook.com/ads/library/?view_all_page_id=555",
			"555"},
		{"bare numeric id", "123456789", "123456789"},
		{"bare numeric id with spaces", "  123456789  ", "123456789"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			page, ad, err := ParseAdLibraryPageID(c.in)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if ad != "" {
				t.Fatalf("expected no ad id, got %q", ad)
			}
			if page != c.wantPage {
				t.Fatalf("page id = %q, want %q", page, c.wantPage)
			}
		})
	}
}

func TestParseAdLibraryPageID_SingleAdLink(t *testing.T) {
	page, ad, err := ParseAdLibraryPageID(
		"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&id=778899")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if page != "" {
		t.Fatalf("expected no page id, got %q", page)
	}
	if ad != "778899" {
		t.Fatalf("ad id = %q, want 778899", ad)
	}
}

func TestParseAdLibraryPageID_Rejected(t *testing.T) {
	cases := []struct{ name, in, wantReason string }{
		{"empty", "", "not_ad_library"},
		{"whitespace", "   ", "not_ad_library"},
		{"wrong host", "https://example.com/ads/library/?view_all_page_id=1", "not_ad_library"},
		{"facebook but not ad library", "https://www.facebook.com/somepage", "not_ad_library"},
		{"keyword search url",
			"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&q=sleep",
			"no_identifier"},
		{"ad library no params", "https://www.facebook.com/ads/library/", "no_identifier"},
		{"non numeric page id",
			"https://www.facebook.com/ads/library/?view_all_page_id=abc", "no_identifier"},
		{"garbage", "not a url at all", "not_ad_library"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			page, ad, err := ParseAdLibraryPageID(c.in)
			if err == nil {
				t.Fatalf("expected an error, got page=%q ad=%q", page, ad)
			}
			le, ok := err.(*AdLibraryLinkError)
			if !ok {
				t.Fatalf("expected *AdLibraryLinkError, got %T", err)
			}
			if le.Reason != c.wantReason {
				t.Fatalf("reason = %q, want %q", le.Reason, c.wantReason)
			}
			if le.Message == "" {
				t.Fatal("error message must be non-empty (it is rendered to the user)")
			}
		})
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && go test ./helpers/ -run TestParseAdLibraryPageID -v`
Expected: FAIL — `undefined: ParseAdLibraryPageID`.

- [ ] **Step 3: Write the implementation**

```go
package helpers

import (
	"net/url"
	"regexp"
	"strings"
)

// AdLibraryLinkError is a pasted-link rejection with a message the UI renders
// verbatim. Reason is a stable code for tests and client-side branching.
type AdLibraryLinkError struct {
	Reason  string
	Message string
}

func (e *AdLibraryLinkError) Error() string { return e.Message }

var (
	numericID     = regexp.MustCompile(`^[0-9]{5,20}$`)
	adLibraryHost = regexp.MustCompile(`(^|\.)facebook\.com$`)
)

// ParseAdLibraryPageID extracts a Meta page id from a pasted Ad Library link.
//
// Accepted: an ads/library URL carrying view_all_page_id, and a bare numeric
// page id. An ads/library URL carrying only `id` identifies ONE ad, not a page;
// it returns that ad id for the caller to resolve against public.ads, because
// Meta's AdLibraryAdDetailsV2Query response carries no page id (see
// src/agf/extract/ad_details.py — AdDetails is reach + active only).
func ParseAdLibraryPageID(raw string) (string, string, error) {
	s := strings.TrimSpace(raw)
	if s == "" {
		return "", "", notAdLibrary()
	}
	if numericID.MatchString(s) {
		return s, "", nil
	}
	withScheme := s
	if !strings.Contains(s, "://") {
		withScheme = "https://" + s
	}
	u, err := url.Parse(withScheme)
	if err != nil || u.Host == "" {
		return "", "", notAdLibrary()
	}
	if !adLibraryHost.MatchString(strings.ToLower(u.Hostname())) ||
		!strings.HasPrefix(strings.ToLower(u.Path), "/ads/library") {
		return "", "", notAdLibrary()
	}
	q := u.Query()
	if page := strings.TrimSpace(q.Get("view_all_page_id")); numericID.MatchString(page) {
		return page, "", nil
	}
	if ad := strings.TrimSpace(q.Get("id")); numericID.MatchString(ad) {
		return "", ad, nil
	}
	return "", "", &AdLibraryLinkError{
		Reason: "no_identifier",
		Message: "That Ad Library link is not pointing at one advertiser. " +
			"Open the advertiser, then copy the URL from your browser.",
	}
}

func notAdLibrary() *AdLibraryLinkError {
	return &AdLibraryLinkError{
		Reason: "not_ad_library",
		Message: "Paste a Meta Ad Library link, for example " +
			"https://www.facebook.com/ads/library/?view_all_page_id=123456789",
	}
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && go test ./helpers/ -run TestParseAdLibraryPageID -v`
Expected: PASS, all subtests.

- [ ] **Step 5: Commit**

```bash
git add backend/helpers/adlibrary.go backend/helpers/adlibrary_test.go
git commit -m "feat(api): parse a page id out of a pasted Ad Library link"
```

---

### Task 3: Go — add-gem repository, controller, routes

**Files:**
- Create: `backend/repositories/add_page_repo.go`
- Create: `backend/controllers/add_gem_controller.go`
- Modify: `backend/routers/routers.go`
- Modify: `backend/models/` (add the two payload structs to the file holding
  `EnrichJobOut`; find it with `grep -rn "EnrichJobOut struct" backend/models/`)
- Test: `backend/internal/integration/add_gem_test.go`

**Interfaces:**
- Consumes: `helpers.ParseAdLibraryPageID`, `helpers.AdLibraryLinkError` (Task 2).
- Produces:
  ```go
  // repositories
  func PageIDForAdID(ctx context.Context, db *gorm.DB, adID string) (string, error) // "" when unknown
  func UpsertAdvertiserByPageID(ctx context.Context, db *gorm.DB, pageID string) (int64, bool, error) // id, alreadyKnown
  func ActiveAddPageJob(ctx context.Context, db *gorm.DB, advertiserID int64) (*models.AddPageJobRow, error)
  func InsertAddPageJob(ctx context.Context, db *gorm.DB, advertiserID int64) (int64, error)
  func AddPageJobByID(ctx context.Context, db *gorm.DB, jobID int64) (*models.AddPageJobRow, error)

  // models
  type AddPageJobRow struct {
      ID           int64
      AdvertiserID int64
      Status       string
      Phase        *string
      TriageNote   *string
      ErrorText    *string
      PageName     *string
      IconURL      *string
      AdCount      int
      CreatedAt    models.APITime
  }
  type AddGemOut struct {
      JobID        int64   `json:"job_id"`
      AdvertiserID int64   `json:"advertiser_id"`
      Status       string  `json:"status"`
      Phase        *string `json:"phase"`
      PageName     *string `json:"page_name"`
      IconURL      *string `json:"icon_url"`
      AdCount      int     `json:"ad_count"`
      TriageNote   *string `json:"triage_note"`
      ErrorText    *string `json:"error_text"`
      AlreadyKnown bool    `json:"already_known"`
  }
  ```
- Produces (HTTP): `POST /api/gems/add` (token-guarded) and
  `GET /api/gems/add/:job_id`.

`Phase` and `TriageNote` read out of the job's existing `payload jsonb`
(`payload->>'phase'`, `payload->>'triage_note'`) — no schema change.

- [ ] **Step 1: Write the failing integration test**

Model it on `backend/internal/integration/hunt_test.go` (read it for the
harness's DB setup and cleanup idiom). The test body:

```go
package integration

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAddGemFromAdLibraryLink(t *testing.T) {
	env := newEnv(t) // same harness constructor hunt_test.go uses
	const pageID = "900900900"

	t.Cleanup(func() {
		must(env.DB.Exec(
			`DELETE FROM public.pipeline_jobs WHERE kind = 'add_page' AND advertiser_id IN (
			   SELECT id FROM public.advertisers WHERE page_id = ?)`, pageID).Error)
		must(env.DB.Exec(`DELETE FROM public.advertisers WHERE page_id = ?`, pageID).Error)
	})

	// An unparseable link writes nothing and returns 422 with a message.
	resp := env.POST(t, "/api/gems/add", map[string]any{"url": "https://example.com/nope"})
	require.Equal(t, http.StatusUnprocessableEntity, resp.Code)
	var advCount int64
	require.NoError(t, env.DB.Raw(
		`SELECT count(*) FROM public.advertisers WHERE page_id = ?`, pageID).Row().Scan(&advCount))
	require.Zero(t, advCount)

	// A good link creates exactly one advertiser and one queued add_page job.
	url := "https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&view_all_page_id=" + pageID
	resp = env.POST(t, "/api/gems/add", map[string]any{"url": url})
	require.Equal(t, http.StatusAccepted, resp.Code)
	var first struct {
		JobID        int64 `json:"job_id"`
		AdvertiserID int64 `json:"advertiser_id"`
		Status       string
		AlreadyKnown bool `json:"already_known"`
	}
	env.Decode(t, resp, &first)
	require.NotZero(t, first.JobID)
	require.NotZero(t, first.AdvertiserID)
	require.False(t, first.AlreadyKnown)
	require.Equal(t, "queued", first.Status)

	var kind, status string
	require.NoError(t, env.DB.Raw(
		`SELECT kind, status FROM public.pipeline_jobs WHERE id = ?`, first.JobID).
		Row().Scan(&kind, &status))
	require.Equal(t, "add_page", kind)
	require.Equal(t, "queued", status)

	// Pasting the same link again returns the SAME advertiser and the SAME
	// in-flight job — never a second queued row.
	resp = env.POST(t, "/api/gems/add", map[string]any{"url": url})
	require.Equal(t, http.StatusAccepted, resp.Code)
	var second struct {
		JobID        int64 `json:"job_id"`
		AdvertiserID int64 `json:"advertiser_id"`
		AlreadyKnown bool  `json:"already_known"`
	}
	env.Decode(t, resp, &second)
	require.Equal(t, first.AdvertiserID, second.AdvertiserID)
	require.Equal(t, first.JobID, second.JobID)
	require.True(t, second.AlreadyKnown)

	var jobCount int64
	require.NoError(t, env.DB.Raw(
		`SELECT count(*) FROM public.pipeline_jobs WHERE kind = 'add_page' AND advertiser_id = ?`,
		first.AdvertiserID).Row().Scan(&jobCount))
	require.EqualValues(t, 1, jobCount)

	// Status poll reports the job.
	resp = env.GET(t, "/api/gems/add/"+itoa(first.JobID))
	require.Equal(t, http.StatusOK, resp.Code)
	var stat struct {
		JobID  int64 `json:"job_id"`
		Status string
	}
	env.Decode(t, resp, &stat)
	require.Equal(t, first.JobID, stat.JobID)
	require.Equal(t, "queued", stat.Status)
}
```

If the harness's helper names differ (`newEnv`, `env.POST`, `env.Decode`,
`must`, `itoa`), adapt to whatever `hunt_test.go` actually uses — do not invent
helpers. Read that file first and match it exactly.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && go test ./internal/integration/ -run TestAddGemFromAdLibraryLink -v`
Expected: FAIL — route not registered (404) or compile error on the missing
controller.

- [ ] **Step 3: Write the repository**

```go
package repositories

import (
	"context"
	"errors"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// PageIDForAdID resolves a single-ad Ad Library link to its advertiser's page
// id using ads we have already scraped. Returns "" when we have never seen it.
func PageIDForAdID(ctx context.Context, db *gorm.DB, adID string) (string, error) {
	var pageID string
	err := db.WithContext(ctx).Raw(
		`SELECT adv.page_id
		   FROM public.ads a
		   JOIN public.advertisers adv ON adv.id = a.advertiser_id
		  WHERE a.external_ad_id = ?
		  LIMIT 1`, adID).Row().Scan(&pageID)
	if errors.Is(err, gorm.ErrRecordNotFound) || errors.Is(err, sqlNoRows) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return pageID, nil
}

// UpsertAdvertiserByPageID returns the advertiser id for a page id, creating a
// minimal row when we have never seen the page. alreadyKnown reports whether
// the row pre-existed — the scrape fills in page_name either way.
func UpsertAdvertiserByPageID(ctx context.Context, db *gorm.DB, pageID string) (int64, bool, error) {
	var id int64
	err := db.WithContext(ctx).Raw(
		`SELECT id FROM public.advertisers WHERE page_id = ?`, pageID).Row().Scan(&id)
	if err == nil {
		return id, true, nil
	}
	if !errors.Is(err, sqlNoRows) && !errors.Is(err, gorm.ErrRecordNotFound) {
		return 0, false, err
	}
	err = db.WithContext(ctx).Raw(
		`INSERT INTO public.advertisers (page_id)
		 VALUES (?)
		 ON CONFLICT (page_id) DO UPDATE SET last_seen = now()
		 RETURNING id`, pageID).Row().Scan(&id)
	if err != nil {
		return 0, false, err
	}
	return id, false, nil
}

// ActiveAddPageJob returns the queued/running add_page job for an advertiser,
// or nil. The partial unique index pipeline_jobs_active_uq guarantees at most one.
func ActiveAddPageJob(ctx context.Context, db *gorm.DB, advertiserID int64) (*models.AddPageJobRow, error) {
	return scanAddPageJob(db.WithContext(ctx).Raw(addPageJobSelect+`
		 WHERE j.kind = 'add_page' AND j.advertiser_id = ?
		   AND j.status IN ('queued','running')
		 ORDER BY j.id DESC LIMIT 1`, advertiserID))
}

// AddPageJobByID reads one add_page job for the status poll.
func AddPageJobByID(ctx context.Context, db *gorm.DB, jobID int64) (*models.AddPageJobRow, error) {
	return scanAddPageJob(db.WithContext(ctx).Raw(addPageJobSelect+`
		 WHERE j.kind = 'add_page' AND j.id = ?`, jobID))
}

// InsertAddPageJob enqueues one job. ON CONFLICT against the partial active
// unique index makes a concurrent double-paste a no-op; the caller re-reads.
func InsertAddPageJob(ctx context.Context, db *gorm.DB, advertiserID int64) (int64, error) {
	var id int64
	err := db.WithContext(ctx).Raw(
		`INSERT INTO public.pipeline_jobs (kind, advertiser_id)
		 VALUES ('add_page', ?)
		 ON CONFLICT (kind, advertiser_id) WHERE status IN ('queued','running')
		 DO NOTHING
		 RETURNING id`, advertiserID).Row().Scan(&id)
	if errors.Is(err, sqlNoRows) || errors.Is(err, gorm.ErrRecordNotFound) {
		return 0, nil // lost the race; caller reads the existing active job
	}
	return id, err
}

const addPageJobSelect = `
	SELECT j.id, j.advertiser_id, j.status,
	       j.payload->>'phase'       AS phase,
	       j.payload->>'triage_note' AS triage_note,
	       j.error_text,
	       adv.page_name,
	       (SELECT count(*) FROM public.ads a WHERE a.advertiser_id = j.advertiser_id) AS ad_count,
	       j.created_at
	  FROM public.pipeline_jobs j
	  JOIN public.advertisers adv ON adv.id = j.advertiser_id`
```

Add the `scanAddPageJob` helper and the `sqlNoRows` sentinel following whatever
row-scanning idiom the neighbouring repo files already use — read
`backend/repositories/liked_gems_repo.go` and match it rather than introducing a
new one. Icon url comes from the matview once it exists; until then it is NULL,
which the UI already handles.

- [ ] **Step 4: Write the controller**

```go
package controllers

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

type AddGemController struct{}

func NewAddGemController() *AddGemController { return &AddGemController{} }

type addGemIn struct {
	URL string `json:"url"`
}

func toAddGemOut(row *models.AddPageJobRow, alreadyKnown bool) models.AddGemOut {
	return models.AddGemOut{
		JobID:        row.ID,
		AdvertiserID: row.AdvertiserID,
		Status:       row.Status,
		Phase:        row.Phase,
		PageName:     row.PageName,
		AdCount:      row.AdCount,
		TriageNote:   row.TriageNote,
		ErrorText:    row.ErrorText,
		AlreadyKnown: alreadyKnown,
	}
}

// Add handles POST /api/gems/add — parse the link, upsert the advertiser,
// enqueue one add_page job. The scrape itself runs on the worker VM.
func (c *AddGemController) Add(ctx *gin.Context) {
	var in addGemIn
	if err := ctx.ShouldBindJSON(&in); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "Send a JSON body with a url field.")
		return
	}
	pageID, adID, err := helpers.ParseAdLibraryPageID(in.URL)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()

	if pageID == "" && adID != "" {
		pageID, err = repositories.PageIDForAdID(rctx, db.DB(), adID)
		if err != nil {
			helpers.Detail(ctx, http.StatusInternalServerError, "Could not look up that ad.")
			return
		}
		if pageID == "" {
			helpers.Detail(ctx, http.StatusUnprocessableEntity,
				"That link points at one ad Field Desk has not seen. Open the advertiser in Ad Library and paste that URL instead.")
			return
		}
	}

	advertiserID, alreadyKnown, err := repositories.UpsertAdvertiserByPageID(rctx, db.DB(), pageID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "Could not add that advertiser.")
		return
	}

	// One active job per advertiser (pipeline_jobs_active_uq). An in-flight job
	// is returned as-is rather than queueing a duplicate scrape.
	if active, err := repositories.ActiveAddPageJob(rctx, db.DB(), advertiserID); err == nil && active != nil {
		ctx.JSON(http.StatusAccepted, toAddGemOut(active, true))
		return
	}
	if _, err := repositories.InsertAddPageJob(rctx, db.DB(), advertiserID); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "Could not start the scrape.")
		return
	}
	row, err := repositories.ActiveAddPageJob(rctx, db.DB(), advertiserID)
	if err != nil || row == nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "Could not start the scrape.")
		return
	}
	ctx.JSON(http.StatusAccepted, toAddGemOut(row, alreadyKnown))
}

// Status handles GET /api/gems/add/:job_id.
func (c *AddGemController) Status(ctx *gin.Context) {
	jobID, err := strconv.ParseInt(ctx.Param("job_id"), 10, 64)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "job_id must be an integer.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	row, err := repositories.AddPageJobByID(rctx, db.DB(), jobID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "Could not read the job.")
		return
	}
	if row == nil {
		helpers.Detail(ctx, http.StatusNotFound, "No such job.")
		return
	}
	ctx.JSON(http.StatusOK, toAddGemOut(row, false))
}
```

- [ ] **Step 5: Register the routes**

In `backend/routers/routers.go`, next to the existing enrich routes (~line 58),
add the controller construction alongside the others and:

```go
		// On-demand gem add from a pasted Ad Library link. The POST is a write,
		// so it is token-guarded like enrich and the hunt writes; the status
		// poll is a plain read.
		api.POST("/gems/add", middlewares.RequireToken(), addGem.Add)
		api.GET("/gems/add/:job_id", addGem.Status)
```

Check the existing `/gems` route group for a path collision with
`GET /gems/:advertiser_id` — gin rejects conflicting wildcard segments at the
same position. If `/gems/:advertiser_id` already exists, the literal `add`
segment conflicts; in that case register the pair as `/gem-adds` and
`/gem-adds/:job_id` instead, and use those paths in Task 6. Resolve this by
running the tests, not by guessing.

- [ ] **Step 6: Run the tests**

Run: `cd backend && go test ./...`
Expected: PASS, including `TestAddGemFromAdLibraryLink`.

- [ ] **Step 7: Commit**

```bash
git add backend/repositories/add_page_repo.go backend/controllers/add_gem_controller.go \
        backend/routers/routers.go backend/models backend/internal/integration/add_gem_test.go
git commit -m "feat(api): POST /api/gems/add enqueues an add_page job from a pasted link"
```

---

### Task 4: Python — the `add_page` job body

**Files:**
- Create: `src/agf/discovery/add_page_one.py`
- Modify: `src/agf/db/pipeline_jobs.py` (add `set_job_phase`, `set_job_note`)
- Test: `tests/discovery/test_add_page_one.py`

**Interfaces:**
- Consumes: `keeper_history.refresh_one_advertiser`, `classify_url`,
  `triage.run_triage`, `enrich_one.run_job` / `_default_enrich` / `_default_score`,
  `pipeline_jobs.set_job_status`, `workers.scheduler.mark_crawled`.
- Produces:
  ```python
  # db/pipeline_jobs.py
  def set_job_phase(conn, job_id: int, phase: str) -> None
  def set_job_note(conn, job_id: int, note: str) -> None

  # discovery/add_page_one.py
  def run(engine, advertiser_id: int, job_id: int, *,
          refresh=refresh_one_advertiser, triage=run_triage,
          enrich=None, extractor=None,
          lookup_ios=None, lookup_android=None) -> int
  def main(argv=None) -> int   # CLI: --advertiser-id N --job-id M
  ```

`run` returns 0 on success, 1 on failure — same contract as `keeper_one.run`.

- [ ] **Step 1: Add the two payload helpers**

In `src/agf/db/pipeline_jobs.py`, after `set_job_status`:

```python
def set_job_phase(conn, job_id: int, phase: str) -> None:
    """Write a short progress label into the job's payload so the API's status
    poll can narrate the run. jsonb_set on a COALESCE'd payload so a row whose
    payload was somehow NULL still takes the write."""
    conn.execute(text(
        "UPDATE public.pipeline_jobs "
        "SET payload = jsonb_set(COALESCE(payload, '{}'::jsonb), "
        "                        '{phase}', to_jsonb(CAST(:phase AS text)), true) "
        "WHERE id = :id"),
        {"phase": phase, "id": job_id})


def set_job_note(conn, job_id: int, note: str) -> None:
    """Write a user-facing note (e.g. a triage verdict) into the job payload."""
    conn.execute(text(
        "UPDATE public.pipeline_jobs "
        "SET payload = jsonb_set(COALESCE(payload, '{}'::jsonb), "
        "                        '{triage_note}', to_jsonb(CAST(:note AS text)), true) "
        "WHERE id = :id"),
        {"note": note, "id": job_id})
```

- [ ] **Step 2: Write the failing tests**

Read `tests/discovery/test_keeper_one.py` first (if present) and match its
fixture idiom. The test file:

```python
"""add_page_one: the pasted-link job body. Every case drives the real DB
(AGF_TEST_DB_URL) with the extractor and lookups faked, so the assertions are
about persisted state, not call counts."""
import pytest
from sqlalchemy import text

from agf.db.engine import begin
from agf.discovery import add_page_one


PAGE_ID = "700700700"


@pytest.fixture
def advertiser(engine):
    with begin(engine) as conn:
        aid = conn.execute(text(
            "INSERT INTO public.advertisers (page_id) VALUES (:p) "
            "ON CONFLICT (page_id) DO UPDATE SET last_seen = now() RETURNING id"),
            {"p": PAGE_ID}).scalar_one()
        job = conn.execute(text(
            "INSERT INTO public.pipeline_jobs (kind, advertiser_id, status) "
            "VALUES ('add_page', :a, 'running') RETURNING id"), {"a": aid}).scalar_one()
    yield aid, job
    with begin(engine) as conn:
        conn.execute(text("DELETE FROM public.pipeline_jobs WHERE advertiser_id = :a"), {"a": aid})
        conn.execute(text("DELETE FROM public.advertisers WHERE id = :a"), {"a": aid})


def _job(engine, job_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT status, error_text, payload->>'phase' AS phase, "
            "       payload->>'triage_note' AS note "
            "FROM public.pipeline_jobs WHERE id = :i"), {"i": job_id}).mappings().one()


def test_marks_manual_add_and_finishes_done(engine, advertiser):
    aid, job = advertiser
    calls = {}

    async def fake_refresh(eng, extractor, advertiser_id, page_id, flip_vanished=False):
        calls["refresh"] = (advertiser_id, page_id, flip_vanished)

    rc = add_page_one.run(engine, aid, job,
                          refresh=fake_refresh,
                          triage=lambda conn, **kw: {"kept": 1, "vetoed": 0},
                          enrich=lambda **kw: None,
                          extractor=object())
    assert rc == 0
    assert calls["refresh"] == (aid, PAGE_ID, False)
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT manual_add FROM public.advertisers WHERE id = :a"), {"a": aid}).scalar() is True
    assert _job(engine, job)["status"] == "done"


def test_veto_is_recorded_but_the_advertiser_is_still_added(engine, advertiser):
    aid, job = advertiser

    async def fake_refresh(eng, extractor, advertiser_id, page_id, flip_vanished=False):
        return None

    rc = add_page_one.run(engine, aid, job,
                          refresh=fake_refresh,
                          triage=lambda conn, **kw: {"kept": 0, "vetoed": 1,
                                                     "veto_reason": "no_app"},
                          enrich=lambda **kw: None,
                          extractor=object())
    assert rc == 0
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT manual_add FROM public.advertisers WHERE id = :a"), {"a": aid}).scalar() is True
    row = _job(engine, job)
    assert row["status"] == "done"
    assert row["note"] and "no_app" in row["note"]


def test_scrape_failure_writes_terminal_error_and_a_crawl_marker(engine, advertiser):
    aid, job = advertiser

    async def boom(eng, extractor, advertiser_id, page_id, flip_vanished=False):
        raise RuntimeError("meta said no")

    rc = add_page_one.run(engine, aid, job, refresh=boom,
                          triage=lambda conn, **kw: {}, enrich=lambda **kw: None,
                          extractor=object())
    assert rc == 1
    row = _job(engine, job)
    assert row["status"] == "error"
    assert "meta said no" in (row["error_text"] or "")
    with engine.connect() as conn:
        marked = conn.execute(text(
            "SELECT count(*) FROM logging.crawl_log "
            "WHERE entity_type = 'add_page' AND entity_id = :a"), {"a": aid}).scalar()
    assert marked >= 1


def test_advertiser_without_a_page_id_fails_cleanly(engine, advertiser):
    aid, job = advertiser
    with begin(engine) as conn:
        conn.execute(text("UPDATE public.advertisers SET page_id = NULL WHERE id = :a"), {"a": aid})
    rc = add_page_one.run(engine, aid, job, extractor=object())
    assert rc == 1
    assert _job(engine, job)["status"] == "error"


def test_manual_add_survives_a_hard_veto_in_the_matview(engine, advertiser):
    """Migration 165's escape hatch, asserted end to end: a manual_add advertiser
    with vetoed=TRUE is in the matview; an identical non-manual one is not."""
    aid, _ = advertiser
    with begin(engine) as conn:
        other = conn.execute(text(
            "INSERT INTO public.advertisers (page_id) VALUES ('700700701') RETURNING id")).scalar_one()
        conn.execute(text("UPDATE public.advertisers SET manual_add = TRUE WHERE id = :a"), {"a": aid})
        for adv in (aid, other):
            conn.execute(text(
                "INSERT INTO adtech.advertiser_scores (advertiser_id, vetoed, veto_reason, scored_at) "
                "VALUES (:a, TRUE, 'no_app', now()) "
                "ON CONFLICT (advertiser_id) DO UPDATE SET vetoed = TRUE, veto_reason = 'no_app'"),
                {"a": adv})
    from agf.scoring.run_scoring import refresh_emerging_matview
    refresh_emerging_matview(engine)
    with engine.connect() as conn:
        present = {r[0] for r in conn.execute(text(
            "SELECT advertiser_id FROM frontend.emerging_scaling_apps "
            "WHERE advertiser_id IN (:a, :b)"), {"a": aid, "b": other})}
        conn.close()
    with begin(engine) as conn:
        conn.execute(text("DELETE FROM adtech.advertiser_scores WHERE advertiser_id = :b"), {"b": other})
        conn.execute(text("DELETE FROM public.advertisers WHERE id = :b"), {"b": other})
    assert aid in present
    assert other not in present
```

The `engine` fixture comes from the repo's existing `conftest.py`; if its name
differs, use the repo's. If `adtech.advertiser_scores` has NOT NULL columns
beyond those inserted above, add them — read `db/schema/050_scoring.sql` for
the real column set rather than guessing.

- [ ] **Step 3: Run the tests to verify they fail**

Run:
```bash
AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it \
  pytest tests/discovery/test_add_page_one.py -v
```
Expected: FAIL — `ModuleNotFoundError: agf.discovery.add_page_one`.

- [ ] **Step 4: Write the implementation**

```python
"""One-shot pasted-link add (subprocess unit for the keeper daemon).

    python -m agf.discovery.add_page_one --advertiser-id N --job-id M

The founder pastes a Meta Ad Library link; the API resolves it to a page id,
upserts the advertiser and queues this job. Here we pull the page's US ads,
read them for a store listing, triage that listing, mark the advertiser as a
manual add, then run the normal enrich path — which scores this one advertiser
and refreshes the gem matview, so the gem is live when this job reports done.

Terminal-status contract (same as keeper_one): steps 1-4 own terminal status
only for their own failures; enrich_one.run_job is the last writer on the happy
path and its 'done' is the job's 'done'. Exactly one terminal write per run.

Triage NEVER rejects here. The vetoes exist to filter breadth-sweep noise, and
the founder asked for this page by name: the verdict is recorded as a note and
advertisers.manual_add carries it past the matview's veto filter (migration 165).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.db.pipeline_jobs import set_job_note, set_job_phase, set_job_status
from agf.discovery.keeper_history import refresh_one_advertiser
from agf.discovery.triage import run_triage
from agf.workers.scheduler import mark_crawled

log = logging.getLogger("agf.add_page")

TIMEOUT_SEC = int(os.environ.get("AGF_ADD_PAGE_TIMEOUT_SEC", "600"))


class AddPageTimeout(BaseException):
    """SIGALRM watchdog — BaseException so no `except Exception` swallows it."""


def _page_id(engine, advertiser_id: int) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT page_id FROM public.advertisers WHERE id = :a"),
            {"a": advertiser_id}).scalar()


def _fail(engine, advertiser_id: int, job_id: int, msg: str) -> int:
    set_job_status_standalone(engine, job_id, "error", msg[:500])
    with begin(engine) as conn:
        mark_crawled(conn, "add_page", advertiser_id, "error")
    return 1


def set_job_status_standalone(engine, job_id: int, status: str, error: str | None = None) -> None:
    with begin(engine) as conn:
        set_job_status(conn, job_id, status, error)


def _phase(engine, job_id: int, label: str) -> None:
    with begin(engine) as conn:
        set_job_phase(conn, job_id, label)


def run(engine, advertiser_id: int, job_id: int, *,
        refresh=refresh_one_advertiser, triage=run_triage, enrich=None,
        extractor=None, lookup_ios=None, lookup_android=None) -> int:
    page_id = _page_id(engine, advertiser_id)
    if not page_id:
        return _fail(engine, advertiser_id, job_id, "advertiser has no page_id")

    if extractor is None:
        from agf.extract.facade import Extractor
        extractor = Extractor()

    # 1. Pull the page's US ads (+ the 16-week series). For a brand-new page this
    #    IS the initial pull; it also fills in page_name and page_like_count.
    _phase(engine, job_id, "Pulling ads")
    try:
        asyncio.run(refresh(engine, extractor, advertiser_id, page_id,
                            flip_vanished=False))
    except Exception as e:  # noqa: BLE001 — terminal status on every failure path
        log.exception("add_page %d: scrape failed", advertiser_id)
        return _fail(engine, advertiser_id, job_id, repr(e))

    # 2+3. Triage this advertiser's own candidates only, so a paste never queues
    #      behind the global status='new' backlog. Network lookups run OUTSIDE
    #      the write txn is not possible here (run_triage owns both), so we keep
    #      the scope tiny — one advertiser's candidates.
    _phase(engine, job_id, "Checking the store listing")
    note = None
    try:
        if lookup_ios is None or lookup_android is None:
            import httpx
            from agf.enrich_full.appstore import lookup_google_play, lookup_itunes_batch
            from functools import partial
            client = httpx.Client(timeout=20, follow_redirects=True)
            lookup_ios = lookup_ios or partial(lookup_itunes_batch, client, pause=3.0)
            lookup_android = lookup_android or lookup_google_play
        with begin(engine) as conn:
            ids = [r[0] for r in conn.execute(text(
                "SELECT c.id FROM discovery.candidates c "
                "WHERE c.page_id = :p AND c.status = 'new'"), {"p": page_id})]
            stats = triage(conn, lookup_ios=lookup_ios,
                           lookup_android=lookup_android, only_ids=ids) or {}
        if stats.get("vetoed"):
            note = f"Flagged by triage: {stats.get('veto_reason') or 'vetoed'}"
    except Exception:  # noqa: BLE001 — triage is advisory here, never fatal
        log.exception("add_page %d: triage failed (continuing)", advertiser_id)
        note = "Store-listing check did not complete."

    # 4. Mark it as a manual add — this is what carries it past the matview's
    #    veto filter (migration 165). The note keeps the reason visible.
    _phase(engine, job_id, "Adding")
    with begin(engine) as conn:
        conn.execute(text(
            "UPDATE public.advertisers SET manual_add = TRUE WHERE id = :a"),
            {"a": advertiser_id})
        if note:
            set_job_note(conn, job_id, note)

    # 5. The normal enrich path: enrich, score THIS advertiser, refresh the
    #    matview. run_job writes the terminal status on this same job row.
    _phase(engine, job_id, "Enriching")
    if enrich is None:
        from agf.enrich_full.enrich_one import _default_enrich, _default_score, run_job
        import httpx
        http_client = httpx.Client(timeout=30, follow_redirects=True)
        try:
            run_job(engine, advertiser_id=advertiser_id, job_id=job_id,
                    enrich_call=lambda eng, aid: _default_enrich(
                        eng, aid, extractor=extractor, http_client=http_client,
                        creative_urls=None),
                    score_call=_default_score,
                    status_table="pipeline_jobs",
                    mark_crawled_on_success=False)
        finally:
            http_client.close()
    else:
        enrich(engine=engine, advertiser_id=advertiser_id, job_id=job_id)
        set_job_status_standalone(engine, job_id, "done")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--advertiser-id", type=int, required=True)
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=TIMEOUT_SEC)
    args = ap.parse_args(argv)

    def _alarm(signum, frame):
        raise AddPageTimeout(f"add_page exceeded {args.timeout}s")

    signal.signal(signal.SIGALRM, _alarm)
    engine = get_engine()
    signal.alarm(args.timeout)
    try:
        return run(engine, args.advertiser_id, args.job_id)
    except AddPageTimeout as e:
        return _fail(engine, args.advertiser_id, args.job_id, str(e))
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())
```

The `discovery.candidates` table/column names in the triage step are a
placeholder-shaped risk: before running, `grep -n "candidates" src/agf/discovery/triage.py`
and use the real table, the real page-id column, and the real "new" status
value. Do not guess.

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it \
  pytest tests/discovery/test_add_page_one.py -v
```
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add src/agf/discovery/add_page_one.py src/agf/db/pipeline_jobs.py \
        tests/discovery/test_add_page_one.py
git commit -m "feat(pipeline): add_page job body — scrape, triage, force-keep, enrich"
```

---

### Task 5: Python — route `add_page` through the keeper daemon

**Files:**
- Modify: `src/agf/discovery/keeper_daemon.py`
- Test: `tests/discovery/test_keeper_daemon.py` (extend; create if absent)

**Interfaces:**
- Consumes: `add_page_one` (Task 4).
- Produces: `keeper_daemon.KINDS == ("add_page", "keeper_history", "corpus_refresh")`
  and `run_keeper_subprocess(advertiser_id, job_id, kind)` dispatching
  `kind == "add_page"` to `python -m agf.discovery.add_page_one`.

- [ ] **Step 1: Write the failing test**

```python
def test_add_page_jobs_are_claimed_before_keeper_and_corpus():
    from agf.discovery import keeper_daemon
    assert keeper_daemon.KINDS[0] == "add_page"
    assert set(keeper_daemon.KINDS) == {"add_page", "keeper_history", "corpus_refresh"}


def test_add_page_kind_runs_the_add_page_cli(monkeypatch):
    from agf.discovery import keeper_daemon
    seen = {}
    monkeypatch.setattr(keeper_daemon.subprocess, "run",
                        lambda cmd, **kw: seen.setdefault("cmd", cmd))
    keeper_daemon.run_keeper_subprocess(7, 11, kind="add_page")
    assert "agf.discovery.add_page_one" in seen["cmd"]
    assert "--advertiser-id" in seen["cmd"] and "7" in seen["cmd"]


def test_keeper_kinds_still_run_keeper_one(monkeypatch):
    from agf.discovery import keeper_daemon
    seen = {}
    monkeypatch.setattr(keeper_daemon.subprocess, "run",
                        lambda cmd, **kw: seen.setdefault("cmd", cmd))
    keeper_daemon.run_keeper_subprocess(7, 11, kind="keeper_history")
    assert "agf.discovery.keeper_one" in seen["cmd"]
```

Adapt the monkeypatch target to however `run_keeper_subprocess` actually invokes
the subprocess — read the function body first.

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it \
  pytest tests/discovery/test_keeper_daemon.py -v
```
Expected: FAIL on the KINDS assertion.

- [ ] **Step 3: Implement**

Change `KINDS` and extend the claim-priority docstring:

```python
# Claim priority: a pasted-link add (someone is watching a spinner) beats a
# keeper's first history, which beats a corpus re-scrape.
KINDS = ("add_page", "keeper_history", "corpus_refresh")

ADD_PAGE_TIMEOUT_SEC = int(os.environ.get("AGF_ADD_PAGE_TIMEOUT_SEC", "600"))
```

In `run_keeper_subprocess`, branch on kind to choose the module, the args and
the subprocess timeout — `add_page` runs a full enrich so it needs the longer
budget, padded above the CLI's own SIGALRM exactly as the keeper path is:

```python
    if kind == "add_page":
        module, timeout = "agf.discovery.add_page_one", ADD_PAGE_TIMEOUT_SEC + 120
    else:
        module, timeout = "agf.discovery.keeper_one", KEEPER_ONE_TIMEOUT_SEC
```

and use `module`/`timeout` in the existing command construction, keeping the
`--advertiser-id` / `--job-id` args (both CLIs take the same pair) and dropping
the `--kind` argument for `add_page` (its CLI has no such flag).

The daemon's lease must outlast the longest subprocess. `LEASE_SEC` is derived
from `KEEPER_ONE_TIMEOUT_SEC`; change it to derive from the max:

```python
LEASE_SEC = max(KEEPER_ONE_TIMEOUT_SEC, ADD_PAGE_TIMEOUT_SEC) + 120
```

- [ ] **Step 4: Run to verify it passes**

Run: same pytest command as Step 2. Expected: PASS.

- [ ] **Step 5: Run the full Python suite bare**

Run:
```bash
AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it pytest
```
Expected: 0 failed. Pre-existing failures count — the project gate is zero
failing tests in the FULL suite. Read the full output before deciding; do not
pipe this through `tail`.

- [ ] **Step 6: Commit**

```bash
git add src/agf/discovery/keeper_daemon.py tests/discovery/test_keeper_daemon.py
git commit -m "feat(pipeline): keeper daemon claims add_page jobs at top priority"
```

---

### Task 6: Web — the Discover header control

**Files:**
- Create: `web/src/components/discover/AddFromAdLibrary.tsx`
- Modify: `web/src/lib/api.ts` (two client functions)
- Modify: `web/src/types/contracts.ts` (the `AddGemStatus` type)
- Modify: `web/src/components/discover/DiscoverFeed.tsx` (mount the control)

**Interfaces:**
- Consumes: `POST /api/gems/add`, `GET /api/gems/add/:job_id` (Task 3 — use
  whatever paths Task 3 actually registered).
- Produces:
  ```ts
  export type AddGemStatus = {
    job_id: number;
    advertiser_id: number;
    status: "queued" | "running" | "done" | "error";
    phase: string | null;
    page_name: string | null;
    icon_url: string | null;
    ad_count: number;
    triage_note: string | null;
    error_text: string | null;
    already_known: boolean;
  };
  export async function addGemFromLink(url: string): Promise<AddGemStatus>;
  export async function getAddGemStatus(jobId: number): Promise<AddGemStatus>;
  ```

- [ ] **Step 1: Add the type and the two client functions**

Follow the exact idiom of the neighbouring `postEnrich` / `getEnrichStatus` in
`web/src/lib/api.ts` (read them first — they carry the auth header and error
handling this codebase uses). `addGemFromLink` must surface the API's 422 detail
message to the caller rather than a generic failure, because that message is the
parser's guidance to the user.

- [ ] **Step 2: Build the control**

A button in the Discover header sized like the surface's existing primary
control — check what `FilterRail.tsx` / the page header already render and match
that size and weight. Not a text link (global CLAUDE.md).

Behaviour: clicking opens an inline paste field. Submitting calls
`addGemFromLink`, then polls `getAddGemStatus` every 2s until `done` or `error`,
rendering `phase` as it advances. On `done`: a result card with the page name,
active ad count, the triage note when present, and a link to
`/gems/<advertiser_id>`. On `error`: the message, plain, with the field still
populated so the user can fix and retry.

The job id lives in component state keyed so a navigation away and back does not
orphan a running job — mirror how the enrich refresh on the gem page handles
this rather than inventing a new mechanism.

Draft strings (final wording set in Task 7):
- Button: `Add from Ad Library`
- Placeholder: `Paste a Meta Ad Library link`
- Phases come from the server (`Pulling ads`, `Checking the store listing`,
  `Adding`, `Enriching`)
- Empty result: `This page is not running any ads in the US.`
- Already known: `Field Desk already had this advertiser. Refreshing it.`

- [ ] **Step 3: Mount it in the Discover header**

In `DiscoverFeed.tsx`, render `<AddFromAdLibrary />` in the header row beside the
existing controls. On success it should trigger the feed's existing refetch so
the new gem can appear without a manual reload.

- [ ] **Step 4: Run the web gates bare**

```bash
cd web && npx tsc --noEmit
cd web && npm run lint
```
Expected: both exit 0.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/discover/AddFromAdLibrary.tsx web/src/lib/api.ts \
        web/src/types/contracts.ts web/src/components/discover/DiscoverFeed.tsx
git commit -m "feat(web): add a gem from a pasted Ad Library link on /discover"
```

---

### Task 7: Copy pass + full-stack gates

**Files:**
- Modify: `backend/helpers/adlibrary.go` (the two error messages)
- Modify: `backend/controllers/add_gem_controller.go` (the `helpers.Detail` messages)
- Modify: `src/agf/discovery/add_page_one.py` (the phase labels and the note)
- Modify: `web/src/components/discover/AddFromAdLibrary.tsx` (all UI strings)

- [ ] **Step 1: Run the humanizer skill over every user-facing string**

Collect all of them into one list first: two parser errors, four controller
error details, four phase labels, the triage-note format, and every string in
the web component. Audit against the skill's surface and structural patterns,
then rewrite. Project rule: plain, clear, professional; no marketing fluff; keep
Field Desk / GemScore / cohort intact.

- [ ] **Step 2: Run an impeccable pass on the control itself**

The design questions this answers: does the empty state say what to do next,
does the error state let the user recover without retyping, is the running state
honest about how long this takes, and is the flagged-gem marker legible without
explaining the veto system.

- [ ] **Step 3: Update the tests the copy changes touch**

`adlibrary_test.go` asserts `Message != ""`, not exact text, so it survives.
Anything that asserts exact copy gets updated in the same commit.

- [ ] **Step 4: Run every gate bare**

```bash
cd backend && go test ./...
AGF_TEST_DB_URL=postgresql+psycopg://agf:agf@localhost:55433/agf_it pytest
cd web && npx tsc --noEmit
cd web && npm run lint
```
Each in its own call, exit code read directly. All four must pass before this
feature is reported done.

- [ ] **Step 5: Commit**

```bash
git commit -m "polish: copy pass over the add-from-Ad-Library flow"
```

---

### Task 8: Deploy and live end-to-end verification

A feature spanning API, DB, browser and a subprocess is not done until a live
run against real dependencies passes. Mock greens do not satisfy this gate.

- [ ] **Step 1: Apply migration 165 to prod**
- [ ] **Step 2: Build and deploy the worker image, then RECREATE the `keeper`
      container** — `KINDS` is read at import and `docker restart` reuses
      creation-time env. No new compose service is introduced, so there is
      nothing to `up -d` separately.
- [ ] **Step 3: Deploy the API**
- [ ] **Step 4: Deploy web**
- [ ] **Step 5: Live e2e** — paste a real Ad Library link for an advertiser not
      already in the corpus. Confirm: the phases advance, the job reaches
      `done`, `advertisers.manual_add` is TRUE, and `/gems/<id>` renders. Then
      paste a link for a page that triage vetoes and confirm it still appears
      with its note.
- [ ] **Step 6: Update project memory** with the shipped state and any landmines.

Deploy is the one step that may be blocked on interactive `gcloud` auth. If so,
land everything else and hand over the checklist rather than reporting done.

---

## Self-Review

**Spec coverage.** Link parsing → Task 2. API → Task 3. Worker job body → Task 4.
Daemon routing → Task 5. Force-keep across both vetoes → Task 1 (scoring veto,
via `manual_add` in the matview) and Task 4 (triage veto, recorded not enforced).
UI → Task 6. Copy rule → Task 7. Testing section → Tasks 2, 3, 4, 5 and the live
gate in Task 8. Deploy order → Task 8, matching the spec's ordering.

**Placeholders.** Three steps deliberately say "read the neighbouring file and
match it" rather than showing code: the Go row-scanning idiom (Task 3 Step 3),
the integration-test harness helpers (Task 3 Step 1), and the
`discovery.candidates` column names (Task 4 Step 4). These are not vague
instructions — each names the exact file to read and the exact thing to copy.
Guessing them would produce code that compiles against an invented interface,
which is the failure mode this plan is trying to avoid.

**Type consistency.** `AddPageJobRow` / `AddGemOut` fields are used identically
in Task 3's repo, controller and Task 6's TS type. `run(engine, advertiser_id,
job_id, *, refresh, triage, enrich, extractor, lookup_ios, lookup_android)` in
Task 4 matches every call in its tests. `KINDS` and
`run_keeper_subprocess(advertiser_id, job_id, kind)` in Task 5 match Task 4's
CLI flags (`--advertiser-id`, `--job-id`).

**Known risk, flagged not hidden.** Task 3 Step 5 may hit a gin route conflict
between `/gems/add` and an existing `/gems/:advertiser_id`. The plan names the
fallback paths and says to resolve it by running the tests.
