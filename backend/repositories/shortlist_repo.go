package repositories

import (
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// Shortlist ports queries.py:shortlist. cohort is passed twice: the CAST(...) IS
// NULL branch means "no cohort filter" when cohort is nil.
func Shortlist(g *gorm.DB, cohort *string, minScore float64, limit, offset int) ([]models.ShortlistRow, error) {
	rows := make([]models.ShortlistRow, 0)
	err := db.WithReadTx(g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT `+mvCols+` FROM frontend.emerging_scaling_apps
			 WHERE gem_score >= ?
			   AND (CAST(? AS text) IS NULL OR cohort = ?)
			 ORDER BY gem_score DESC, advertiser_id
			 LIMIT ? OFFSET ?`,
			minScore, cohort, cohort, limit, offset,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.ShortlistRow, 0)
	}
	return rows, err
}
