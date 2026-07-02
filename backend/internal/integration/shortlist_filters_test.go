// Shortlist filtering / pagination / categories — the backend side of the
// Discover filter bar (spec docs/superpowers/specs/2026-06-10-discover-
// filtering-pagination.md). Seeded gems, by gem_score DESC:
//
//	advID  88.5  app "Acme App" (Productivity)   page "Acme Co"  vel 0.7  age 42  funnel 1.0  growth NULL
//	adv2ID 75    no app                          page NULL       vel NULL age NULL funnel NULL growth NULL
//	adv3ID 72    app "Walk Tracker" (H&F)        page "Walkly"   vel 0.9  age 12  funnel 0.2  growth 10→4 (falling)
package integration

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"

	"github.com/stretchr/testify/require"
)

// seededSet is the set of advertisers this package's seed() owns. The suite
// shares one test DB with backend/repositories, whose fixtures INSERT their own
// advertisers and REFRESH the same matview concurrently; scoping assertions to
// our own ids keeps the feed tests hermetic against those intruders instead of
// serializing every package (go test -p 1).
func seededSet() map[int]bool {
	return map[int]bool{advID: true, adv2ID: true, adv3ID: true, adv4ID: true}
}

// keepSeeded filters an ordered id list down to this package's seeded gems,
// preserving order. Relative order (what the sort/filter tests prove) survives;
// foreign rows from parallel packages drop out.
func keepSeeded(ids []int) []int {
	s := seededSet()
	out := make([]int, 0, len(ids))
	for _, id := range ids {
		if s[id] {
			out = append(out, id)
		}
	}
	return out
}

// listGems GETs /api/shortlist with params and returns (recorder, advertiser
// ids in order), scoped to this package's seeded gems. The X-Total-Count header
// on the recorder is a GLOBAL count and is therefore NOT hermetic — assert it
// only under a filter that excludes foreign rows (e.g. an owned cohort/genre).
func listGems(t *testing.T, params url.Values) (*httptest.ResponseRecorder, []int) {
	t.Helper()
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, authed(httptest.NewRequest(http.MethodGet, "/api/shortlist?"+params.Encode(), nil)))
	var rows []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
	ids := make([]int, len(rows))
	for i, row := range rows {
		ids[i] = int(row["advertiser_id"].(float64))
	}
	return w, keepSeeded(ids)
}

func TestShortlistCategoryFilter(t *testing.T) {
	w, ids := listGems(t, url.Values{"category": {"Health & Fitness"}})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []int{adv3ID}, ids, "only the Health & Fitness gem")
	require.Equal(t, "1", w.Header().Get("X-Total-Count"))

	w, ids = listGems(t, url.Values{"category": {"Casino"}})
	require.Equal(t, 200, w.Code)
	require.Empty(t, ids, "unknown category → empty page, not an error")
	require.Equal(t, "0", w.Header().Get("X-Total-Count"))
}

func TestShortlistSearch(t *testing.T) {
	// Matches the app name…
	w, ids := listGems(t, url.Values{"q": {"tracker"}})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []int{adv3ID}, ids)

	// …and the page name, case-insensitively.
	_, ids = listGems(t, url.Values{"q": {"WALKLY"}})
	require.Equal(t, []int{adv3ID}, ids)

	// LIKE metacharacters are literals, not wildcards.
	w, ids = listGems(t, url.Values{"q": {"%"}})
	require.Equal(t, 200, w.Code)
	require.Empty(t, ids, "no name contains a literal percent sign")
}

func TestShortlistSorts(t *testing.T) {
	// newest = youngest ads first, NULL longevity last.
	_, ids := listGems(t, url.Values{"sort": {"newest"}})
	require.Equal(t, []int{adv3ID, advID, adv2ID}, ids)

	// fastest = highest scaling velocity first, NULL last.
	_, ids = listGems(t, url.Values{"sort": {"fastest"}})
	require.Equal(t, []int{adv3ID, advID, adv2ID}, ids)

	// explicit gemscore matches the default order.
	_, ids = listGems(t, url.Values{"sort": {"gemscore"}})
	require.Equal(t, []int{advID, adv2ID, adv3ID}, ids)
}

func TestShortlistSortDir(t *testing.T) {
	// dir=asc reverses the default gemscore order.
	_, ids := listGems(t, url.Values{"dir": {"asc"}})
	require.Equal(t, []int{adv3ID, adv2ID, advID}, ids)

	// Reversed velocity — NULLs stay last in both directions.
	_, ids = listGems(t, url.Values{"sort": {"fastest"}, "dir": {"asc"}})
	require.Equal(t, []int{advID, adv3ID, adv2ID}, ids)

	// Reversed newest = oldest ads first, NULL longevity still last.
	_, ids = listGems(t, url.Values{"sort": {"newest"}, "dir": {"desc"}})
	require.Equal(t, []int{advID, adv3ID, adv2ID}, ids)

	// An explicit dir matching the key's natural direction is a no-op.
	_, ids = listGems(t, url.Values{"sort": {"newest"}, "dir": {"asc"}})
	require.Equal(t, []int{adv3ID, advID, adv2ID}, ids)
}

func TestShortlistMinAdAgeAndFunnel(t *testing.T) {
	_, ids := listGems(t, url.Values{"min_ad_age": {"21"}})
	require.Equal(t, []int{advID}, ids, "12-day and NULL-age gems excluded")

	_, ids = listGems(t, url.Values{"has_funnel": {"true"}})
	require.Equal(t, []int{advID}, ids, "funnel_intent >= 0.7 only (0.5 baseline, 0.2, and NULL excluded)")
}

