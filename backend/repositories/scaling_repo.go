package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// Scaling serves the weekly ad-activity series for an advertiser, reconstructed
// at read time from public.ads start/end intervals (it replaced the fixed
// 16-week adtech.app_advertiser_metrics read — see the growth-signals redesign
// spec, 2026-06-10). The window is advertiser-relative: from the week of their
// first stored ad, capped at 26 weeks back, up to the current week. Active ads
// treat their end as now() — their stored end_date is merely "last seen
// delivering". launched_count = ads whose start_date falls in the week;
// ended_count = ended (NOT is_active) ads whose end_date falls in the week.
// An advertiser with no dated ads yields an empty series, not an error (the
// HAVING drops the bounds row, so weeks generates nothing).
// distinct_creative_count is deprecated — served as 0 for contract
// compatibility; total_eu_reach is served as null.
func Scaling(ctx context.Context, g *gorm.DB, advertiserID int) ([]models.ScalingPoint, error) {
	rows := make([]models.ScalingPoint, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`WITH bounds AS (
			   SELECT GREATEST(date_trunc('week', min(start_date)),
			                   date_trunc('week', now()) - interval '25 weeks') AS w0,
			          date_trunc('week', now()) AS wn
			   FROM public.ads WHERE advertiser_id = ?
			   HAVING min(start_date) IS NOT NULL
			 ), weeks AS (
			   SELECT generate_series(w0, wn, interval '1 week') AS week_start FROM bounds
			 )
			 SELECT w.week_start::date AS week_start,
			        count(a.id) FILTER (WHERE a.start_date < w.week_start + interval '7 days'
			          AND (CASE WHEN a.is_active THEN now() ELSE a.end_date END) >= w.week_start)
			          AS active_ad_count,
			        0 AS distinct_creative_count,
			        NULL::bigint AS total_eu_reach,
			        count(a.id) FILTER (WHERE a.start_date >= w.week_start
			          AND a.start_date < w.week_start + interval '7 days') AS launched_count,
			        count(a.id) FILTER (WHERE NOT a.is_active AND a.end_date >= w.week_start
			          AND a.end_date < w.week_start + interval '7 days') AS ended_count
			 FROM weeks w LEFT JOIN public.ads a ON a.advertiser_id = ?
			 GROUP BY 1 ORDER BY 1`,
			advertiserID, advertiserID,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.ScalingPoint, 0)
	}
	return rows, err
}
