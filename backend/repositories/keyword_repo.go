// Package repositories — discovery.dynamic_keywords reads and lifecycle writes.
//
// The keyword flywheel's whole control surface is these rows: status gates
// whether the sweep picks a keyword up, next_eligible_at gates when a resting
// one wakes, dry_streak drives automatic retirement. Changing a row takes
// effect on the next flywheel tick; nothing here needs a worker restart.
package repositories

import (
	"context"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// KeywordYieldParams filters the yield read. Zero values mean "no filter".
type KeywordYieldParams struct {
	Source    string
	Status    string
	MinSweeps int
	Limit     int
	Offset    int
}

// KeywordYield returns keywords ranked by keeper yield, highest first.
//
// The candidate counts come from a correlated aggregate over
// discovery.candidates rather than a JOIN + GROUP BY: a keyword with thousands
// of candidates would otherwise fan the row set out before grouping.
func KeywordYield(ctx context.Context, g *gorm.DB, p KeywordYieldParams) ([]models.KeywordYieldRow, error) {
	var rows []models.KeywordYieldRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT k.keyword,
			        k.source,
			        k.status,
			        k.sweeps_used,
			        k.dry_streak,
			        k.rest_count,
			        k.last_swept_at,
			        k.next_eligible_at,
			        COALESCE(c.candidates, 0) AS candidates,
			        COALESCE(c.keepers, 0)    AS keepers
			   FROM discovery.dynamic_keywords k
			   LEFT JOIN LATERAL (
			        SELECT count(*)                                  AS candidates,
			               count(*) FILTER (WHERE status = 'kept')   AS keepers
			          FROM discovery.candidates
			         WHERE source_keyword = k.keyword
			   ) c ON TRUE
			  WHERE (? = '' OR k.source = ?)
			    AND (? = '' OR k.status = ?)
			    AND k.sweeps_used >= ?
			  ORDER BY keepers DESC, candidates DESC, k.keyword
			  LIMIT ? OFFSET ?`,
			p.Source, p.Source, p.Status, p.Status, p.MinSweeps, p.Limit, p.Offset,
		).Scan(&rows).Error
	})
	return rows, err
}
