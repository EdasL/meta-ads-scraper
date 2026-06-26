# Review Queue Diversity + Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `/review` fetch return a diverse, non-repeating batch that blends the user's taste with uncertain and brand-new apps, killing the filter-bubble repeats.

**Architecture:** Move queue selection from frozen scoring-time `queue_rank` to per-request serve-time selection in the Go API. A wide candidate pool (all unrated scored apps ∪ top fresh-unscored apps) is fetched by SQL, then a pure, DB-free `assembleQueue` function blends three buckets (exploit / uncertain / fresh ≈ 60/25/15), caps cards per niche, and randomizes per request. Enrichment runs only on the final selection.

**Tech Stack:** Go 1.x, Gin, GORM (raw SQL mode), Postgres, testify. Python scorer unchanged.

## Global Constraints

- All work and commits on `main`, no feature branches (solo, local-only).
- Reads run inside `db.WithReadTx`; this query is read-only — keep it that way.
- No schema change to `frontend.preference_scores` or `frontend.app_ratings`.
- No frontend change (`ReviewDeck.tsx`), no scorer change (`score_content_preferences.py`), no route change.
- Diversity axis is `niche_keyword` (`ShortlistRow.NicheKeyword *string`); when NULL, fall back to `cohort` for the cap key.
- Rating input stays `like` / `neutral` / `dislike`.
- Go integration tests are gated on `AGF_IT_DB_URL` and use the package-level `ratingsDB` handle (`TestMain` in `backend/repositories/ratings_repo_test.go`).
- Run unit tests: `go test ./backend/repositories/... -run AssembleQueue -v`
- Run integration tests: `AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./backend/repositories/... -v`
- Commit message footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `backend/repositories/preference_queue.go` | `prefCandidate` type, `queueOpts`, constants, pure `assembleQueue` |
| Create | `backend/repositories/preference_queue_test.go` | unit tests for `assembleQueue` (no DB) |
| Modify | `backend/repositories/preference_repo.go` | candidate-pool SQL (scored ∪ fresh), call `assembleQueue`, enrich final selection |
| Modify | `backend/repositories/preference_repo_test.go` | update integration test for fresh-unscored inclusion + diversity properties |

The `prefScanRow` type currently in `preference_repo.go` is replaced by the
candidate scan target defined in Task 1.

---

## Task 1: Pure `assembleQueue` blend/cap/interleave

**Files:**
- Create: `backend/repositories/preference_queue.go`
- Test: `backend/repositories/preference_queue_test.go`

**Interfaces:**
- Consumes: `models.PreferenceCardRow` (embeds `models.ShortlistRow` with `AdvertiserID int`, `NicheKeyword *string`; plus `PLike float64`, `Why string`).
- Produces:
  - `type prefCandidate struct { models.PreferenceCardRow; Uncertainty float64; Source string }` (`Source` is `"scored"` or `"fresh"`).
  - `type queueOpts struct { Limit, PerNicheCap int; ExploitFrac, UncertainFrac float64; Rng *rand.Rand }`
  - `func assembleQueue(cands []prefCandidate, opts queueOpts) []prefCandidate`
  - Constants `defaultPerNicheCap = 3`, `exploitFrac = 0.60`, `uncertainFrac = 0.25`, `poolMultiplier = 4`.

- [ ] **Step 1: Write the failing tests**

Create `backend/repositories/preference_queue_test.go`:

