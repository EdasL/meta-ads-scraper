# Gem Feed App-Level Dedup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the gems feed from one-row-per-Meta-page to one-row-per-product (965 → ~870 rows), and add a detail-page section listing every Meta page fronting an app.

**Architecture:** A new product-grain SQL view `frontend.emerging_scaling_products` over the per-advertiser matview groups by a normalized name+developer key, keeps the highest-GemScore page as representative, and sums the ad-count footprint across pages. The Discover feed and the dossier's Gem read this view; the matview, scoring pipeline, and per-advertiser dossier reads are untouched. The review queue dedups by `app_id` in its serve-time assembler. The detail page gains a "Pages running this app" section listing all advertisers for the product across both stores.

**Tech Stack:** Postgres 17 (SQL view, migrations via `agf.db.migrate`), Go 1.x (Gin + GORM raw SQL, `backend/`), Next.js/React (`web/`), pytest (DB tests), `go test` (unit + `AGF_IT_DB_URL` integration), `node --test` (web pure-mapper tests).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-06-27-gem-feed-app-dedup-design.md`.
- **Product copy:** plain, clear, professional. No marketing words, no exclamation spam, no all-caps UI labels. Grounded in real data; never restate an adjacent number with a derived adjective. (`CLAUDE.md`)
- **page_count is display-only** — never a ranking signal or scored axis.
- **Commit to `main` directly** (solo, local-only repo). Stage **explicit paths** only — never `git add -A`/`.`.
- **Module root is `backend/`** for Go commands.
- **DB tests skip silently without their env var** — a skip is NOT a pass. Set `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it` for the pytest DB test and `AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it` for the Go integration/repo tests, and confirm the summary shows the test RAN (not `s`/`skipped`).
- **Standard is ZERO failing tests** in the full suite before done.
- **The product-key SQL is verified** against live prod: 965 → 870 rows, both cross-store pairs merge, over-merge check clean (max 1 distinct name per key, 0 false merges).

### Shared product-key function (single source of truth)

The normalized name+developer key is defined ONCE as an `IMMUTABLE` SQL function
`frontend.product_key(p_name, p_developer)` (created in Task 1) and reused by both
the view (Task 1) and the dossier pages query (Task 4). It returns
`'p:'||name_norm||'|'||dev_norm`:

- `name_norm = regexp_replace(lower(btrim(coalesce(p_name,''))), '[^a-z0-9]+', '', 'g')`
- `dev_norm = regexp_replace(regexp_replace(lower(btrim(coalesce(p_developer,''))), '\y(llc|inc|ltd|co|gmbh|corp|corporation|company|limited)\y\.?', '', 'g'), '[^a-z0-9]+', '', 'g')`

Verified on live prod: keying the feed by this signature yields 965 → 870 rows,
merges both cross-store pairs, and produces zero false merges (max 1 distinct
name per key).

---

### Task 1: Product-grain SQL view + structural test

**Files:**
- Create: `db/schema/142_emerging_products_view.sql`
- Create (test): `tests/db/test_matview_products.py`

**Interfaces:**
- Produces: view `frontend.emerging_scaling_products` with **every** column of `mvCols` (same names) where `active_ad_count`, `ads_seen`, `new_ads_4w`, `longrun_ad_count` are summed and `max_longevity_days` is the max across the product's pages, plus two new columns `page_count int` and `stores text` (`'ios'`/`'android'`/`'ios+android'`/NULL). One row per product; representative = highest `gem_score` page (`advertiser_id` is the representative's, so detail deep-links resolve).

- [ ] **Step 1: Write the migration file**

Create `db/schema/142_emerging_products_view.sql`:

```sql
-- Single source of truth for the product identity key: normalized app name +
-- developer (corp suffixes stripped). Used by the product view below AND by the
-- dossier "pages running this app" query, so both agree on what one product is.
CREATE OR REPLACE FUNCTION frontend.product_key(p_name text, p_developer text)
RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT 'p:'
      || regexp_replace(lower(btrim(coalesce(p_name, ''))), '[^a-z0-9]+', '', 'g')
      || '|'
      || regexp_replace(
           regexp_replace(lower(btrim(coalesce(p_developer, ''))),
             '\y(llc|inc|ltd|co|gmbh|corp|corporation|company|limited)\y\.?', '', 'g'),
           '[^a-z0-9]+', '', 'g');
$$;

