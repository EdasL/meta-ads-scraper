package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// prefScanRow is the scan target for a preference queue row. It mirrors mvScanRow
// but adds the per-user preference columns. GORM's Scan only handles one level of
// struct embedding, so we embed models.ShortlistRow directly (not mvScanRow) and
// repeat the flat growth fields here.
type prefScanRow struct {
	models.ShortlistRow
	// growth columns (mirrors mvScanRow)
	FirstCount  *int     `gorm:"column:first_count"`
	LatestCount *int     `gorm:"column:latest_count"`
	Weeks       *int     `gorm:"column:weeks_observed"`
	PctChange   *float64 `gorm:"column:pct_change"`
	// preference columns
	PLike float64 `gorm:"column:p_like"`
	Why   string  `gorm:"column:why"`
}

func (r prefScanRow) toMvScanRow() mvScanRow {
	return mvScanRow{
		ShortlistRow: r.ShortlistRow,
		FirstCount:   r.FirstCount,
		LatestCount:  r.LatestCount,
		Weeks:        r.Weeks,
		PctChange:    r.PctChange,
	}
}

// PreferenceQueue returns the user's review queue: matview rows joined to their
// preference_scores, excluding apps the user already rated, ordered by
// queue_rank. Hero creative + growth are attached via the shared enrichment
// pass inside the same read transaction. The CTE aliases the score table's
// advertiser_id so the bare matview columns in mvCols stay unambiguous.
func PreferenceQueue(ctx context.Context, g *gorm.DB, userEmail string, limit int) ([]models.PreferenceCardRow, error) {
	var outRows []models.PreferenceCardRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		rows := make([]prefScanRow, 0)
		if err := tx.Raw(
			`WITH ranked AS (
			   SELECT ps.advertiser_id AS adv_id, ps.p_like, ps.why, ps.queue_rank
			   FROM frontend.preference_scores ps
			   WHERE ps.user_email = ?
			     AND NOT EXISTS (
			       SELECT 1 FROM frontend.app_ratings r
			       WHERE r.user_email = ps.user_email
			         AND r.advertiser_id = ps.advertiser_id)
			 )
			 SELECT `+mvCols+`, ranked.p_like, ranked.why
			 FROM frontend.emerging_scaling_apps
			 JOIN ranked ON ranked.adv_id = frontend.emerging_scaling_apps.advertiser_id
			 ORDER BY ranked.queue_rank
			 LIMIT ?`,
			userEmail, limit,
		).Scan(&rows).Error; err != nil {
			return err
		}
		mvRows := make([]mvScanRow, len(rows))
		for i, r := range rows {
			mvRows[i] = r.toMvScanRow()
		}
		shortRows := toShortlistRows(mvRows)
		if err := attachEnrichment(tx, shortRows); err != nil {
			return err
		}
		outRows = make([]models.PreferenceCardRow, len(shortRows))
		for i := range shortRows {
			outRows[i] = models.PreferenceCardRow{
				ShortlistRow: shortRows[i],
				PLike:        rows[i].PLike,
				Why:          rows[i].Why,
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return outRows, nil
}
