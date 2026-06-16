package repositories

import (
	"context"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// InsertEnrichJob creates a 'running' job row and returns its id.
func InsertEnrichJob(ctx context.Context, g *gorm.DB, advertiserID int) (int, error) {
	var id int
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`INSERT INTO public.enrich_jobs (advertiser_id)
			 VALUES (?) RETURNING id`,
			advertiserID,
		).Scan(&id).Error
	})
	return id, err
}

// LatestEnrichJob returns the most recent job row for an advertiser, or nil.
func LatestEnrichJob(ctx context.Context, g *gorm.DB, advertiserID int) (*models.EnrichJobRow, error) {
	var rows []models.EnrichJobRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT id, status, error_text, started_at, finished_at
			 FROM public.enrich_jobs
			 WHERE advertiser_id = ?
			 ORDER BY started_at DESC
			 LIMIT 1`,
			advertiserID,
		).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}