-- App-level dedup for the gems feed. The per-advertiser matview
-- frontend.emerging_scaling_apps shows one row per Meta page, but one app is
-- routinely fronted by many pages (persona pages that read like a real person,
-- main-account shielding, geographic splits) and by both app stores — so ~10%
-- of feed rows were the same product repeated. This view collapses to one row
-- per product: the highest-GemScore page represents it, the ad-count footprint
-- is summed across pages, and iOS+Android listings of the same app merge by a
-- normalized name+developer key. The feed and the dossier's Gem read this view;
-- the matview is unchanged, so per-advertiser dossier/dossier-ads still work.
CREATE OR REPLACE VIEW frontend.emerging_scaling_products AS
WITH base AS (
  SELECT mv.*,
         CASE
           WHEN mv.app_name IS NULL OR btrim(mv.app_name) = '' THEN 'adv:' || mv.advertiser_id
           ELSE frontend.product_key(mv.app_name, ap.developer)
         END AS product_key
  FROM frontend.emerging_scaling_apps mv
  LEFT JOIN public.apps ap ON ap.id = mv.app_id
),
agg AS (
  SELECT product_key,
         sum(active_ad_count)     AS p_active_ad_count,
         sum(ads_seen)            AS p_ads_seen,
         sum(new_ads_4w)          AS p_new_ads_4w,
         sum(longrun_ad_count)    AS p_longrun_ad_count,
         max(max_longevity_days)  AS p_max_longevity_days,
         count(*)::int            AS page_count,
         CASE
           WHEN count(DISTINCT store) FILTER (WHERE store IS NOT NULL) > 1 THEN 'ios+android'
           ELSE max(store) FILTER (WHERE store IS NOT NULL)
         END                      AS stores
  FROM base
  GROUP BY product_key
),
ranked AS (
  SELECT base.*,
         row_number() OVER (
           PARTITION BY product_key
           ORDER BY gem_score DESC NULLS LAST, active_ad_count DESC NULLS LAST, advertiser_id
         ) AS rn
  FROM base
)
SELECT
  r.advertiser_id, r.cohort, r.page_name, r.app_id, r.app_name, r.icon_url,
  r.gem_score, r.scaling_velocity, r.under_radar, r.funnel_intent,
  a.p_active_ad_count    AS active_ad_count,
  a.p_max_longevity_days AS max_longevity_days,
  r.alert, r.first_count, r.latest_count, r.weeks_observed, r.pct_change,
  r.refreshed_at, r.first_ad_at,
  a.p_longrun_ad_count   AS longrun_ad_count,
  a.p_new_ads_4w         AS new_ads_4w,
  a.p_ads_seen           AS ads_seen,
  r.demand, r.whitespace, r.niche_keyword, r.niche_advertiser_count,
  r.installs_delta_28d, r.rating_count_delta_28d,
  r.installs, r.rating_count, r.rating, r.tagline,
  r.store, r.store_id, r.incubating, r.funnel_url, r.web_only,
  a.page_count,
  a.stores
FROM ranked r
JOIN agg a USING (product_key)
WHERE r.rn = 1;
```

- [ ] **Step 2: Write the failing structural test**

Create `tests/db/test_matview_products.py`:

```python
import os
import pytest
from sqlalchemy import text
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations

DB = os.environ.get("AGF_TEST_DB_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs AGF_TEST_DB_URL")


def test_products_view_shape_and_conservation():
    eng = get_engine(DB)
    apply_migrations(eng)
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = 'frontend.emerging_scaling_products'::regclass "
            "AND attnum > 0 AND NOT attisdropped"))}
        # the two new columns plus the identity columns the feed reads
        assert {"page_count", "stores"} <= cols
        assert {"advertiser_id", "app_name", "icon_url", "gem_score",
                "active_ad_count", "store", "store_id", "funnel_url", "web_only"} <= cols

        prod_rows = c.execute(text(
            "SELECT count(*) FROM frontend.emerging_scaling_products")).scalar()
        mv_rows = c.execute(text(
            "SELECT count(*) FROM frontend.emerging_scaling_apps")).scalar()
        pages_sum = c.execute(text(
            "SELECT coalesce(sum(page_count), 0) FROM frontend.emerging_scaling_products")).scalar()

    # Dedup never adds rows, and every matview row belongs to exactly one product,
    # so the page_counts sum back to the matview row count.
    assert prod_rows <= mv_rows
    assert pages_sum == mv_rows
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it .venv/bin/python -m pytest tests/db/test_matview_products.py -q`
Expected: FAIL — `relation "frontend.emerging_scaling_products" does not exist` (the migration hasn't been applied because the file is new but apply_migrations runs it; if it already applied, the failure is instead a missing-column/shape error). Confirm it RAN (not `skipped`).

- [ ] **Step 4: Apply the migration and re-run**

`apply_migrations` in Step 2 applies the new file. Re-run:
Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it .venv/bin/python -m pytest tests/db/test_matview_products.py -q`
Expected: PASS (`1 passed`).

- [ ] **Step 5: Apply to the live DB and spot-check the dedup**

Run:
```bash
AGF_DATABASE_URL="$(plutil -extract EnvironmentVariables.AGF_DATABASE_URL raw ~/Library/LaunchAgents/com.fielddesk.api.plist | sed 's/postgresql+psycopg/postgresql/')" \
PYTHONPATH=src .venv/bin/python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; print(apply_migrations(get_engine()))"
```
Expected: prints `['142_emerging_products_view.sql']` (or `[]` if already applied).
Then confirm the row count with a one-off query (reuse the probe DSN): `SELECT count(*) FROM frontend.emerging_scaling_products;` → ~870 (vs 965 in the matview).

- [ ] **Step 6: Commit**

```bash
git add db/schema/142_emerging_products_view.sql tests/db/test_matview_products.py
git commit -m "feat(db): product-grain view collapsing per-advertiser gem rows"
```

---

### Task 2: Backend feed reads the product view (+ footprint tiebreak)

