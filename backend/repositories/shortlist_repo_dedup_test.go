// Package repositories_test: app-level dedup of the gems feed (the product
// view). Skipped unless AGF_IT_DB_URL is set. Seeds its own sentinel cohort so
// it never collides with the ratings fixture in the same package.
package repositories_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

const dedupCohort = "DEDUP_TEST"

// seedDedup builds one product fronted by 4 Meta pages across both stores:
//   - iOS listing "Dedup App" / dev "Dedup Co"      → pages P1(score 90), P2(80), P3(70)
//   - Android listing "Dedup App" / dev "Dedup Co LLC" → page P4(85)
//
// The normalized name+dev key merges all four into one product; P1 (top score)
// is the representative.
func seedDedup(g *gorm.DB) {
	// FK-safe teardown of any prior run (sentinel page_ids + apps).
	must(g.Exec(`DELETE FROM public.creatives WHERE ad_id IN (
		SELECT a.id FROM public.ads a JOIN public.advertisers adv ON adv.id = a.advertiser_id
		WHERE adv.page_id LIKE 'DEDUP_P%')`).Error)
	must(g.Exec(`DELETE FROM public.ads WHERE advertiser_id IN (
		SELECT id FROM public.advertisers WHERE page_id LIKE 'DEDUP_P%')`).Error)
	must(g.Exec(`DELETE FROM adtech.advertiser_scores WHERE advertiser_id IN (
		SELECT id FROM public.advertisers WHERE page_id LIKE 'DEDUP_P%')`).Error)
	must(g.Exec(`DELETE FROM public.advertisers WHERE page_id LIKE 'DEDUP_P%'`).Error)
	must(g.Exec(`DELETE FROM public.apps WHERE store_id IN ('ded-ios', 'ded-android')`).Error)

	var iosApp, androidApp int
	must(g.Raw(`INSERT INTO public.apps (store, store_id, name, developer, is_emerging)
		VALUES ('ios','ded-ios','Dedup App','Dedup Co',true) RETURNING id`).Scan(&iosApp).Error)
	must(g.Raw(`INSERT INTO public.apps (store, store_id, name, developer, is_emerging)
		VALUES ('android','ded-android','Dedup App','Dedup Co LLC',true) RETURNING id`).Scan(&androidApp).Error)

	mk := func(page string, appID, score, ads int) {
		var advID int
		must(g.Raw(`INSERT INTO public.advertisers (page_id, page_name)
			VALUES (?, ?) RETURNING id`, page, "Dedup "+page).Scan(&advID).Error)
		var adID int
		must(g.Raw(`INSERT INTO public.ads (external_ad_id, advertiser_id, app_id, is_active, start_date)
			VALUES (?, ?, ?, true, now() - interval '5 days') RETURNING id`,
			page+"-AD", advID, appID).Scan(&adID).Error)
		must(g.Exec(`INSERT INTO public.creatives (ad_id, type, s3_key)
			VALUES (?, 'image', ?)`, adID, "creatives/"+page+".jpg").Error)
		must(g.Exec(`INSERT INTO adtech.advertiser_scores
			(advertiser_id, cohort, gem_score, active_ad_count, max_longevity_days, alert)
			VALUES (?, ?, ?, ?, 30, false)`, advID, dedupCohort, float64(score), ads).Error)
	}
	mk("DEDUP_P1", iosApp, 90, 3)
	mk("DEDUP_P2", iosApp, 80, 2)
	mk("DEDUP_P3", iosApp, 70, 2)
	mk("DEDUP_P4", androidApp, 85, 4)

	must(g.Exec(`REFRESH MATERIALIZED VIEW frontend.emerging_scaling_apps`).Error)
}

func TestShortlistCollapsesProduct(t *testing.T) {
	requireRatingsDB(t)
	seedDedup(ratingsDB)
	ctx := context.Background()

	cohort := dedupCohort
	rows, total, err := repositories.Shortlist(ctx, ratingsDB, repositories.ShortlistParams{
		Cohort: &cohort, Limit: 50, Offset: 0,
	})
	require.NoError(t, err)
	require.Equal(t, 1, total, "4 pages across 2 stores collapse to one product")
	require.Len(t, rows, 1)

	row := rows[0]
	require.EqualValues(t, 90, row.GemScore, "highest-score page represents the product")
	require.NotNil(t, row.ActiveAdCount)
	require.Equal(t, 3+2+2+4, *row.ActiveAdCount, "footprint summed across all pages")
	require.Equal(t, 4, row.PageCount)
	require.NotNil(t, row.Stores)
	require.Equal(t, "ios+android", *row.Stores)
}

func TestDossierListsAllPages(t *testing.T) {
	requireRatingsDB(t)
	seedDedup(ratingsDB)
	ctx := context.Background()

	// Representative of the Dedup product is the top-score iOS page (P1). Find it.
	cohort := dedupCohort
	rows, _, err := repositories.Shortlist(ctx, ratingsDB, repositories.ShortlistParams{
		Cohort: &cohort, Limit: 1,
	})
	require.NoError(t, err)
	require.Len(t, rows, 1)

	d, err := repositories.Dossier(ctx, ratingsDB, rows[0].AdvertiserID)
	require.NoError(t, err)
	require.NotNil(t, d)
	require.Len(t, d.Pages, 4, "all four pages across both stores")

	// Sorted by active ads desc — P4 (4 active) leads.
	require.GreaterOrEqual(t, d.Pages[0].ActiveAds, d.Pages[1].ActiveAds)
	for _, p := range d.Pages {
		require.NotEmpty(t, p.PageName)
		require.GreaterOrEqual(t, p.TotalAds, 1)
	}
}
