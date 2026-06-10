package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// Shortlist ports queries.py:shortlist. cohort is passed twice: the CAST(...) IS
// NULL branch means "no cohort filter" when cohort is nil.
func Shortlist(ctx context.Context, g *gorm.DB, cohort *string, minScore float64, limit, offset int) ([]models.ShortlistRow, error) {
	rows := make([]models.ShortlistRow, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		scanned := make([]mvScanRow, 0)
		// Only surface advertisers we can actually show an ad for — i.e. with at
		// least one downloaded creative. This hides incumbent/firehose advertisers
		// that have a score but no scraped ad media (empty hero tiles, flat counts).
		if err := tx.Raw(
			`SELECT `+mvCols+` FROM frontend.emerging_scaling_apps
			 WHERE gem_score >= ?
			   AND (CAST(? AS text) IS NULL OR cohort = ?)
			   AND EXISTS (
			     SELECT 1 FROM public.creatives c
			     JOIN public.ads a ON a.id = c.ad_id
			     WHERE a.advertiser_id = frontend.emerging_scaling_apps.advertiser_id
			       AND c.s3_key IS NOT NULL
			   )
			 ORDER BY gem_score DESC, advertiser_id
			 LIMIT ? OFFSET ?`,
			minScore, cohort, cohort, limit, offset,
		).Scan(&scanned).Error; err != nil {
			return err
		}
		rows = toShortlistRows(scanned)
		return attachEnrichment(tx, rows)
	})
	if rows == nil {
		rows = make([]models.ShortlistRow, 0)
	}
	return rows, err
}