These changes are coupled: `mvCols` gains two columns that exist only in the view, and every consumer that scans `mvCols` (the shortlist feed and the dossier Gem) must read the view in the same change or fail to scan. The dossier's other reads stay on `public.ads`/advertisers by `advertiser_id` (unchanged).

**Files:**
- Modify: `backend/repositories/repositories.go:21-27` (mvCols)
- Modify: `backend/models/models.go:123` (add fields to ShortlistRow)
- Modify: `backend/repositories/shortlist_repo.go` (5 view swaps + orderClause/foryou tiebreak)
- Modify: `backend/repositories/dossier_repo.go:57` (Gem read → view)
- Create (test): `backend/repositories/shortlist_repo_dedup_test.go`

**Interfaces:**
- Consumes: `frontend.emerging_scaling_products` (Task 1).
- Produces: `models.ShortlistRow.PageCount int`, `models.ShortlistRow.Stores *string`; `repositories.Shortlist` now returns one row per product with summed footprint, ordered by the sort key then `active_ad_count DESC`.

- [ ] **Step 1: Write the failing hermetic dedup test**

Create `backend/repositories/shortlist_repo_dedup_test.go` (package `repositories_test` — reuses `TestMain`, `must`, `requireRatingsDB` from `ratings_repo_test.go`):

```go
// Package repositories_test: app-level dedup of the gems feed (the product
// view). Skipped unless AGF_IT_DB_URL is set. Seeds its own sentinel cohort so
// it never collides with the ratings fixture in the same package.
package repositories_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

const dedupCohort = "DEDUP_TEST"

// seedDedup builds one product fronted by 4 Meta pages across both stores:
//   - iOS listing "Dedup App" / dev "Dedup Co"      → pages P1(score 90), P2(80), P3(70)
//   - Android listing "Dedup App" / dev "Dedup Co LLC" → page P4(85)
// The normalized name+dev key merges all four into one product; P1 (top score)
// is the representative.
func seedDedup(g *gorm.DB) {
	// FK-safe teardown of any prior run (sentinel page_ids + apps).
	must(g.Exec(`DELETE FROM public.creatives WHERE ad_id IN (
		SELECT a.id FROM public.ads a JOIN public.advertisers adv ON adv.id = a.advertiser_id
		WHERE adv.page_id LIKE 'DEDUP_P%')`).Error)
	must(g.Exec(`DELETE FROM public.ads WHERE advertiser_id IN (
		SELECT id FROM public.advertisers WHERE page_id LIKE 'DEDUP_P%')`).Error)
	must(g.Exec(`DELETE FROM adtech.advertiser_scores WHERE advertiser_id IN (
		SELECT id FROM public.advertisers WHERE page_id LIKE 'DEDUP_P%')`).Error)
	must(g.Exec(`DELETE FROM public.advertisers WHERE page_id LIKE 'DEDUP_P%'`).Error)
	must(g.Exec(`DELETE FROM public.apps WHERE store_id IN ('ded-ios', 'ded-android')`).Error)

	var iosApp, androidApp int
	must(g.Raw(`INSERT INTO public.apps (store, store_id, name, developer, is_emerging)
		VALUES ('ios','ded-ios','Dedup App','Dedup Co',true) RETURNING id`).Scan(&iosApp).Error)
	must(g.Raw(`INSERT INTO public.apps (store, store_id, name, developer, is_emerging)
		VALUES ('android','ded-android','Dedup App','Dedup Co LLC',true) RETURNING id`).Scan(&androidApp).Error)

	mk := func(page string, appID, score, ads int) {
		var advID int
		must(g.Raw(`INSERT INTO public.advertisers (page_id, page_name)
			VALUES (?, ?) RETURNING id`, page, "Dedup "+page).Scan(&advID).Error)
		var adID int
		must(g.Raw(`INSERT INTO public.ads (external_ad_id, advertiser_id, app_id, is_active, start_date)
			VALUES (?, ?, ?, true, now() - interval '5 days') RETURNING id`,
			page+"-AD", advID, appID).Scan(&adID).Error)
		must(g.Exec(`INSERT INTO public.creatives (ad_id, type, s3_key)
			VALUES (?, 'image', ?)`, adID, "creatives/"+page+".jpg").Error)
		must(g.Exec(`INSERT INTO adtech.advertiser_scores
			(advertiser_id, cohort, gem_score, active_ad_count, max_longevity_days, alert)
			VALUES (?, ?, ?, ?, 30, false)`, advID, dedupCohort, float64(score), ads).Error)
	}
	mk("DEDUP_P1", iosApp, 90, 3)
	mk("DEDUP_P2", iosApp, 80, 2)
	mk("DEDUP_P3", iosApp, 70, 2)
	mk("DEDUP_P4", androidApp, 85, 4)

	must(g.Exec(`REFRESH MATERIALIZED VIEW frontend.emerging_scaling_apps`).Error)
}

func TestShortlistCollapsesProduct(t *testing.T) {
	requireRatingsDB(t)
	seedDedup(ratingsDB)
	ctx := context.Background()

	cohort := dedupCohort
	rows, total, err := repositories.Shortlist(ctx, ratingsDB, repositories.ShortlistParams{
		Cohort: &cohort, Limit: 50, Offset: 0,
	})
	require.NoError(t, err)
	require.Equal(t, 1, total, "4 pages across 2 stores collapse to one product")
	require.Len(t, rows, 1)

	row := rows[0]
	require.EqualValues(t, 90, row.GemScore, "highest-score page represents the product")
	require.NotNil(t, row.ActiveAdCount)
	require.Equal(t, 3+2+2+4, *row.ActiveAdCount, "footprint summed across all pages")
	require.Equal(t, 4, row.PageCount)
	require.NotNil(t, row.Stores)
	require.Equal(t, "ios+android", *row.Stores)
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./repositories/ -run TestShortlistCollapsesProduct -v`
Expected: FAIL — `ShortlistRow` has no field `PageCount`/`Stores` (compile error), or the scan errors on the missing columns.

