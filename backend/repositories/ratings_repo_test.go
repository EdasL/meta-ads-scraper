// Package repositories_test exercises ratings_repo.go against a real Postgres
// database. Skipped unless AGF_IT_DB_URL is set.
package repositories_test

import (
	"context"
	"os"
	"testing"

	"github.com/stretchr/testify/require"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

var ratingsDB *gorm.DB

func TestMain(m *testing.M) {
	dsn := os.Getenv("AGF_IT_DB_URL")
	if dsn == "" {
		os.Exit(0)
	}
	g, err := db.Init(dsn)
	if err != nil {
		panic(err)
	}
	ratingsDB = g
	seedRatings(g)
	os.Exit(m.Run())
}

// ratingsAdvID is the seeded advertiser used across ratings tests.
var ratingsAdvID int64

// seedRatings inserts one advertiser + matview row, wiring up only what
// UpsertRating (and the matview) actually read. The integration test's
// seed() owns the full suite; here we just need a minimal fixture.
func seedRatings(g *gorm.DB) {
	must(g.Exec(`TRUNCATE frontend.app_ratings RESTART IDENTITY`).Error)
	// Remove any stale test advertiser.
	must(g.Exec(`DELETE FROM public.advertisers WHERE page_id = 'RATINGS_TEST'`).Error)

	must(g.Raw(`INSERT INTO public.advertisers (page_id, page_name, page_like_count)
		VALUES ('RATINGS_TEST', 'Ratings Test Co', 1234) RETURNING id`).Scan(&ratingsAdvID).Error)

	// Insert a score row so the matview refresh picks it up.
	must(g.Exec(`INSERT INTO adtech.advertiser_scores
		(advertiser_id, cohort, gem_score, scaling_velocity, under_radar, funnel_intent,
		 active_ad_count, max_longevity_days, alert)
		VALUES (?, 'ios', 77.5, 0.6, 0.5, 0.8, 2, 20, false)`, ratingsAdvID).Error)

	// A bare ad + creative so the matview includes this advertiser.
	var adID int
	must(g.Raw(`INSERT INTO public.ads (external_ad_id, advertiser_id)
		VALUES ('RAD1', ?) RETURNING id`, ratingsAdvID).Scan(&adID).Error)
	must(g.Exec(`INSERT INTO public.creatives (ad_id, type, s3_key)
		VALUES (?, 'image', 'creatives/rad1.jpg')`, adID).Error)

	must(g.Exec(`REFRESH MATERIALIZED VIEW frontend.emerging_scaling_apps`).Error)
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}

func TestUpsertAndListRating(t *testing.T) {
	ctx := context.Background()
	email := "test@example.com"

	// Start clean.
	require.NoError(t, repositories.DeleteRating(ctx, ratingsDB, email, ratingsAdvID))

	// Upsert a "like" with a comment.
	require.NoError(t, repositories.UpsertRating(ctx, ratingsDB, email, ratingsAdvID, "like", "looks promising"))

	rows, err := repositories.ListRatings(ctx, ratingsDB, email)
	require.NoError(t, err)
	require.Contains(t, rows, ratingsAdvID, "advertiser must appear in result map")

	row := rows[ratingsAdvID]
	require.Equal(t, "like", row.Rating)
	require.Equal(t, "looks promising", row.Comment)
	require.NotEmpty(t, row.Features, "features snapshot must be populated")
}

func TestUpsertRatingOverwrites(t *testing.T) {
	ctx := context.Background()
	email := "test@example.com"

	require.NoError(t, repositories.UpsertRating(ctx, ratingsDB, email, ratingsAdvID, "like", "first"))
	require.NoError(t, repositories.UpsertRating(ctx, ratingsDB, email, ratingsAdvID, "dislike", "changed mind"))

	rows, err := repositories.ListRatings(ctx, ratingsDB, email)
	require.NoError(t, err)
	row := rows[ratingsAdvID]
	require.Equal(t, "dislike", row.Rating)
	require.Equal(t, "changed mind", row.Comment)
}

func TestDeleteRating(t *testing.T) {
	ctx := context.Background()
	email := "test@example.com"

	require.NoError(t, repositories.UpsertRating(ctx, ratingsDB, email, ratingsAdvID, "neutral", "meh"))
	require.NoError(t, repositories.DeleteRating(ctx, ratingsDB, email, ratingsAdvID))

	rows, err := repositories.ListRatings(ctx, ratingsDB, email)
	require.NoError(t, err)
	require.NotContains(t, rows, ratingsAdvID, "deleted row must not appear")
}

func TestListRatingsEmpty(t *testing.T) {
	ctx := context.Background()
	email := "nobody@example.com"

	rows, err := repositories.ListRatings(ctx, ratingsDB, email)
	require.NoError(t, err)
	require.Empty(t, rows)
}