```go
package repositories

import (
	"math/rand"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

func cand(id int, niche string, pLike, unc float64, source string) prefCandidate {
	n := niche
	c := prefCandidate{Uncertainty: unc, Source: source}
	c.AdvertiserID = id
	c.NicheKeyword = &n
	c.PLike = pLike
	return c
}

func ids(cs []prefCandidate) []int {
	out := make([]int, len(cs))
	for i, c := range cs {
		out[i] = c.AdvertiserID
	}
	return out
}

func baseOpts(limit int, seed int64) queueOpts {
	return queueOpts{
		Limit:         limit,
		PerNicheCap:   defaultPerNicheCap,
		ExploitFrac:   exploitFrac,
		UncertainFrac: uncertainFrac,
		Rng:           rand.New(rand.NewSource(seed)),
	}
}

func TestAssembleQueue_ExcludesNothingAddedAndDedupesAdvertisers(t *testing.T) {
	cands := []prefCandidate{
		cand(1, "a", 0.9, 0.2, "scored"),
		cand(2, "b", 0.8, 0.3, "scored"),
		cand(3, "c", 0.5, 0.9, "scored"),
		cand(4, "d", 0.5, 1.0, "fresh"),
	}
	out := assembleQueue(cands, baseOpts(10, 1))
	seen := map[int]bool{}
	for _, c := range out {
		require.False(t, seen[c.AdvertiserID], "advertiser %d repeated", c.AdvertiserID)
		seen[c.AdvertiserID] = true
	}
	require.LessOrEqual(t, len(out), 4)
}

func TestAssembleQueue_PerNicheCap(t *testing.T) {
	var cands []prefCandidate
	for i := 0; i < 20; i++ {
		cands = append(cands, cand(i+1, "samenicheword", 0.9-float64(i)*0.01, 0.5, "scored"))
	}
	out := assembleQueue(cands, baseOpts(20, 7))
	require.LessOrEqual(t, len(out), defaultPerNicheCap,
		"a single niche must not exceed the cap")
}

func TestAssembleQueue_FreshReachable(t *testing.T) {
	var cands []prefCandidate
	// 30 scored exploit-y apps across many niches...
	for i := 0; i < 30; i++ {
		cands = append(cands, cand(i+1, string(rune('a'+i%10)), 0.95, 0.1, "scored"))
	}
	// ...plus one fresh app in its own niche.
	cands = append(cands, cand(999, "freshniche", 0.5, 1.0, "fresh"))
	out := assembleQueue(cands, baseOpts(20, 3))
	require.Contains(t, ids(out), 999, "fresh app must be reachable in the blend")
}

func TestAssembleQueue_RatioMix(t *testing.T) {
	var cands []prefCandidate
	// 100 high-p_like scored (exploit), each its own niche
	for i := 0; i < 100; i++ {
		cands = append(cands, cand(1000+i, string(rune('A'))+string(rune(i)), 0.9, 0.1, "scored"))
	}
	// 100 high-uncertainty scored, each its own niche
	for i := 0; i < 100; i++ {
		cands = append(cands, cand(2000+i, string(rune('B'))+string(rune(i)), 0.5, 0.99, "scored"))
	}
	// 100 fresh, each its own niche
	for i := 0; i < 100; i++ {
		cands = append(cands, cand(3000+i, string(rune('C'))+string(rune(i)), 0.5, 1.0, "fresh"))
	}
	out := assembleQueue(cands, baseOpts(20, 11))
	require.Len(t, out, 20)
	var nFresh int
	for _, c := range out {
		if c.Source == "fresh" {
			nFresh++
		}
	}
	// fresh target ≈ 15% of 20 = 3; allow slack for rounding/interleave.
	require.GreaterOrEqual(t, nFresh, 1)
	require.LessOrEqual(t, nFresh, 6)
}

func TestAssembleQueue_VariesAcrossRequests(t *testing.T) {
	var cands []prefCandidate
	for i := 0; i < 60; i++ {
		cands = append(cands, cand(i+1, string(rune('a'+i%15)), 0.6, 0.6, "scored"))
	}
	a := ids(assembleQueue(cands, baseOpts(20, 1)))
	b := ids(assembleQueue(cands, baseOpts(20, 2)))
	require.NotEqual(t, a, b, "different rng seeds must yield different batches")
}

func TestAssembleQueue_UnderfillRedistributes(t *testing.T) {
	// Only exploit candidates; uncertain+fresh empty. Must still fill toward limit.
	var cands []prefCandidate
	for i := 0; i < 10; i++ {
		cands = append(cands, cand(i+1, string(rune('a'+i)), 0.9-float64(i)*0.05, 0.1, "scored"))
	}
	out := assembleQueue(cands, baseOpts(8, 5))
	require.Len(t, out, 8)
}

var _ = models.ShortlistRow{} // keep import if unused elsewhere
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `go test ./backend/repositories/... -run AssembleQueue -v`
Expected: FAIL — `undefined: prefCandidate`, `undefined: assembleQueue`, etc.

- [ ] **Step 3: Write the implementation**

Create `backend/repositories/preference_queue.go`:

```go
package repositories

