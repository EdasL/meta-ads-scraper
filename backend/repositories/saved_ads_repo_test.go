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
