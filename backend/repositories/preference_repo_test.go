package repositories_test

import (
	"context"
	"fmt"
	"testing"

	"github.com/stretchr/testify/require"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// seedPrefCandidates inserts n owned advertisers (sentinel cohort, distinct
// niches so the per-niche cap never collapses them) into the matview and returns
// their ids ordered by gem_score DESC. The queue's fresh bucket reads the global
// matview, so without an owned pool the stochastic-variety assertion below is at
// the mercy of however many rows sibling packages happen to have refreshed into
// the shared test DB — this makes the pool large and ambient-independent.
func seedPrefCandidates(g *gorm.DB, n int) []int64 {
	must(g.Exec(`DELETE FROM public.creatives WHERE ad_id IN (
		SELECT a.id FROM public.ads a JOIN public.advertisers adv ON adv.id = a.advertiser_id
		WHERE adv.page_id LIKE 'PREF_P%')`).Error)
	must(g.Exec(`DELETE FROM public.ads WHERE advertiser_id IN (
		SELECT id FROM public.advertisers WHERE page_id LIKE 'PREF_P%')`).Error)
	must(g.Exec(`DELETE FROM adtech.advertiser_scores WHERE advertiser_id IN (
		SELECT id FROM public.advertisers WHERE page_id LIKE 'PREF_P%')`).Error)
	must(g.Exec(`DELETE FROM public.advertisers WHERE page_id LIKE 'PREF_P%'`).Error)

	ids := make([]int64, n)
	for i := 0; i < n; i++ {
		page := fmt.Sprintf("PREF_P%d", i)
		var advID int64
		must(g.Raw(`INSERT INTO public.advertisers (page_id, page_name)
			VALUES (?, ?) RETURNING id`, page, "Pref "+page).Scan(&advID).Error)
		var adID int
		must(g.Raw(`INSERT INTO public.ads (external_ad_id, advertiser_id, is_active, start_date)
			VALUES (?, ?, true, now() - interval '20 days') RETURNING id`,
			page+"-AD", advID).Scan(&adID).Error)
		must(g.Exec(`INSERT INTO public.creatives (ad_id, type, s3_key)
			VALUES (?, 'image', ?)`, adID, "creatives/"+page+".jpg").Error)
		must(g.Exec(`INSERT INTO adtech.advertiser_scores
			(advertiser_id, cohort, gem_score, active_ad_count, max_longevity_days, alert, niche_keyword)
			VALUES (?, 'PREF_TEST', ?, 2, 30, false, ?)`,
			advID, float64(90-i), fmt.Sprintf("pref niche %d", i)).Error)
		ids[i] = advID
	}
	must(g.Exec(`REFRESH MATERIALIZED VIEW frontend.emerging_scaling_apps`).Error)
	return ids
}

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

	// Own a wide candidate pool so the queue is hermetic w.r.t. sibling packages
	// that insert/refresh the same matview in parallel. ids are gem_score DESC.
	ids := seedPrefCandidates(g, 8)

	// Score every owned candidate for this user; rate the first so it's excluded.
	for i, id := range ids {
		require.NoError(t, g.Exec(
			`INSERT INTO frontend.preference_scores
			   (user_email, advertiser_id, p_like, uncertainty, queue_rank, why, model_version)
			 VALUES (?, ?, ?, ?, ?, 'seeded', 'logit-v1')`,
			email, id, 0.9-float64(i)*0.05, 0.5, i).Error)
	}
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
