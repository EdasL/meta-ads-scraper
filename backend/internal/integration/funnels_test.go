// Funnels — the backend side of the /funnels view. GET /api/funnels groups
// discovery.funnel_candidates by landing_domain, confirmed-only. Seeded rows:
//
//	getrelatio.com    2 pages (FP1 quiz_steps=2, FP2 quiz_steps=0)  last_seen now-1h
//	spotandtango.com  1 page  (FP3)                                 last_seen now-6h
//	pending.example   status=new      → excluded
//	dropped.example   status=dropped  → excluded
package integration

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"

	"github.com/stretchr/testify/require"
)

// listFunnels GETs /api/funnels with params and returns (recorder, landing domains in order).
func listFunnels(t *testing.T, params url.Values) (*httptest.ResponseRecorder, []string, []map[string]any) {
	t.Helper()
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/funnels?"+params.Encode(), nil))
	var rows []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
	domains := make([]string, len(rows))
	for i, row := range rows {
		domains[i] = row["landing_domain"].(string)
	}
	return w, domains, rows
}

func TestFunnelsConfirmedOnlyGroupedByDomain(t *testing.T) {
	w, domains, rows := listFunnels(t, url.Values{})
	require.Equal(t, 200, w.Code)
	// Two confirmed domains; the 'new' and 'dropped' rows are excluded.
	require.Equal(t, "2", w.Header().Get("X-Total-Count"))
	require.ElementsMatch(t, []string{"getrelatio.com", "spotandtango.com"}, domains)

	byDomain := map[string]map[string]any{}
	for _, r := range rows {
		byDomain[r["landing_domain"].(string)] = r
	}
	// getrelatio.com aggregates its two pages: advertiser_count=2, has_quiz=true
	// (FP1 has quiz_steps=2), times_seen summed (5+3=8).
	rel := byDomain["getrelatio.com"]
	require.EqualValues(t, 2, rel["advertiser_count"])
	require.Equal(t, true, rel["has_quiz"])
	require.EqualValues(t, 8, rel["times_seen"])
	require.EqualValues(t, 2, rel["max_quiz_steps"])

	// spotandtango.com: one page, no quiz.
	st := byDomain["spotandtango.com"]
	require.EqualValues(t, 1, st["advertiser_count"])
	require.Equal(t, false, st["has_quiz"])
}

func TestFunnelsDefaultSortNewest(t *testing.T) {
	// Default sort is newest by last_seen_at: getrelatio.com (now-1h) before
	// spotandtango.com (now-6h).
	w, domains, _ := listFunnels(t, url.Values{})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []string{"getrelatio.com", "spotandtango.com"}, domains)
}

func TestFunnelsSortByAdvertisers(t *testing.T) {
	// advertisers DESC: getrelatio.com (2) before spotandtango.com (1).
	w, domains, _ := listFunnels(t, url.Values{"sort": {"advertisers"}})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []string{"getrelatio.com", "spotandtango.com"}, domains)
}

func TestFunnelsSearchByDomain(t *testing.T) {
	w, domains, _ := listFunnels(t, url.Values{"q": {"SPOTAND"}})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []string{"spotandtango.com"}, domains)
	require.Equal(t, "1", w.Header().Get("X-Total-Count"))
}

func TestFunnelsHasQuizFilter(t *testing.T) {
	// has_quiz=1 keeps only domains with at least one quiz page.
	w, domains, _ := listFunnels(t, url.Values{"has_quiz": {"1"}})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []string{"getrelatio.com"}, domains)
	require.Equal(t, "1", w.Header().Get("X-Total-Count"))
}

func TestFunnelsPagination(t *testing.T) {
	w, domains, _ := listFunnels(t, url.Values{"limit": {"1"}, "offset": {"0"}, "sort": {"advertisers"}})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []string{"getrelatio.com"}, domains)
	require.Equal(t, "2", w.Header().Get("X-Total-Count"), "total spans all pages")

	w, domains, _ = listFunnels(t, url.Values{"limit": {"1"}, "offset": {"1"}, "sort": {"advertisers"}})
	require.Equal(t, 200, w.Code)
	require.Equal(t, []string{"spotandtango.com"}, domains)
}