- [ ] **Step 3: Add the model fields**

In `backend/models/models.go`, after the `WebOnly` field (line 123) and before the `Explainer` enriched block (line 124), add:

```go
	// Product-level dedup (142_emerging_products_view.sql). page_count = distinct
	// Meta pages fronting this app; stores = "ios" / "android" / "ios+android".
	// Display-only — running one app from many pages is a common ad technique,
	// not a quality signal.
	PageCount int     `json:"page_count" gorm:"column:page_count"`
	Stores    *string `json:"stores" gorm:"column:stores"`
```

- [ ] **Step 4: Add the columns to mvCols**

In `backend/repositories/repositories.go`, change the final line of `mvCols` (line 27) from:

```go
	`store, store_id, incubating, funnel_url, web_only`
```

to:

```go
	`store, store_id, incubating, funnel_url, web_only, page_count, stores`
```

- [ ] **Step 5: Swap the shortlist reads to the view + add the footprint tiebreak**

In `backend/repositories/shortlist_repo.go`, replace every `frontend.emerging_scaling_apps` with `frontend.emerging_scaling_products` in these five places:
- the `foryou` correlation (line 66): `AND ps.advertiser_id = frontend.emerging_scaling_products.advertiser_id`
- the category `EXISTS` (line 119): `WHERE public.apps.id = frontend.emerging_scaling_products.app_id`
- the count query (line 166): `SELECT count(*) FROM frontend.emerging_scaling_products WHERE `
- the page query (line 172): `SELECT `+mvCols+` FROM frontend.emerging_scaling_products`
- the category-count `FROM`/join (lines 202-203): `FROM frontend.emerging_scaling_products` and `JOIN public.apps ON public.apps.id = frontend.emerging_scaling_products.app_id`

Then add the summed-footprint tiebreak. In `orderClause` (line 96), change:

```go
	return clause + ", advertiser_id"
```

to:

```go
	// active_ad_count is the summed footprint in the product view; a many-page
	// app sorts above an equal-score single-page app.
	return clause + ", active_ad_count DESC NULLS LAST, advertiser_id"
```

And in `buildShortlistOrder` (line 64-67), change the `foryou` clause tail from `DESC NULLS LAST, advertiser_id` to:

```go
		         DESC NULLS LAST, active_ad_count DESC NULLS LAST, advertiser_id`, []any{p.UserEmail}
```

- [ ] **Step 6: Swap the dossier Gem read to the view**

In `backend/repositories/dossier_repo.go` (line 57), change:

```go
			`SELECT `+mvCols+` FROM frontend.emerging_scaling_apps WHERE advertiser_id = ?`,
```

to:

```go
			`SELECT `+mvCols+` FROM frontend.emerging_scaling_products WHERE advertiser_id = ?`,
```

(So the detail header shows product totals + carries `page_count`/`stores`/representative `app_id`. Feed links always use the representative `advertiser_id`, which is the view's row key.)

- [ ] **Step 7: Run the dedup test + the existing integration suite**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./repositories/ -run TestShortlistCollapsesProduct -v`
Expected: PASS.

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./... 2>&1`
Expected: PASS — in particular `internal/integration` `TestShortlist` still sees **3** rows (each seeded advertiser is its own product) and `TestGemDossier` still resolves. Read the full output; confirm no `FAIL` and that DB tests ran (not all skipped).

- [ ] **Step 8: Commit**

```bash
git add backend/repositories/repositories.go backend/models/models.go \
        backend/repositories/shortlist_repo.go backend/repositories/dossier_repo.go \
        backend/repositories/shortlist_repo_dedup_test.go
