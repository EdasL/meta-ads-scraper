package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// AppMetrics returns the app-side growth series (adtech.app_metrics daily
// snapshots) for every app linked to the advertiser's ads, ordered by
// snapshot_date ASC. An advertiser with no linked apps (or no snapshots yet)
// returns an empty slice, never nil.
func AppMetrics(ctx context.Context, g *gorm.DB, advertiserID int) ([]models.AppMetricPoint, error) {
	rows := make([]models.AppMetricPoint, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT m.app_id, ap.store, m.snapshot_date, m.rating, m.rating_count, m.installs
			 FROM adtech.app_metrics m
			 JOIN public.apps ap ON ap.id = m.app_id
			 WHERE m.app_id IN (
			   SELECT DISTINCT a.app_id FROM public.ads a
			   WHERE a.advertiser_id = ? AND a.app_id IS NOT NULL
			 )
			 ORDER BY m.snapshot_date, m.app_id`,
			advertiserID,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.AppMetricPoint, 0)
	}
	return rows, err
}
