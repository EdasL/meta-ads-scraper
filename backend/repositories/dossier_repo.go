package repositories

import (
	"context"
	"database/sql"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// dossierEnrichment scans the advertiser-enrichment row (self-join for the
// owning company name) plus the advertiser-level Meta signals (migration 109).
type dossierEnrichment struct {
	PageID                 *string         `gorm:"column:page_id"`
	PageCategory           *string         `gorm:"column:page_category"`
	IgUsername             *string         `gorm:"column:ig_username"`
	IgFollowers            *int            `gorm:"column:ig_followers"`
	ParentCompany          *string         `gorm:"column:parent_company"`
	AdsLastSeen            *models.APITime `gorm:"column:ads_last_seen"`
	PageLikeCount          *int64          `gorm:"column:page_like_count"`
	MetaActiveAds          *int            `gorm:"column:meta_active_ads"`
	MetaActiveAdsCapped    bool            `gorm:"column:meta_active_ads_capped"`
	MetaActiveAdsCheckedAt *models.APITime `gorm:"column:meta_active_ads_checked_at"`
}

// dossierApp scans an apps row plus its primary key (needed to attach reviews;
// the id itself is not part of the AppInfo JSON contract).
type dossierApp struct {
	models.AppInfo
	ID int `gorm:"column:id"`
}

// dossierReview scans an app_reviews row plus its owning app id.
type dossierReview struct {
	models.AppReview
	AppID int `gorm:"column:app_id"`
}

// adChange4w scans the trailing 4-week change in the ad signals, reconstructed
// from each ad's run interval (start_date/end_date) in public.ads — no snapshot
// table needed. An ad counts as active on a date D when it started on or before
// D and had not ended by D; "running 30+ days on D" additionally requires it to
// have started 30+ days before D.
type adChange4w struct {
	ActiveDelta    int `gorm:"column:active_delta"`
	Running30Delta int `gorm:"column:running30_delta"`
}

// Dossier ports queries.py:gem_dossier — five reads in one read-only snapshot.
// Returns (nil, nil) when the advertiser is not in the matview (→ 404).
func Dossier(ctx context.Context, g *gorm.DB, advertiserID int) (*models.GemDossier, error) {
	var out *models.GemDossier
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		scanned := make([]mvScanRow, 0, 1)
		if err := tx.Raw(
			`SELECT `+mvCols+` FROM frontend.emerging_scaling_apps WHERE advertiser_id = ?`,
			advertiserID,
		).Scan(&scanned).Error; err != nil {
			return err
		}
		if len(scanned) == 0 {
			return nil // out stays nil → caller returns 404
		}
		gems := toShortlistRows(scanned)

		// LEFT JOIN: an advertiser with no domain_id yields a NULL domain. GORM's
		// Scan leaves NullString invalid (not an error) on a null/absent row, so
		// d.Domain correctly stays nil — do NOT add a no-rows error check here.
		var domain sql.NullString
		if err := tx.Raw(
			`SELECT d.domain FROM public.advertisers a
			 LEFT JOIN public.domains d ON d.id = a.domain_id WHERE a.id = ?`,
			advertiserID,
		).Scan(&domain).Error; err != nil {
			return err
		}

		appRows := make([]dossierApp, 0)
		if err := tx.Raw(
			`SELECT DISTINCT ap.id, ap.store, ap.store_id, ap.name, ap.developer, ap.genre,
			        ap.release_date, ap.rating, ap.rating_count, ap.is_emerging,
			        ap.description, ap.icon_url, ap.screenshots, ap.price_label,
			        ap.installs, ap.updated_at
			 FROM public.apps ap JOIN public.ads a ON a.app_id = ap.id
			 WHERE a.advertiser_id = ?`,
			advertiserID,
		).Scan(&appRows).Error; err != nil {
			return err
		}

		// Top-5 most recent store reviews per app, one batched read. Emerging
		// apps often have zero reviews — every app still gets a non-nil slice.
		reviewsByApp := make(map[int][]models.AppReview, len(appRows))
		if len(appRows) > 0 {
			appIDs := make([]int, len(appRows))
			for i, ar := range appRows {
				appIDs[i] = ar.ID
			}
			revRows := make([]dossierReview, 0)
			if err := tx.Raw(
				`SELECT app_id, author, rating, title, content, reviewed_at
				 FROM (
				   SELECT r.app_id, r.author, r.rating, r.title, r.content, r.reviewed_at,
				          row_number() OVER (
				            PARTITION BY r.app_id
				            ORDER BY r.reviewed_at DESC NULLS LAST
				          ) AS rn
				   FROM public.app_reviews r WHERE r.app_id IN (?)
				 ) ranked
				 WHERE rn <= 5
				 ORDER BY app_id, reviewed_at DESC NULLS LAST`,
				appIDs,
			).Scan(&revRows).Error; err != nil {
				return err
			}
			for _, rr := range revRows {
				reviewsByApp[rr.AppID] = append(reviewsByApp[rr.AppID], rr.AppReview)
			}
		}
		apps := make([]models.AppInfo, len(appRows))
		for i, ar := range appRows {
			app := ar.AppInfo
			app.Reviews = reviewsByApp[ar.ID]
			if app.Reviews == nil {
				app.Reviews = make([]models.AppReview, 0)
			}
			apps[i] = app
		}

		funnels := make([]models.FunnelInfo, 0)
		if err := tx.Raw(
			`SELECT f.landing_url, f.final_url_after_redirect, f.screenshot_s3_key
			 FROM public.funnels f JOIN public.ads a ON a.id = f.ad_id
			 WHERE a.advertiser_id = ?`,
			advertiserID,
		).Scan(&funnels).Error; err != nil {
			return err
		}

		// One card per AD (DISTINCT ON a.id) carrying its best creative — stored
		// media (s3_key) first, then video over image over the text backstop — so
		// an ad with both a media row and a text row renders once, with media.
		// body_text/title fall back to any sibling creative row's text (a media
		// row often has none; the keeper-history backstop carries the ad copy).
		//   - ad_days_running: active ads count up to today; ended ads freeze at
		//     end_date − start_date; NULL when start_date is unknown.
		//   - publisher_platforms (text[]) is cast to JSON text so it scans
		//     through models.StringList without driver array plumbing.
		//   - eu_total_reach: latest non-NULL snapshot for the ad (US ads have
		//     none → null).
		// Best ads first: active ads, then highest EU reach (US ads have none →
		// sort last), then longest-running, then ads with stored media as a
		// tiebreaker — the order the reel shows its top 5.
		creatives := make([]models.CreativeInfo, 0)
		if err := tx.Raw(
			`SELECT * FROM (
			   SELECT DISTINCT ON (a.id)
			          c.type, c.s3_key,
			          COALESCE(c.body_text, bt.body_text) AS body_text,
			          COALESCE(c.title, bt.title) AS title,
			          a.external_ad_id, a.cta_text, a.link_url,
			          a.start_date::date AS ad_started_on,
			          a.end_date::date AS ad_ended_on,
			          a.is_active AS ad_active,
			          CASE
			            WHEN a.start_date IS NULL THEN NULL
			            WHEN COALESCE(a.is_active, false) THEN CURRENT_DATE - a.start_date::date
			            WHEN a.end_date IS NOT NULL THEN a.end_date::date - a.start_date::date
			          END AS ad_days_running,
			          to_json(a.publisher_platforms)::text AS publisher_platforms,
			          m.eu_total_reach
			   FROM public.creatives c JOIN public.ads a ON a.id = c.ad_id
			   LEFT JOIN LATERAL (
			     SELECT am.eu_total_reach FROM adtech.ad_metrics am
			     WHERE am.ad_id = a.id AND am.eu_total_reach IS NOT NULL
			     ORDER BY am.snapshot_date DESC LIMIT 1
			   ) m ON true
			   LEFT JOIN LATERAL (
			     SELECT cb.body_text, cb.title FROM public.creatives cb
			     WHERE cb.ad_id = a.id AND (cb.body_text IS NOT NULL OR cb.title IS NOT NULL)
			     ORDER BY cb.id LIMIT 1
			   ) bt ON true
			   WHERE a.advertiser_id = ?
			   ORDER BY a.id,
			            (c.s3_key IS NOT NULL) DESC,
			            CASE c.type WHEN 'video' THEN 0 WHEN 'image' THEN 1 ELSE 2 END,
			            c.id
			 ) per_ad
			 ORDER BY COALESCE(ad_active, false) DESC,
			          eu_total_reach DESC NULLS LAST,
			          ad_days_running DESC NULLS LAST,
			          (s3_key IS NOT NULL) DESC`,
			advertiserID,
		).Scan(&creatives).Error; err != nil {
			return err
		}

		enr := make([]dossierEnrichment, 0, 1)
		if err := tx.Raw(
			`SELECT a.page_id, a.page_category, a.ig_username, a.ig_followers,
			        p.page_name AS parent_company,
			        a.last_seen AS ads_last_seen,
			        a.page_like_count, a.meta_active_ads, a.meta_active_ads_capped,
			        a.meta_active_ads_checked_at
			 FROM public.advertisers a
			 LEFT JOIN public.advertisers p ON p.id = a.parent_company_id
			 WHERE a.id = ?`,
			advertiserID,
		).Scan(&enr).Error; err != nil {
			return err
		}

		// Ad-lifecycle summary — one aggregate over the advertiser's stored ads.
		// Zero ads still yields one row (counts 0, dates null) because an
		// aggregate without GROUP BY always returns a row. median_run_days
		// covers ended ads only and is null until at least 5 have ended
		// (percentile_cont ignores rows whose run length is null);
		// running_30d_plus = active ads started 30+ days ago; the 4w windows
		// are 28 days.
		life := make([]models.AdLifecycle, 0, 1)
		if err := tx.Raw(
			`SELECT count(*) AS ads_seen,
			        count(*) FILTER (WHERE a.is_active) AS active_now,
			        count(*) FILTER (WHERE a.is_active
			          AND a.start_date <= now() - interval '30 days') AS running_30d_plus,
			        CASE WHEN count(*) FILTER (WHERE NOT a.is_active) >= 5
			             THEN round(percentile_cont(0.5) WITHIN GROUP (
			                    ORDER BY (a.end_date::date - a.start_date::date))
			                  FILTER (WHERE NOT a.is_active))::int
			        END AS median_run_days,
			        min(a.start_date)::date AS first_ad_at,
			        count(*) FILTER (WHERE a.start_date >= now() - interval '28 days') AS launched_4w,
			        count(*) FILTER (WHERE NOT a.is_active
			          AND a.end_date >= now() - interval '28 days') AS ended_4w
			 FROM public.ads a WHERE a.advertiser_id = ?`,
			advertiserID,
		).Scan(&life).Error; err != nil {
			return err
		}

		// Single-row explainer read (adtech.advertiser_explainers PK). Stays nil
		// when no row exists yet (generated offline by cmd/explainers).
		exps := make([]models.ExplainerInfo, 0, 1)
		if err := tx.Raw(
			`SELECT what_it_does, problem_solved, why_working
			 FROM adtech.advertiser_explainers WHERE advertiser_id = ?`,
			advertiserID,
		).Scan(&exps).Error; err != nil {
			return err
		}

		d := &models.GemDossier{
			Gem:       gems[0],
			Apps:      apps,
			Funnels:   funnels,
			Creatives: creatives,
			Growth:    gems[0].Growth, // folded from the matview growth columns
		}
		if len(exps) > 0 {
			ex := exps[0]
			d.Explainer = &ex
		}
		if len(life) > 0 {
			lf := life[0]
			d.AdLifecycle = &lf
		}
		if domain.Valid {
			d.Domain = &domain.String
		}
		if len(enr) > 0 {
			d.PageID = enr[0].PageID
			d.PageCategory = enr[0].PageCategory
			d.IgUsername = enr[0].IgUsername
			d.IgFollowers = enr[0].IgFollowers
			d.ParentCompany = enr[0].ParentCompany
			d.AdsLastSeen = enr[0].AdsLastSeen
			d.PageLikeCount = enr[0].PageLikeCount
			d.MetaActiveAds = enr[0].MetaActiveAds
			d.MetaActiveAdsCapped = enr[0].MetaActiveAdsCapped
			d.MetaActiveAdsCheckedAt = enr[0].MetaActiveAdsCheckedAt
		}

		// 4-week change in the ad signals, reconstructed from each ad's run
		// interval — count as of today minus count as of 28 days ago. Exact and
		// available immediately (no snapshot history needed) because every ad
		// carries its own start_date/end_date. Attached only when the advertiser
		// has at least one datable ad.
		// An ad was running on date D when it started on or before D and had not
		// stopped by D. A still-active ad has no real end (its stored end_date is
		// just the last day it was seen), so is_active stands in for "running
		// through now" — the same rule the scaling-series reconstruction uses.
		chg := make([]adChange4w, 0, 1)
		if err := tx.Raw(
			`SELECT
			   count(*) FILTER (WHERE start_date <= now()
			     AND (is_active OR end_date >= now()))
			   - count(*) FILTER (WHERE start_date <= now() - interval '28 days'
			     AND (is_active OR end_date >= now() - interval '28 days'))
			     AS active_delta,
			   count(*) FILTER (WHERE start_date <= now() - interval '30 days'
			     AND (is_active OR end_date >= now()))
			   - count(*) FILTER (WHERE start_date <= now() - interval '58 days'
			     AND (is_active OR end_date >= now() - interval '28 days'))
			     AS running30_delta
			 FROM public.ads
			 WHERE advertiser_id = ? AND start_date IS NOT NULL`,
			advertiserID,
		).Scan(&chg).Error; err != nil {
			return err
		}
		if len(chg) > 0 {
			d.MetricChange4w = &models.MetricChange4w{
				ActiveAds:  chg[0].ActiveDelta,
				Running30d: chg[0].Running30Delta,
			}
		}
		out = d
		return nil
	})
	return out, err
}
