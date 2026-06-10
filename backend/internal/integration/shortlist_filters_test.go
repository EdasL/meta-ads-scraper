// Shortlist filtering / pagination / categories — the backend side of the
// Discover filter bar (spec docs/superpowers/specs/2026-06-10-discover-
// filtering-pagination.md). Seeded gems, by gem_score DESC:
//
//	advID  88.5  app "Acme App" (Productivity)   page "Acme Co"  vel 0.7  age 42  funnel 0.5  growth NULL
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

// listGems GETs /api/shortlist with params and returns (recorder, advertiser ids in order).
func listGems(t *testing.T, params url.Values) (*httptest.ResponseRecorder, []int) {
	t.Helper()
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/shortlist?"+params.Encode(), nil))
	var rows []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
	ids := make([]int, len(rows))
	for i, row := range rows {
		ids[i] = int(row["advertiser_id"].(float64))
	}
	return w, ids
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

func TestShortlistRisingHidesFallingGrowth(t *testing.T) {
	w, ids := listGems(t, url.Values{"rising": {"true"}})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []int{advID, adv2ID}, ids, "falling 10→4 gem hidden; NULL growth kept")
	require.Equal(t, "2", w.Header().Get("X-Total-Count"))
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

func TestShortlistMinAdAgeAndFunnel(t *testing.T) {
	_, ids := listGems(t, url.Values{"min_ad_age": {"21"}})
	require.Equal(t, []int{advID}, ids, "12-day and NULL-age gems excluded")

	_, ids = listGems(t, url.Values{"has_funnel": {"true"}})
	require.Equal(t, []int{advID}, ids, "funnel_intent >= 0.5 only (0.2 and NULL excluded)")
}

func TestShortlistTotalCountWithPagination(t *testing.T) {
	w, ids := listGems(t, url.Values{"limit": {"1"}})
	require.Equal(t, 200, w.Code)
	require.Len(t, ids, 1, "page of one")
	require.Equal(t, "3", w.Header().Get("X-Total-Count"), "total spans all pages")

	w, ids = listGems(t, url.Values{"limit": {"1"}, "offset": {"2"}})
	require.Equal(t, []int{adv3ID}, ids, "third page of one = lowest score")
	require.Equal(t, "3", w.Header().Get("X-Total-Count"))
}

func TestShortlistBadParams(t *testing.T) {
	for _, q := range []url.Values{
		{"sort": {"bogus"}},
		{"rising": {"banana"}},
		{"has_funnel": {"2"}},
		{"min_ad_age": {"abc"}},
	} {
		r := router()
		w := httptest.NewRecorder()
		r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/shortlist?"+q.Encode(), nil))
		require.Equal(t, http.StatusUnprocessableEntity, w.Code, "params %v", q)
		require.Contains(t, w.Body.String(), "detail")
	}
}

func TestShortlistCategories(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/shortlist/categories", nil))
	require.Equal(t, 200, w.Code)

	var cats []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &cats))
	// One visible gem per genre; the NULL-genre gem (adv2) is skipped. Equal
	// counts tie-break alphabetically.
	require.Len(t, cats, 2)
	require.Equal(t, "Health & Fitness", cats[0]["category"])
	require.EqualValues(t, 1, cats[0]["count"])
	require.Equal(t, "Productivity", cats[1]["category"])
	require.EqualValues(t, 1, cats[1]["count"])
}
