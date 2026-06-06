package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// ReviewQueue ports queries.py:review_queue — alerting gems not yet decided.
func ReviewQueue(ctx context.Context, g *gorm.DB, minScore float64, limit, offset int) ([]models.ReviewQueueRow, error) {
	rows := make([]models.ReviewQueueRow, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT advertiser_id, cohort, page_name, app_name, gem_score,
			        active_ad_count, max_longevity_days
			 FROM frontend.emerging_scaling_apps e
			 WHERE e.alert = TRUE AND e.gem_score >= ?
			   AND NOT EXISTS (SELECT 1 FROM frontend.review_decisions r
			                   WHERE r.advertiser_id = e.advertiser_id)
			 ORDER BY e.gem_score DESC, e.advertiser_id
			 LIMIT ? OFFSET ?`,
			minScore, limit, offset,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.ReviewQueueRow, 0)
	}
	return rows, err
}

// RecordDecision ports queries.py:record_decision — the only write in the API.
func RecordDecision(ctx context.Context, g *gorm.DB, advertiserID int, verdict, reviewer string, note *string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(
			`INSERT INTO frontend.review_decisions
			   (advertiser_id, verdict, reviewer, note, decided_at)
			 VALUES (?, ?, ?, ?, clock_timestamp())`,
			advertiserID, verdict, reviewer, note,
		).Error
	})
}