git commit -m "feat(feed): serve the gems feed from the product-grain view"
```

---

### Task 3: Review queue dedups by app_id

**Files:**
- Modify: `backend/repositories/preference_queue.go` (assembleQueue)
- Modify: `backend/repositories/preference_queue_test.go` (add a test)

**Interfaces:**
- Consumes: `prefCandidate` (embeds `models.PreferenceCardRow` → `ShortlistRow`, so `c.AppID *int` is available).
- Produces: `assembleQueue` emits at most one card per non-null `app_id`.

- [ ] **Step 1: Write the failing test**

Add to `backend/repositories/preference_queue_test.go` (package `repositories`, white-box). Mirror the existing helpers in that file (`cand`, `baseOpts`); if their signatures differ, adapt the construction to set `Source`, `PLike`, and `AppID`. This test builds three candidates where two share `app_id = 100`:

```go
func TestAssembleQueueDedupsByAppID(t *testing.T) {
	appA, appB := 100, 200
	mk := func(adv int, app *int, plike float64) prefCandidate {
		c := prefCandidate{Source: "scored", PLike: plike}
		c.AdvertiserID = adv
		c.AppID = app
		c.Cohort = "ios"
		return c
	}
	cands := []prefCandidate{
		mk(1, &appA, 0.9), // same app as #2, higher p_like → this one wins
		mk(2, &appA, 0.5),
		mk(3, &appB, 0.7),
	}
	got := assembleQueue(cands, queueOpts{
		Limit: 10, PerNicheCap: 10, ExploitFrac: 1, UncertainFrac: 0,
		Rng: rand.New(rand.NewSource(1)),
	})
	apps := map[int]int{}
	for _, c := range got {
		if c.AppID != nil {
			apps[*c.AppID]++
		}
	}
	require.LessOrEqual(t, apps[appA], 1, "at most one card per app_id")
	require.Len(t, got, 2, "two distinct apps → two cards")
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && go test ./repositories/ -run TestAssembleQueueDedupsByAppID -v`
Expected: FAIL — both `app_id = 100` cards are returned (`got` has length 3 / `apps[100] == 2`).

- [ ] **Step 3: Add the app_id dedup guard**

In `backend/repositories/preference_queue.go`, in `assembleQueue`, add a `seenApp` set next to `seenAdv` (after line 148):

```go
	picked := make([]prefCandidate, 0, opts.Limit)
	seenAdv := map[int]bool{}
	seenApp := map[int]bool{} // collapse multiple Meta pages of the same app
	nicheCount := map[string]int{}
```

Then in the `tryTake` closure, after the existing `seenAdv` skip (after line 159), add the app_id skip:

```go
			if seenAdv[c.AdvertiserID] {
				continue
			}
			if c.AppID != nil && seenApp[*c.AppID] {
				continue
			}
```

And record it where `seenAdv` is set (after line 164):

```go
			seenAdv[c.AdvertiserID] = true
			if c.AppID != nil {
				seenApp[*c.AppID] = true
			}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && go test ./repositories/ -run TestAssembleQueueDedupsByAppID -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/repositories/preference_queue.go backend/repositories/preference_queue_test.go
git commit -m "feat(review): dedup the review queue by app_id"
```

---

### Task 4: Dossier "Pages running this app"

**Files:**
- Modify: `backend/models/models.go` (add `AdvertiserPage`, `GemDossier.Pages`)
- Modify: `backend/repositories/dossier_repo.go` (pages query in `Dossier`)
- Modify: `backend/repositories/shortlist_repo_dedup_test.go` (assert pages on the same fixture)

**Interfaces:**
- Consumes: the dossier's representative `app_id` (from `gems[0].AppID`); the Task 1 shared normalized-key SQL.
- Produces: `models.AdvertiserPage{AdvertiserID, PageID, PageName, PageCategory, ActiveAds, TotalAds, FirstAd, LastAd}`; `GemDossier.Pages []AdvertiserPage` (JSON `pages`, always non-nil — `[]` when the product has one page).

- [ ] **Step 1: Write the failing assertion (reuses the Task 2 fixture)**

Append to `backend/repositories/shortlist_repo_dedup_test.go`:

```go
func TestDossierListsAllPages(t *testing.T) {
	requireRatingsDB(t)
	seedDedup(ratingsDB)
	ctx := context.Background()

	// Representative of the Dedup product is the top-score iOS page (P1). Find it.
	cohort := dedupCohort
	rows, _, err := repositories.Shortlist(ctx, ratingsDB, repositories.ShortlistParams{
		Cohort: &cohort, Limit: 1,
	})
	require.NoError(t, err)
	require.Len(t, rows, 1)

	d, err := repositories.Dossier(ctx, ratingsDB, rows[0].AdvertiserID)
	require.NoError(t, err)
	require.NotNil(t, d)
	require.Len(t, d.Pages, 4, "all four pages across both stores")

	// Sorted by active ads desc — P4 (4 active) leads.
	require.GreaterOrEqual(t, d.Pages[0].ActiveAds, d.Pages[1].ActiveAds)
	for _, p := range d.Pages {
		require.NotEmpty(t, p.PageName)
		require.GreaterOrEqual(t, p.TotalAds, 1)
	}
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./repositories/ -run TestDossierListsAllPages -v`
Expected: FAIL — `d.Pages` undefined (compile error).

- [ ] **Step 3: Add the model types**

In `backend/models/models.go`, add a struct before `GemDossier` (around line 248):

```go
// AdvertiserPage is one Meta page that runs ads for a product, with its ad
// footprint. The detail page lists every page fronting an app (across both
// stores) so a human can see the whole spread and open each Ad Library page.
type AdvertiserPage struct {
	AdvertiserID int      `json:"advertiser_id" gorm:"column:advertiser_id"`
	PageID       *string  `json:"page_id" gorm:"column:page_id"`
	PageName     *string  `json:"page_name" gorm:"column:page_name"`
	PageCategory *string  `json:"page_category" gorm:"column:page_category"`
	ActiveAds    int      `json:"active_ads" gorm:"column:active_ads"`
	TotalAds     int      `json:"total_ads" gorm:"column:total_ads"`
	FirstAd      *APIDate `json:"first_ad" gorm:"column:first_ad"`
	LastAd       *APIDate `json:"last_ad" gorm:"column:last_ad"`
}
```

Then add a field to `GemDossier` (after `MetricChange4w`, line 270):

```go
	MetricChange4w         *MetricChange4w `json:"metric_change_4w"`
	// Every Meta page fronting this app, across both stores (always non-nil; one
	// entry when the product has a single page). 142_emerging_products_view.sql.
	Pages                  []AdvertiserPage `json:"pages"`
```

- [ ] **Step 4: Add the pages query to Dossier**

In `backend/repositories/dossier_repo.go`, inside `Dossier`, after the explainer read (after line 255, before `d := &models.GemDossier{...}`), add:

```go
		// Pages running this app: every advertiser whose ads point at any store
		// listing in this product (same frontend.product_key as the representative
		// app — the iOS and Android siblings merge). One row per page with its ad
		// footprint. Uses the SAME key function as the product view, so the page
		// set matches the feed's collapse exactly.
		pages := make([]models.AdvertiserPage, 0)
		if gems[0].AppID != nil {
			if err := tx.Raw(
				`SELECT adv.id AS advertiser_id, adv.page_id, adv.page_name, adv.page_category,
				        count(a.id)                            AS total_ads,
				        count(a.id) FILTER (WHERE a.is_active) AS active_ads,
				        min(a.start_date)::date                AS first_ad,
				        max(a.end_date)::date                  AS last_ad
				 FROM public.ads a
				 JOIN public.advertisers adv ON adv.id = a.advertiser_id
				 WHERE a.app_id IN (
				   SELECT ap.id FROM public.apps ap
				   WHERE frontend.product_key(ap.name, ap.developer)
				       = (SELECT frontend.product_key(name, developer)
				          FROM public.apps WHERE id = ?)
				 )
				 GROUP BY adv.id, adv.page_id, adv.page_name, adv.page_category
				 ORDER BY active_ads DESC NULLS LAST, total_ads DESC`,
				*gems[0].AppID,
			).Scan(&pages).Error; err != nil {
				return err
			}
		}
```

Then add `Pages: pages,` to the `GemDossier` literal (in the `d := &models.GemDossier{...}` block, line 257-263):

```go
		d := &models.GemDossier{
			Gem:       gems[0],
			Apps:      apps,
			Funnels:   funnels,
			Creatives: creatives,
			Growth:    gems[0].Growth,
			Pages:     pages,
		}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./repositories/ -run 'TestDossierListsAllPages|TestShortlistCollapsesProduct' -v`
Expected: PASS (both).

- [ ] **Step 6: Run the full Go suite**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./... 2>&1`
Expected: PASS, no `FAIL`, DB tests ran.

- [ ] **Step 7: Commit**

```bash
git add backend/models/models.go backend/repositories/dossier_repo.go \
        backend/repositories/shortlist_repo_dedup_test.go
git commit -m "feat(detail): list every Meta page fronting an app in the dossier"
```

---

### Task 5: Web contract + adapters

**Files:**
- Modify: `web/src/types/api.ts` (ApiShortlistRow, ApiGemDossier, + ApiAdvertiserPage)
- Modify: `web/src/types/contracts.ts` (ShortlistRow, GemDossier, + AdvertiserPage)
- Modify: `web/src/lib/adapters.ts` (adaptShortlistRow, adaptDossier)
- Modify: `web/src/lib/adapters.test.ts` (assert the new fields map)

**Interfaces:**
- Consumes: the JSON from Tasks 2 & 4 (`page_count`, `stores` on rows; `pages[]` on the dossier).
- Produces: `ShortlistRow.page_count?`, `ShortlistRow.stores?`, `GemDossier.pages: AdvertiserPage[]` for the components.

- [ ] **Step 1: Add the API wire types**

In `web/src/types/api.ts`, add to `ApiShortlistRow` (after `funnel_url`, line 76):

```typescript
  // Product-level dedup (matview migration 142). page_count = Meta pages
  // fronting this app; stores = "ios" | "android" | "ios+android".
  page_count?: number | null;
  stores?: string | null;
```

Add a new interface and extend `ApiGemDossier` (after `metric_change_4w`, line 155):

```typescript
  pages?: ApiAdvertiserPage[];
```

And near the other Api* interfaces:

```typescript
export interface ApiAdvertiserPage {
  advertiser_id: number;
  page_id: string | null;
  page_name: string | null;
  page_category: string | null;
  active_ads: number;
  total_ads: number;
  first_ad: string | null;
  last_ad: string | null;
}
```

- [ ] **Step 2: Add the UI contract types**

In `web/src/types/contracts.ts`, add to `ShortlistRow` (after `web_only`, line 78):

```typescript
  page_count?: number; // Meta pages fronting this app (display-only)
  stores?: "ios" | "android" | "ios+android"; // which stores the product is on
```

Add to `GemDossier` (after `meta_active_ads_checked_at`, line 224):

```typescript
  /** Every Meta page fronting this app, across both stores. */
  pages: AdvertiserPage[];
```

And a new interface:

```typescript
export interface AdvertiserPage {
  advertiser_id: number;
  page_id: string | null;
  page_name: string | null;
  page_category: string | null;
  active_ads: number;
  total_ads: number;
  first_ad: string | null;
  last_ad: string | null;
}
```

- [ ] **Step 3: Map them in the adapters**

In `web/src/lib/adapters.ts`, in `adaptShortlistRow` (before the closing `};`, after `funnel_url`, line 142):

```typescript
    funnel_url: r.funnel_url ?? undefined,
    page_count: r.page_count ?? undefined,
    stores:
      r.stores === "ios+android" ? "ios+android"
      : r.stores === "ios" ? "ios"
      : r.stores === "android" ? "android"
      : undefined,
```

In `adaptDossier`, in the returned object (after `metric_change_4w`, line 315):

```typescript
    metric_change_4w: adaptMetricChange4w(d.metric_change_4w),
    pages: (d.pages ?? []).map((p) => ({
      advertiser_id: p.advertiser_id,
      page_id: p.page_id,
      page_name: p.page_name,
      page_category: p.page_category,
      active_ads: num(p.active_ads),
      total_ads: num(p.total_ads),
      first_ad: p.first_ad,
      last_ad: p.last_ad,
    })),
```

(Import `ApiAdvertiserPage` is unnecessary — `d.pages` is already typed via `ApiGemDossier`.)

- [ ] **Step 4: Write the failing adapter test**

In `web/src/lib/adapters.test.ts`, add a test (use the file's existing fixture style — extend the dossier/shortlist fixtures with the new fields):

```typescript
test("adaptShortlistRow maps page_count and stores", () => {
  const row = adaptShortlistRow(
    { advertiser_id: 1, cohort: "ios", page_name: "P", app_id: 5, app_name: "A",
      gem_score: 70, scaling_velocity: 0, under_radar: 0, funnel_intent: 0,
      active_ad_count: 9, max_longevity_days: 10, alert: false, refreshed_at: null,
      page_count: 4, stores: "ios+android" } as never,
    (k: string) => k,
  );
  assert.equal(row.page_count, 4);
  assert.equal(row.stores, "ios+android");
});

test("adaptDossier defaults pages to [] when absent", () => {
  // Build a minimal dossier fixture matching the file's existing pattern, with
  // no `pages` key, and assert the adapted result has pages === [].
  // (Reuse the dossier fixture already defined above in this test file.)
});
```

Fill the second test's body using the dossier fixture already present in `adapters.test.ts` (call `adaptDossier(fixture, [], [], mediaUrl)` and `assert.deepEqual(result.pages, [])`).

- [ ] **Step 5: Run the web tests + typecheck + lint**

Run: `cd web && node --test src/lib/adapters.test.ts && npx tsc --noEmit && npm run lint`
Expected: tests PASS, `tsc` clean, eslint clean.

- [ ] **Step 6: Commit**

```bash
git add web/src/types/api.ts web/src/types/contracts.ts web/src/lib/adapters.ts web/src/lib/adapters.test.ts
git commit -m "feat(web): carry page_count/stores/pages through the web contract"
```

---

### Task 6: Feed badges in GemTable

**Files:**
- Modify: `web/src/components/discover/GemTable.tsx` (badges next to "Web only")

**Interfaces:**
- Consumes: `row.page_count`, `row.stores` (Task 5).

- [ ] **Step 1: Add the badges**

In `web/src/components/discover/GemTable.tsx`, in the name cell, replace the `web_only` badge block (lines 144-148) with the web_only badge plus the two new badges. The new badges sit in the same baseline row, use the same muted `t-xs` style, and render only when meaningful:

```tsx
          {row.web_only && (
            <span className="t-xs" style={{ color: "var(--muted)", whiteSpace: "nowrap" }}>
              Web only
            </span>
          )}
          {row.page_count !== undefined && row.page_count > 1 && (
            <span className="t-xs" style={{ color: "var(--muted)", whiteSpace: "nowrap" }}
                  title="Meta pages running ads for this app">
              {row.page_count} pages
            </span>
          )}
          {row.stores === "ios+android" && (
            <span className="t-xs" style={{ color: "var(--muted)", whiteSpace: "nowrap" }}>
              iOS + Android
            </span>
          )}
```

- [ ] **Step 2: Typecheck, lint, build**

Run: `cd web && npx tsc --noEmit && npm run lint && npm run build`
Expected: all clean.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/discover/GemTable.tsx
git commit -m "feat(web): show page-count and cross-store badges in the feed"
```

---

### Task 7: "Pages running this app" detail section

**Files:**
- Create: `web/src/components/detail/PagesSection.tsx`
- Modify: `web/src/components/detail/GemDetail.tsx` (import + render after AdsSection)

**Interfaces:**
- Consumes: `gem.pages` (Task 5), `adLibraryPageUrl` from `web/src/lib/links.ts`, `monthLabel` (date formatter already used in `GemTable.tsx`; import from its source).

- [ ] **Step 1: Create the component**

Create `web/src/components/detail/PagesSection.tsx`:

```tsx
import type { GemDossier } from "@/types/contracts";
import { adLibraryPageUrl } from "@/lib/links";
import { monthLabel } from "@/lib/why";

// Lists every Meta page running ads for this app, across both stores. Running
// one app from many pages is a common ad technique (persona pages, geographic
// splits, keeping volume off the main account), so this is informational — it
// shows the whole spread behind one gem, not a quality score.
export function PagesSection({ gem }: { gem: GemDossier }) {
  const pages = gem.pages ?? [];
  if (pages.length <= 1) return null;

  return (
    <section>
      <h2 className="t-sm w-strong" style={{ marginBottom: 10 }}>
        Pages running this app ({pages.length})
      </h2>
      <table className="w-table">
        <thead>
          <tr>
            <th>Page</th>
            <th className="num">Ads (active / total)</th>
            <th>Running</th>
            <th aria-label="Ad Library" />
          </tr>
        </thead>
        <tbody>
          {pages.map((p) => (
            <tr key={p.advertiser_id}>
              <td>
                <span className="w-strong">{p.page_name ?? "Unnamed page"}</span>
                {p.page_category && (
                  <span className="t-xs" style={{ color: "var(--muted)", marginLeft: 8 }}>
                    {p.page_category}
                  </span>
                )}
              </td>
              <td className="num nums">
                {p.active_ads} / {p.total_ads}
              </td>
              <td className="t-xs nums" style={{ whiteSpace: "nowrap" }}>
                {monthLabel(p.first_ad) ?? "—"} – {monthLabel(p.last_ad) ?? "—"}
              </td>
              <td>
                {p.page_id && (
                  <a className="t-xs" href={adLibraryPageUrl(p.page_id)} target="_blank" rel="noreferrer">
                    Ad Library
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

(`monthLabel` is exported from `@/lib/why` — same import `GemTable.tsx` uses. If the `w-table`/`nums` classes differ from the codebase's table styles, match the classes used by the dossier's other tables.)

- [ ] **Step 2: Wire it into the detail page**

In `web/src/components/detail/GemDetail.tsx`, add the import (after the `GemNotes` import, line 16):

```tsx
import { PagesSection } from "./PagesSection";
```

And render it after the `AdsSection` block (after lines 110-112), before `AppStorePreview`:

```tsx
    <div style={{ marginTop: 36 }}>
      <AdsSection gem={gem} />
    </div>

    <div style={{ marginTop: 36 }}>
      <PagesSection gem={gem} />
    </div>

    <div style={{ marginTop: 36 }}>
      <AppStorePreview gem={gem} />
    </div>
```

- [ ] **Step 3: Typecheck, lint, build**

Run: `cd web && npx tsc --noEmit && npm run lint && npm run build`
Expected: all clean.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/detail/PagesSection.tsx web/src/components/detail/GemDetail.tsx
git commit -m "feat(detail): add the Pages-running-this-app section"
```

---

## Live verification (after all tasks)

1. **Deploy the API binary** (per ops/RUNBOOK / the project's fullstack-field-add pattern): build from a clean `git worktree add … HEAD`, `mv` into `~/bin/agf-api`, `launchctl kickstart com.fielddesk.api`. The view migration is already applied (Task 1 Step 5).
2. **Feed count:** `GET /api/shortlist` with a high limit → `X-Total-Count` ≈ **870** (was 965).
3. **Spot-check a merged product:** *My General RV* appears **once**, with `stores: "ios+android"`, `page_count` ≈ 20, and a summed `active_ad_count`. *Football Results & Fixtures* appears once with `page_count` 5.
4. **No false merge:** a known same-normalized-name / different-developer pair still appears as two rows.
5. **Detail page (Playwright or browser):** open *My General RV* → the "Pages running this app" section lists ~20 pages across both stores (geographic "General RV Center …" names), each with active/total ad counts, a running-dates range, and a working Ad Library link.
6. **Review queue:** `GET /api/preference-queue` returns no two cards with the same `app_id`.

## Self-review notes

- **Spec coverage:** product view (Task 1) ✔; best-page score + summed footprint + tiebreak (Tasks 1–2) ✔; name+dev key with corp-suffix strip (Tasks 1, 4) ✔; page_count/stores badges (Task 6) ✔; queue app_id dedup (Task 3) ✔; detail Pages section (Tasks 4, 7) ✔; web contract (Task 5) ✔.
- **Key DRY:** the product identity is a single `IMMUTABLE` function `frontend.product_key(name, developer)` (Task 1), reused by the view (Task 1) and the dossier pages query (Task 4) — no duplicated logic block; both collapse the same products by construction.
- **Pagination:** dedup is in the view (before `LIMIT/OFFSET`), so `X-Total-Count` and paging stay correct.
- **Blast radius:** the matview, scoring pipeline, and per-advertiser dossier sub-reads (ads, growth, lifecycle, enrichment) are unchanged; only the feed/category reads and the dossier Gem read move to the view.
```
