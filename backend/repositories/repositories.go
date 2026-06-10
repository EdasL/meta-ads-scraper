// Package repositories is the raw-SQL data layer. Each query is a verbatim port
// of src/agf/api/queries.py (and src/agf/monitoring/metrics.py), executed via
// GORM's Raw/Exec in raw-SQL mode. GORM rewrites `?` placeholders to `$n` for
// Postgres. All reads run inside a READ ONLY transaction (db.WithReadTx); the
// single write runs inside db.WithWriteTx.
package repositories

import (
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// mvCols is the frontend.emerging_scaling_apps column list, in order. It now
// includes the per-advertiser growth columns added by Track A's matview rebuild
// (105_emerging_matview_growth.sql): first_count, latest_count, weeks_observed,
// pct_change. They scan into mvScanRow's flat growth fields and are assembled
// into ShortlistRow.Growth by toShortlistRows (nil when the whole group is NULL).
const mvCols = `advertiser_id, cohort, page_name, app_id, app_name, gem_score, ` +
	`scaling_velocity, under_radar, funnel_intent, active_ad_count, ` +
	`max_longevity_days, alert, first_count, latest_count, weeks_observed, ` +
	`pct_change, refreshed_at`

// mvScanRow is the scan target for a matview row: the public ShortlistRow plus
// the flat growth columns. The growth columns are scanned here (rather than into
// ShortlistRow, whose Growth field is gorm:"-" so GORM never tries to bind a
// nested struct to flat columns) and folded into ShortlistRow.Growth.
type mvScanRow struct {
	models.ShortlistRow
	FirstCount  *int     `gorm:"column:first_count"`
	LatestCount *int     `gorm:"column:latest_count"`
	Weeks       *int     `gorm:"column:weeks_observed"`
	PctChange   *float64 `gorm:"column:pct_change"`
}

// growth returns the assembled GrowthInfo, or nil when every growth column is
// NULL (advertiser absent from adtech.advertiser_growth → LEFT JOIN nulls).
func (r mvScanRow) growth() *models.GrowthInfo {
	if r.FirstCount == nil && r.LatestCount == nil && r.Weeks == nil && r.PctChange == nil {
		return nil
	}
	return &models.GrowthInfo{
		FirstCount:  r.FirstCount,
		LatestCount: r.LatestCount,
		Weeks:       r.Weeks,
		PctChange:   r.PctChange,
	}
}

// toShortlistRows folds the flat scan rows into public ShortlistRows with Growth
// populated. The explainer + hero creative are layered on afterward by the
// caller (a single batched read each).
func toShortlistRows(scanned []mvScanRow) []models.ShortlistRow {
	rows := make([]models.ShortlistRow, len(scanned))
	for i, s := range scanned {
		row := s.ShortlistRow
		row.Growth = s.growth()
		rows[i] = row
	}
	return rows
}

// attachEnrichment layers the grug explainer and hero ad creative onto a page of
// shortlist rows with ONE batched read each (advertiser_id IN (ids)). Both are
// nil when absent — explainers are generated offline by cmd/explainers and many
// advertisers lack image creatives. It mutates rows in place. tx must be the
// active read transaction so the reads share the page's snapshot.
func attachEnrichment(tx *gorm.DB, rows []models.ShortlistRow) error {
	if len(rows) == 0 {
		return nil
	}
	ids := make([]int, len(rows))
	for i, r := range rows {
		ids[i] = r.AdvertiserID
	}

	// Explainers — one row per advertiser (adtech.advertiser_explainers PK).
	type explainerScan struct {
		AdvertiserID int `gorm:"column:advertiser_id"`
		models.ExplainerInfo
	}
	exps := make([]explainerScan, 0, len(ids))
	if err := tx.Raw(
		`SELECT advertiser_id, what_it_does, problem_solved, why_working
		 FROM adtech.advertiser_explainers
		 WHERE advertiser_id IN (?)`,
		ids,
	).Scan(&exps).Error; err != nil {
		return err
	}
	byExplainer := make(map[int]models.ExplainerInfo, len(exps))
	for _, e := range exps {
		byExplainer[e.AdvertiserID] = e.ExplainerInfo
	}

	// Hero creative — one image creative per advertiser via LATERAL pick. Active
	// ads first, then most recent (ad.last_seen — the matview's own recency
	// column), then highest creative id. Only image-type creatives with an s3_key
	// are eligible (the card renders a real image, falling back to a body_text
	// tile when none).
	type heroScan struct {
		AdvertiserID int `gorm:"column:advertiser_id"`
		models.CreativeInfo
	}
	heroes := make([]heroScan, 0, len(ids))
	if err := tx.Raw(
		`SELECT adv.id AS advertiser_id, c.type, c.s3_key, c.body_text, c.title
		 FROM public.advertisers adv
		 JOIN LATERAL (
		   SELECT cr.type, cr.s3_key, cr.body_text, cr.title
		   FROM public.creatives cr
		   JOIN public.ads ad ON ad.id = cr.ad_id
		   WHERE ad.advertiser_id = adv.id
		     AND cr.s3_key IS NOT NULL
		     AND lower(cr.type) IN ('image', 'img', 'photo', 'video')
		   ORDER BY (CASE WHEN lower(cr.type) IN ('image', 'img', 'photo') THEN 0 ELSE 1 END),
		            ad.is_active DESC NULLS LAST, ad.last_seen DESC NULLS LAST, cr.id DESC
		   LIMIT 1
		 ) c ON true
		 WHERE adv.id IN (?)`,
		ids,
	).Scan(&heroes).Error; err != nil {
		return err
	}
	byHero := make(map[int]models.CreativeInfo, len(heroes))
	for _, h := range heroes {
		byHero[h.AdvertiserID] = h.CreativeInfo
	}

	for i := range rows {
		if e, ok := byExplainer[rows[i].AdvertiserID]; ok {
			ex := e
			rows[i].Explainer = &ex
		}
		if h, ok := byHero[rows[i].AdvertiserID]; ok {
			hero := h
			rows[i].HeroCreative = &hero
		}
	}
	return nil
}
