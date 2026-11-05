package mcpserver

import (
	"context"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"testing"
)

func TestNormalizeScrape(t *testing.T) {
	r, err := normalizeScrape(adQueriesIn{Queries: []string{" Walking plan ", "walking plan"}, Country: " gb "}, "keyword")
	if err != nil || r.Country != "GB" || r.MatchMode != "exact" || len(r.Queries) != 1 {
		t.Fatalf("normalization: %#v %v", r, err)
	}
	for _, in := range []adQueriesIn{{}, {Queries: []string{""}}, {Queries: []string{"a", "b", "c", "d"}}, {Queries: []string{"x"}, Country: "ZZ"}, {Queries: []string{"x"}, Country: "US UY"}, {Queries: []string{"x"}, MatchMode: "invalid"}} {
		if _, err := normalizeScrape(in, "keyword"); err == nil {
			t.Fatalf("accepted %#v", in)
		}
	}
}
func TestAdScrapeRequiresAdmin(t *testing.T) {
	h := handlers{u: middlewares.AuthedUser{Role: "member"}}
	if _, _, err := h.searchMetaAds(context.Background(), nil, adQueriesIn{Queries: []string{"walking"}}); err == nil {
		t.Fatal("allowed member")
	}
	if _, _, err := h.adScrapeStatus(context.Background(), nil, scrapeStatusIn{ScrapeID: 1}); err == nil {
		t.Fatal("allowed member status")
	}
	if _, _, err := h.nextAdPage(context.Background(), nil, nextAdPageIn{ScrapeID: 1}); err == nil {
		t.Fatal("allowed member pagination")
	}
}
