package mcpserver

import (
	"context"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/stretchr/testify/require"
	"testing"
)

func TestAdResearchSelectionAndAccess(t *testing.T) {
	a := selectedAd{ScrapeID: 1, AdID: "42"}
	require.NoError(t, validateAdSelection([]selectedAd{a}, 6))
	for _, ads := range [][]selectedAd{{a, a}, {{ScrapeID: -1, AdID: "2"}}, {{ScrapeID: 1, AdID: ""}}, {{ScrapeID: 1, AdID: " 2"}}} {
		require.Error(t, validateAdSelection(ads, 6))
	}
	h := handlers{u: middlewares.AuthedUser{Role: "member"}}
	_, _, err := h.findSimilarBrands(context.Background(), nil, similarBrandsIn{Brief: "test"})
	require.Error(t, err)
	_, _, err = h.judgeAds(context.Background(), nil, judgeAdsIn{Brief: "test", CandidateAds: []selectedAd{a}})
	require.Error(t, err)
}