import (
	"math/rand"
	"sort"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// Serve-time queue blend constants. The review queue mixes the user's taste
// (exploit) with the model's most uncertain apps and brand-new arrivals so the
// deck stays diverse and never re-serves one cluster. See
// docs/superpowers/specs/2026-06-26-review-queue-diversity-design.md.
const (
	defaultPerNicheCap = 3    // max cards per niche_keyword in one batch
	exploitFrac        = 0.60 // share ranked by p_like
	uncertainFrac      = 0.25 // share ranked by uncertainty (p_like ≈ 0.5)
	// freshFrac is the remainder (~0.15): fresh-unscored + leftover scored.
	poolMultiplier = 4 // fresh candidates fetched = poolMultiplier * limit
)

// prefCandidate is one app considered for the review queue: a renderable card
// plus the selection inputs (uncertainty, source). Source is "scored" (has a
// preference_scores row) or "fresh" (in the matview, not yet scored for user).
type prefCandidate struct {
	models.PreferenceCardRow
	Uncertainty float64
	Source      string
}

// queueOpts parameterises assembleQueue. Rng makes selection deterministic in
// tests and varied per request in production (a fresh source each call).
type queueOpts struct {
	Limit         int
	PerNicheCap   int
	ExploitFrac   float64
	UncertainFrac float64
	Rng           *rand.Rand
}

// nicheKey is the diversity-cap key: niche_keyword, falling back to cohort when
// the niche is NULL/empty so capping never collapses all NULL-niche apps into
// one unbounded bucket... wait, NULL must still cap — see below.
func nicheKey(c prefCandidate) string {
	if c.NicheKeyword != nil && *c.NicheKeyword != "" {
		return "n:" + *c.NicheKeyword
	}
	if c.Cohort != "" {
		return "c:" + c.Cohort
	}
	return "" // empty key is exempt from the cap (unknown niche)
}

// assembleQueue blends exploit/uncertain/fresh buckets to opts.Limit, enforces a
// per-niche cap, dedupes advertisers, and randomises within buckets. Pure: no DB,
// deterministic given opts.Rng.
func assembleQueue(cands []prefCandidate, opts queueOpts) []prefCandidate {
	if opts.Limit <= 0 || len(cands) == 0 {
		return nil
	}

	// Shuffle once so equal-key ties resolve randomly (per-request variety),
	// then stable-sort within each bucket by the bucket's priority.
	shuffled := make([]prefCandidate, len(cands))
	copy(shuffled, cands)
	opts.Rng.Shuffle(len(shuffled), func(i, j int) {
		shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
	})

	var scored, fresh []prefCandidate
	for _, c := range shuffled {
		if c.Source == "fresh" {
			fresh = append(fresh, c)
		} else {
			scored = append(scored, c)
		}
	}

	exploit := append([]prefCandidate(nil), scored...)
	sort.SliceStable(exploit, func(i, j int) bool { return exploit[i].PLike > exploit[j].PLike })

	uncertain := append([]prefCandidate(nil), scored...)
	sort.SliceStable(uncertain, func(i, j int) bool { return uncertain[i].Uncertainty > uncertain[j].Uncertainty })

	// Bucket order: each bucket is a queue we draw from in round-robin. Fresh
	// pulls from fresh-unscored first, then leftover scored (already shuffled).
	freshQ := append(append([]prefCandidate(nil), fresh...), scored...)

	buckets := [][]prefCandidate{exploit, uncertain, freshQ}
	// Draw targets per bucket; remainder goes to fresh.
	nExploit := int(float64(opts.Limit)*opts.ExploitFrac + 0.5)
	nUncertain := int(float64(opts.Limit)*opts.UncertainFrac + 0.5)
	nFresh := opts.Limit - nExploit - nUncertain
	if nFresh < 0 {
		nFresh = 0
	}
	targets := []int{nExploit, nUncertain, nFresh}

	cap := opts.PerNicheCap
	if cap <= 0 {
		cap = defaultPerNicheCap
	}

	picked := make([]prefCandidate, 0, opts.Limit)
	seenAdv := map[int]bool{}
	nicheCount := map[string]int{}
	cursors := []int{0, 0, 0}
	got := []int{0, 0, 0}

	tryTake := func(b int) bool {
		for cursors[b] < len(buckets[b]) {
			c := buckets[b][cursors[b]]
			cursors[b]++
			if seenAdv[c.AdvertiserID] {
				continue
			}
			k := nicheKey(c)
			if k != "" && nicheCount[k] >= cap {
				continue
			}
			seenAdv[c.AdvertiserID] = true
			if k != "" {
				nicheCount[k]++
			}
			picked = append(picked, c)
			got[b]++
			return true
		}
		return false
	}

	// Round-robin to interleave buckets until each hits its target.
	for len(picked) < opts.Limit {
		progressed := false
		for b := 0; b < 3; b++ {
			if got[b] >= targets[b] {
				continue
			}
			if tryTake(b) {
				progressed = true
				if len(picked) >= opts.Limit {
					break
				}
			}
		}
		if !progressed {
			break
		}
	}

	// Underfill pass: ignore targets, fill from any bucket (still capped/deduped).
	for len(picked) < opts.Limit {
		progressed := false
		for b := 0; b < 3; b++ {
			if tryTake(b) {
				progressed = true
				if len(picked) >= opts.Limit {
					break
				}
			}
		}
		if !progressed {
			break
		}
	}

	return picked
}
```

> Note: the `nicheKey` doc comment above is intentionally trimmed in the final
> file — keep only: "diversity-cap key: niche_keyword, else cohort, else exempt."

- [ ] **Step 4: Run tests to verify they pass**

Run: `go test ./backend/repositories/... -run AssembleQueue -v`
Expected: PASS (all `TestAssembleQueue_*`).

- [ ] **Step 5: Vet + tidy**

Run: `go vet ./backend/repositories/...`
Expected: no output. Remove the `var _ = models.ShortlistRow{}` line from the test if `models` is otherwise used; keep it only if the import would be unused.

- [ ] **Step 6: Commit**

```bash
git add backend/repositories/preference_queue.go backend/repositories/preference_queue_test.go
git commit -m "feat(review): pure assembleQueue blend/cap/interleave

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Wire candidate-pool SQL into `PreferenceQueue`

**Files:**
- Modify: `backend/repositories/preference_repo.go`
- Modify: `backend/repositories/preference_repo_test.go`

**Interfaces:**
- Consumes: `prefCandidate`, `queueOpts`, `assembleQueue`, constants (Task 1); `mvCols`, `toShortlistRows`, `attachEnrichment` (existing in `repositories.go`); `models.PreferenceCardRow`.
- Produces: unchanged public signature `PreferenceQueue(ctx, g, userEmail string, limit int) ([]models.PreferenceCardRow, error)`.

- [ ] **Step 1: Update the integration test (now a failing spec)**

The rewrite makes fresh-unscored matview apps appear, so the old `require.Len(t, rows, 1)` no longer holds. Replace the body of `TestPreferenceQueue` in `backend/repositories/preference_repo_test.go` (keep the setup through the two inserts) so the assertions become:

```go
	rows, err := repositories.PreferenceQueue(context.Background(), g, email, 50)
	require.NoError(t, err)
	require.NotEmpty(t, rows)

	got := map[int64]bool{}
	for _, r := range rows {
		got[int64(r.AdvertiserID)] = true
	}
	require.False(t, got[ids[0]], "rated app must be excluded")
	require.True(t, got[ids[1]], "unrated scored app must surface")

	// Per-request variety: a second fetch should not be byte-identical order.
	rows2, err := repositories.PreferenceQueue(context.Background(), g, email, 50)
	require.NoError(t, err)
	if len(rows) > 3 && len(rows2) > 3 {
		var a, b []int
		for _, r := range rows {
			a = append(a, r.AdvertiserID)
		}
		for _, r := range rows2 {
			b = append(b, r.AdvertiserID)
		}
		require.NotEqual(t, a, b, "two fetches should differ in order/content")
	}
```

Add `"context"` is already imported. No new imports needed.

- [ ] **Step 2: Run the integration test to verify it fails**

Run: `AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./backend/repositories/... -run TestPreferenceQueue -v`
Expected: FAIL (old query has no variety / excludes fresh) OR compile error once Step 3 types land. If `AGF_IT_DB_URL` is unset the test skips — set it.

- [ ] **Step 3: Rewrite `PreferenceQueue` and its scan type**

In `backend/repositories/preference_repo.go`, replace the `prefScanRow` type, its `toMvScanRow` method, and the `PreferenceQueue` function with:

```go
// candScanRow is the scan target for a queue candidate: matview columns plus the
// per-user preference inputs. GORM's Scan handles one level of embedding, so we
// embed models.ShortlistRow and repeat the flat growth fields (mirrors mvScanRow).
type candScanRow struct {
	models.ShortlistRow
	FirstCount  *int     `gorm:"column:first_count"`
	LatestCount *int     `gorm:"column:latest_count"`
	Weeks       *int     `gorm:"column:weeks_observed"`
	PctChange   *float64 `gorm:"column:pct_change"`
	PLike       float64  `gorm:"column:p_like"`
	Uncertainty float64  `gorm:"column:uncertainty"`
	Why         string   `gorm:"column:why"`
	Source      string   `gorm:"column:source"`
}

func (r candScanRow) toMvScanRow() mvScanRow {
	return mvScanRow{
		ShortlistRow: r.ShortlistRow,
		FirstCount:   r.FirstCount,
		LatestCount:  r.LatestCount,
		Weeks:        r.Weeks,
		PctChange:    r.PctChange,
	}
}

// PreferenceQueue returns the user's review queue. It builds a wide candidate
// pool — all unrated scored apps ∪ the top fresh-unscored matview apps — then
// blends exploit/uncertain/fresh buckets and caps per niche via assembleQueue so
// each fetch is diverse and non-repeating. Hero creative + growth are attached
// only to the final selection, inside the same read transaction.
func PreferenceQueue(ctx context.Context, g *gorm.DB, userEmail string, limit int) ([]models.PreferenceCardRow, error) {
	var outRows []models.PreferenceCardRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		freshCap := limit * poolMultiplier
		scanned := make([]candScanRow, 0)
		if err := tx.Raw(
			`WITH scored AS (
			   SELECT ps.advertiser_id AS adv_id, ps.p_like, ps.uncertainty,
			          ps.why, 'scored'::text AS source
			   FROM frontend.preference_scores ps
			   WHERE ps.user_email = ?
			     AND NOT EXISTS (
			       SELECT 1 FROM frontend.app_ratings r
			       WHERE r.user_email = ps.user_email
			         AND r.advertiser_id = ps.advertiser_id)
			 ),
			 fresh AS (
			   SELECT m.advertiser_id AS adv_id, 0.5::double precision AS p_like,
			          1.0::double precision AS uncertainty, 'new'::text AS why,
			          'fresh'::text AS source
			   FROM frontend.emerging_scaling_apps m
			   WHERE NOT EXISTS (
			       SELECT 1 FROM frontend.preference_scores ps
			       WHERE ps.user_email = ? AND ps.advertiser_id = m.advertiser_id)
			     AND NOT EXISTS (
			       SELECT 1 FROM frontend.app_ratings r
			       WHERE r.user_email = ? AND r.advertiser_id = m.advertiser_id)
			   ORDER BY m.gem_score DESC NULLS LAST
			   LIMIT ?
			 ),
			 cand AS (
			   SELECT * FROM scored
			   UNION ALL
			   SELECT * FROM fresh
			 )
			 SELECT `+mvCols+`, cand.p_like, cand.uncertainty, cand.why, cand.source
			 FROM frontend.emerging_scaling_apps
			 JOIN cand ON cand.adv_id = frontend.emerging_scaling_apps.advertiser_id`,
			userEmail, userEmail, userEmail, freshCap,
		).Scan(&scanned).Error; err != nil {
			return err
		}

		cands := make([]prefCandidate, len(scanned))
		for i, r := range scanned {
			short := toShortlistRows([]mvScanRow{r.toMvScanRow()})[0]
			cands[i] = prefCandidate{
				PreferenceCardRow: models.PreferenceCardRow{
					ShortlistRow: short,
					PLike:        r.PLike,
					Why:          r.Why,
				},
				Uncertainty: r.Uncertainty,
				Source:      r.Source,
			}
		}

		selected := assembleQueue(cands, queueOpts{
			Limit:         limit,
			PerNicheCap:   defaultPerNicheCap,
			ExploitFrac:   exploitFrac,
			UncertainFrac: uncertainFrac,
			Rng:           rand.New(rand.NewSource(time.Now().UnixNano())),
		})

		shortRows := make([]models.ShortlistRow, len(selected))
		for i := range selected {
			shortRows[i] = selected[i].ShortlistRow
		}
		if err := attachEnrichment(tx, shortRows); err != nil {
			return err
		}
		outRows = make([]models.PreferenceCardRow, len(selected))
		for i := range selected {
			outRows[i] = models.PreferenceCardRow{
				ShortlistRow: shortRows[i],
				PLike:        selected[i].PLike,
				Why:          selected[i].Why,
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return outRows, nil
}
```

- [ ] **Step 4: Add the new imports**

In `backend/repositories/preference_repo.go`, the import block must include `math/rand` and `time` alongside the existing `context` and packages:

```go
import (
	"context"
	"math/rand"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)
```

- [ ] **Step 5: Build + run the integration test**

Run: `go build ./backend/... && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./backend/repositories/... -run TestPreferenceQueue -v`
Expected: PASS — rated app excluded, unrated scored app present, two fetches differ.

> If `time.Now()` makes the variety assertion flaky on tiny pools, that is
> acceptable: the assertion is guarded by `len(rows) > 3`. Do not seed from a
> constant in production code — per-request entropy is the point.

- [ ] **Step 6: Commit**

```bash
git add backend/repositories/preference_repo.go backend/repositories/preference_repo_test.go
git commit -m "feat(review): serve-time diverse candidate pool (scored ∪ fresh)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Full-suite + live end-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Full backend test suite (unit + integration)**

Run: `AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./backend/... 2>&1`
Expected: `ok` for every package, 0 failures. Read the full output — do not pipe through `tail`/`grep`. Fix any pre-existing failures per the zero-failing-tests standard.

- [ ] **Step 2: Live API run against the working DB**

Start the API (or use the running one) and hit the endpoint twice for a user that has ratings:

```bash
curl -s "http://localhost:8080/api/preference-queue?user_email=elakavicius@gmail.com&limit=30" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("n=",len(d)); print("niches=",sorted({x.get("niche_keyword") for x in d})); print("ids=",[x["advertiser_id"] for x in d][:30])'
```

Run it twice. Expected:
- `n` ≈ 30 (or fewer if the pool is small).
- The two `ids` lists are **not identical** (per-request variety).
- No `advertiser_id` the user has rated appears (spot-check against `frontend.app_ratings`).
- `niches` shows more than one or two values (diversity), and no single niche dominates beyond `defaultPerNicheCap`.

> If the API isn't running locally, the prod path is via cloud-sql-proxy; start
> the Go API against the same DSN the dashboard uses. Confirm `niche_keyword`
> NULL rate with `SELECT count(*) FILTER (WHERE niche_keyword IS NULL), count(*)
> FROM frontend.emerging_scaling_apps;` — if NULLs dominate, the cohort fallback
> in `nicheKey` is what's doing the capping; note it in the commit if so.

- [ ] **Step 3: Record verification + (optional) memory update**

If everything passes, the feature is done. Optionally update the memory index entry for the review queue to note serve-time diversity now lives in `assembleQueue`.

No commit needed unless Step 1 required fixes.

---

## Self-Review

**Spec coverage:**
- Serve-time per-request selection → Task 2 (`assembleQueue` called with fresh Rng per request). ✓
- Scored ∪ fresh-unscored pool (staleness fix) → Task 2 SQL `scored`/`fresh` CTEs. ✓
- 60/25/15 buckets → Task 1 constants + round-robin. ✓
- Per-niche cap → Task 1 `nicheKey` + `nicheCount`. ✓
- Per-request variety → Task 1 shuffle + Task 2 time-seeded Rng; asserted in both unit and integration tests. ✓
- Pure testable core → Task 1 unit tests, no DB. ✓
- Unchanged controller/route/frontend/scorer/schema → no tasks touch them; Global Constraints. ✓
- Live e2e run → Task 3. ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code. The trimmed
`nicheKey` comment is called out explicitly. ✓

**Type consistency:** `prefCandidate`/`queueOpts`/`assembleQueue` defined in Task 1
are consumed verbatim in Task 2. `AdvertiserID int`, `NicheKeyword *string`,
`Cohort string`, `PLike float64`, `Why string` match `models.ShortlistRow` /
`PreferenceCardRow`. `candScanRow` mirrors the existing `mvScanRow` growth fields.
SQL selects `p_like, uncertainty, why, source` → scanned by matching `gorm:"column:"`
tags. ✓
