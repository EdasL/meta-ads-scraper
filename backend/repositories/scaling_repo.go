package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// Scaling ports queries.py:gem_scaling.
func Scaling(ctx context.Context, g *gorm.DB, advertiserID int) ([]models.ScalingPoint, error) {
	rows := make([]models.ScalingPoint, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT week_start, active_ad_count, distinct_creative_count, total_eu_reach
			 FROM adtech.app_advertiser_metrics
			 WHERE advertiser_id = ? ORDER BY week_start`,
			advertiserID,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.ScalingPoint, 0)
	}
	return rows, err
}