// The 14-day longevity floor used to drop fresh apps from the feed entirely.
// Migration 124 keeps them as an "incubating" cohort: hidden from the default
// confirmed feed, surfaced only under incubating=true (the Fresh view).
func TestShortlistIncubatingCohort(t *testing.T) {
	// Default feed: confirmed only — the longevity-floored gem (adv4ID) is hidden,
	// the three confirmed seeded gems appear. (X-Total-Count is a global count and
	// racy under parallel packages, so assert our own seeded rows instead.)
	w, ids := listGems(t, url.Values{})
	require.Equal(t, 200, w.Code)
	require.NotContains(t, ids, adv4ID, "incubating gem must not pollute the confirmed feed")
	require.ElementsMatch(t, []int{advID, adv2ID, adv3ID}, ids, "the three confirmed seeded gems")

	// Fresh view: incubating=true returns ONLY the longevity-floored cohort.
	w, ids = listGems(t, url.Values{"incubating": {"true"}})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []int{adv4ID}, ids, "only the incubating gem")
}

func TestShortlistTotalCountWithPagination(t *testing.T) {
	// Scope to the 'ios' cohort — owned solely by this package's seed (advID 88.5,
	// adv2ID 75) — so X-Total-Count is hermetic. An unfiltered feed count races
	// with the advertisers other packages insert into the shared matview.
	ios := url.Values{"cohort": {"ios"}}

	set := func(base url.Values, extra url.Values) url.Values {
		v := url.Values{}
		for k, vs := range base {
			v[k] = vs
		}
		for k, vs := range extra {
			v[k] = vs
		}
		return v
	}

	w, ids := listGems(t, set(ios, url.Values{"limit": {"1"}}))
	require.Equal(t, 200, w.Code)
	require.Equal(t, []int{advID}, ids, "page of one = highest score in cohort")
	require.Equal(t, "2", w.Header().Get("X-Total-Count"), "total spans all pages")

	w, ids = listGems(t, set(ios, url.Values{"limit": {"1"}, "offset": {"1"}}))
	require.Equal(t, []int{adv2ID}, ids, "second page of one = lowest score in cohort")
	require.Equal(t, "2", w.Header().Get("X-Total-Count"))
}

func TestShortlistBadParams(t *testing.T) {
	for _, q := range []url.Values{
		{"sort": {"bogus"}},
		{"dir": {"sideways"}},
		{"has_funnel": {"2"}},
		{"min_ad_age": {"abc"}},
		// Negative paging must be a deliberate 422, not a Postgres error → 500.
		{"limit": {"-1"}},
		{"offset": {"-1"}},
		// Non-finite floats parse but poison the SQL comparison (NaN > everything
		// in Postgres → silently empty feed) — reject them up front.
		{"min_score": {"NaN"}},
		{"min_score": {"Inf"}},
	} {
		r := router()
		w := httptest.NewRecorder()
		r.ServeHTTP(w, authed(httptest.NewRequest(http.MethodGet, "/api/shortlist?"+q.Encode(), nil)))
		require.Equal(t, http.StatusUnprocessableEntity, w.Code, "params %v", q)
		require.Contains(t, w.Body.String(), "detail")
	}
}

// Present-but-empty string params mean "no filter", exactly like absent ones —
// truncated/hand-edited share URLs must not silently empty the feed or 422.
func TestShortlistEmptyParamsAreNoFilter(t *testing.T) {
	w, ids := listGems(t, url.Values{"category": {""}, "cohort": {""}, "sort": {""}, "dir": {""}})
	require.Equal(t, 200, w.Code)
	require.Len(t, ids, 3, "empty params filter nothing")
}

// An oversized limit is clamped, never an error (and never a full-table dump).
func TestShortlistLimitClamped(t *testing.T) {
	w, ids := listGems(t, url.Values{"limit": {"99999"}})
	require.Equal(t, 200, w.Code)
	require.Len(t, ids, 3)
}

func listCategories(t *testing.T, params url.Values) []map[string]any {
	t.Helper()
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, authed(httptest.NewRequest(http.MethodGet, "/api/shortlist/categories?"+params.Encode(), nil)))
	require.Equal(t, 200, w.Code)
	var cats []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &cats))
	return cats
}

func TestShortlistCategories(t *testing.T) {
	cats := listCategories(t, url.Values{})
	// One visible gem per genre; the NULL-genre gem (adv2) is skipped. Equal
	// counts tie-break alphabetically.
	require.Len(t, cats, 2)
	require.Equal(t, "Health & Fitness", cats[0]["category"])
	require.EqualValues(t, 1, cats[0]["count"])
	require.Equal(t, "Productivity", cats[1]["category"])
	require.EqualValues(t, 1, cats[1]["count"])
}

// Counts answer "what would the feed hold if I picked this category?" under
// the CURRENT filters — a dropdown number must match what clicking it yields.
func TestShortlistCategoriesHonorFilters(t *testing.T) {
	// q=walk matches only the Health & Fitness gem.
	cats := listCategories(t, url.Values{"q": {"walk"}})
	require.Len(t, cats, 1)
	require.Equal(t, "Health & Fitness", cats[0]["category"])

	// min_score=80 keeps only the Productivity gem.
	cats = listCategories(t, url.Values{"min_score": {"80"}})
	require.Len(t, cats, 1)
	require.Equal(t, "Productivity", cats[0]["category"])

	// The category param itself is ignored — counts are for switching.
	cats = listCategories(t, url.Values{"category": {"Productivity"}})
	require.Len(t, cats, 2)
}
