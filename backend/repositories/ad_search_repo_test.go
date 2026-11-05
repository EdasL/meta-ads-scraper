package repositories_test

import (
	"context"
	"testing"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/stretchr/testify/require"
)

func TestAdSearchPersistenceAndRanking(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	owner := "ad-search-test@t.local"
	cleanup := func() {
		must(g.Exec(`DELETE FROM public.pipeline_jobs WHERE kind='ad_search' AND advertiser_id IN (SELECT id FROM frontend.ad_searches WHERE created_by=?)`, owner).Error)
		must(g.Exec(`DELETE FROM frontend.ad_searches WHERE created_by=?`, owner).Error)
	}
	cleanup()
	t.Cleanup(cleanup)
	request := models.AdSearchRequest{Operation: "keyword", Country: "US", MatchMode: "exact", Queries: []string{"walking plan"}}
	s, err := repositories.CreateAdSearch(ctx, g, "AD-SEARCH-TEST@t.local", request, "key", false)
	require.NoError(t, err)
	var jobs int64
	require.NoError(t, g.Raw(`SELECT count(*) FROM public.pipeline_jobs WHERE kind='ad_search' AND advertiser_id=?`, s.ID).Scan(&jobs).Error)
	require.EqualValues(t, 1, jobs)
	again, err := repositories.CreateAdSearch(ctx, g, owner, request, "key", false)
	require.NoError(t, err)
	require.Equal(t, s.ID, again.ID)
	changed := request
	changed.Queries = []string{"different"}
	_, err = repositories.CreateAdSearch(ctx, g, owner, changed, "key", false)
	require.ErrorIs(t, err, repositories.ErrAdSearchConflict)
	foreign, err := repositories.GetAdSearch(ctx, g, "other@t.local", s.ID)
	require.NoError(t, err)
	require.Nil(t, foreign)
	_, err = repositories.CreateAdSearch(ctx, g, owner, request, "", false)
	require.NoError(t, err)
	_, err = repositories.CreateAdSearch(ctx, g, owner, request, "", false)
	require.ErrorIs(t, err, repositories.ErrAdSearchCapacity)
	again, err = repositories.CreateAdSearch(ctx, g, owner, request, "key", false)
	require.NoError(t, err)
	require.Equal(t, s.ID, again.ID)
	require.NoError(t, g.Exec(`UPDATE frontend.ad_searches SET status = 'done' WHERE id = ?`, s.ID).Error)
	again, err = repositories.CreateAdSearch(ctx, g, owner, request, "key", false)
	require.NoError(t, err)
	require.Equal(t, "done", again.Status)

	cached, err := repositories.CreateAdSearch(ctx, g, owner, request, "", false)
	require.NoError(t, err)
	require.Equal(t, s.ID, cached.ID)
	// Cache includes country and query, and never crosses the owner boundary.
	foreignRequest := request
	foreignRequest.Country = "GB"
	fresh, err := repositories.CreateAdSearch(ctx, g, owner, foreignRequest, "", false)
	require.NoError(t, err)
	require.NotEqual(t, s.ID, fresh.ID)
	require.NoError(t, g.Exec(`INSERT INTO frontend.ad_search_results(search_id,ad_archive_id,page_id,page_name,source_term,country,is_active,creative_hash,body_text) VALUES (?, '1','page','Brand','query:x','US',true,'same',''), (?, '2','page','Brand','query:x','US',true,'same','Sparse')`, s.ID, s.ID).Error)
	rows, err := repositories.AdScrapeResults(ctx, g, owner, s.ID, 1, 1)
	require.NoError(t, err)
	require.Len(t, rows, 1)
	require.Equal(t, "2", rows[0].AdArchiveID)
	rows, err = repositories.AdScrapeResults(ctx, g, "other@t.local", s.ID, 20, 0)
	require.NoError(t, err)
	require.Empty(t, rows)
}
