package repositories_test

import (
	"context"
	"fmt"
	"testing"

	"github.com/stretchr/testify/require"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
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

// seedEmbedding inserts an embedding row (a 2-dim vector is fine: the model/dot
// code handles any length; the dim=768 column value just gates the cache query).
func seedEmbedding(g *gorm.DB, advID int64, vec string) {
	must(g.Exec(`DELETE FROM frontend.app_embeddings WHERE advertiser_id = ?`, advID).Error)
	must(g.Exec(`INSERT INTO frontend.app_embeddings (advertiser_id, embedding, content_hash, dim)
	             VALUES (?, ?, 'seed', 768)`, advID, vec).Error)
}

func TestPreferenceQueueServeTime(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	email := "pref-live-test@fielddesk.local"

	require.NoError(t, g.Exec(`DELETE FROM frontend.app_ratings WHERE user_email = ?`, email).Error)
	ids := seedPrefCandidates(g, 12) // gem_score DESC: ids[0] highest

	// Embeddings: ids[0..9] on-axis vectors — ids[0..4] near e1, ids[5..9] near e2.
	// ids[10..11] get NO embedding (fresh lane).
	for i := 0; i < 5; i++ {
		seedEmbedding(g, ids[i], "{0.9950371902099892,0.09950371902099892}")
	}
	for i := 5; i < 10; i++ {
		seedEmbedding(g, ids[i], "{0.09950371902099892,0.9950371902099892}")
	}
	must(g.Exec(`DELETE FROM frontend.app_embeddings WHERE advertiser_id IN (?, ?)`,
		ids[10], ids[11]).Error)
	repositories.ResetPrefEmbCacheForTest()

	// 8 likes near e1 + 1 dislike near e2 → model direction ≈ e1.
	// Rate ids[0..2] like (they're near e1) and ids[5] dislike; the remaining
	// 5 likes come from extra sentinel advertisers outside the matview pool so
	// the queue still has unrated e1-candidates (ids[3], ids[4]) to rank.
	// Clean up prior-run extras first (do NOT rely on ON CONFLICT — advertisers
	// may have no unique constraint on page_id).
	must(g.Exec(`DELETE FROM frontend.app_embeddings WHERE advertiser_id IN (
		SELECT id FROM public.advertisers WHERE page_id LIKE 'PREF_XLIKE%')`).Error)
	must(g.Exec(`DELETE FROM frontend.app_ratings WHERE advertiser_id IN (
		SELECT id FROM public.advertisers WHERE page_id LIKE 'PREF_XLIKE%')`).Error)
	must(g.Exec(`DELETE FROM public.advertisers WHERE page_id LIKE 'PREF_XLIKE%'`).Error)
	extra := make([]int64, 5)
	for i := range extra {
		var advID int64
		must(g.Raw(`INSERT INTO public.advertisers (page_id, page_name)
			VALUES (?, ?) RETURNING id`,
			fmt.Sprintf("PREF_XLIKE%d", i), fmt.Sprintf("Pref extra %d", i)).Scan(&advID).Error)
		extra[i] = advID
		seedEmbedding(g, advID, "{0.9950371902099892,0.09950371902099892}")
	}
	for _, id := range append([]int64{ids[0], ids[1], ids[2]}, extra...) {
		must(g.Exec(`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, features)
			VALUES (?, ?, 'like', '{}')
			ON CONFLICT (user_email, advertiser_id) DO UPDATE SET rating = 'like'`,
			email, id).Error)
	}
	must(g.Exec(`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, features)
		VALUES (?, ?, 'dislike', '{}')
		ON CONFLICT (user_email, advertiser_id) DO UPDATE SET rating = 'dislike'`,
		email, ids[5]).Error)

	// Limit = the whole matview pool: the shared test DB can hold matview rows
	// committed by other tests (a fixed 12 let those crowd the low-ranked
	// off-taste sentinel out of the queue), so size the limit to serve every
	// unrated candidate and keep the assertions deterministic.
	var poolN int
	must(g.Raw(`SELECT count(*) FROM frontend.emerging_scaling_apps`).Scan(&poolN).Error)
	rows, err := repositories.PreferenceQueue(context.Background(), g, email, poolN+12, nil)
	require.NoError(t, err)
	require.NotEmpty(t, rows)

	byID := map[int64]models.PreferenceCardRow{}
	for _, r := range rows {
		byID[int64(r.AdvertiserID)] = r
	}
	require.NotContains(t, byID, ids[0], "rated app must be excluded")
	require.NotContains(t, byID, ids[5], "disliked app must be excluded")

	// Live taste: an unrated e1-candidate must out-p_like an unrated e2-candidate.
	require.Contains(t, byID, ids[3], "unrated e1 candidate must be served")
	require.Contains(t, byID, ids[6], "unrated e2 candidate must be served")
	require.Greater(t, byID[ids[3]].PLike, byID[ids[6]].PLike,
		"on-taste candidate must outscore off-taste")
	require.Contains(t, byID[ids[3]].Why, "similar to",
		"fitted model must name a liked app")

	// exclude param: excluded ids never come back.
	rows2, err := repositories.PreferenceQueue(context.Background(), g, email, 12,
		[]int64{ids[3], ids[4]})
	require.NoError(t, err)
	for _, r := range rows2 {
		require.NotEqual(t, ids[3], int64(r.AdvertiserID), "excluded id served")
		require.NotEqual(t, ids[4], int64(r.AdvertiserID), "excluded id served")
	}
}

func TestPreferenceQueueColdstart(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	email := "pref-cold-test@fielddesk.local"

	require.NoError(t, g.Exec(`DELETE FROM frontend.app_ratings WHERE user_email = ?`, email).Error)
	ids := seedPrefCandidates(g, 6)
	for _, id := range ids {
		seedEmbedding(g, id, "{1.0,0.0}")
	}
	repositories.ResetPrefEmbCacheForTest()

	// One like (< MinLikes) → coldstart: embedded candidates serve gem_score/100.
	must(g.Exec(`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, features)
		VALUES (?, ?, 'like', '{}')
		ON CONFLICT (user_email, advertiser_id) DO UPDATE SET rating = 'like'`,
		email, ids[0]).Error)

	rows, err := repositories.PreferenceQueue(context.Background(), g, email, 6, nil)
	require.NoError(t, err)
	for _, r := range rows {
		for i, id := range ids {
			if int64(r.AdvertiserID) == id {
				// seedPrefCandidates seeds gem_score = 90 - i
				require.InDelta(t, float64(90-i)/100.0, r.PLike, 1e-9,
					"coldstart p_like must be gem_score/100")
				require.Equal(t, "", r.Why, "coldstart why must be empty")
			}
		}
	}
}
