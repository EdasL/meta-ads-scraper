package repositories_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

func TestPreferenceQueue(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB

	email := "pref-repo-test@fielddesk.local"
	// Clean slate for this user.
	require.NoError(t, g.Exec(
		`DELETE FROM frontend.preference_scores WHERE user_email = ?`, email).Error)
	require.NoError(t, g.Exec(
		`DELETE FROM frontend.app_ratings WHERE user_email = ?`, email).Error)

	// Pick two advertisers that exist in the matview.
	var ids []int64
	require.NoError(t, g.Raw(
		`SELECT advertiser_id FROM frontend.emerging_scaling_apps LIMIT 2`).Scan(&ids).Error)
	if len(ids) < 2 {
		t.Skip("need >=2 matview rows in the test DB")
	}
	// Rank both; rate the first so it must be excluded.
	require.NoError(t, g.Exec(
		`INSERT INTO frontend.preference_scores
		   (user_email, advertiser_id, p_like, uncertainty, queue_rank, why, model_version)
		 VALUES (?, ?, 0.9, 0.2, 0, 'has funnel', 'logit-v1'),
		        (?, ?, 0.4, 0.9, 1, '', 'logit-v1')`,
		email, ids[0], email, ids[1]).Error)
	require.NoError(t, g.Exec(
		`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, features)
		 VALUES (?, ?, 'like', '{}')`, email, ids[0]).Error)

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
}
