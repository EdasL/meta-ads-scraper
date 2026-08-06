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

// ControlKeywords applies one lifecycle action to a batch of keywords and
// reports each row's before/after status. Keywords that do not exist are
// returned in the second slice instead of failing the call — a batch of
// winners pasted from a yield read should not be rejected wholesale because
// one entry has since been pruned.
//
// 'seed' is the exception: it inserts missing keywords as trial rows sourced
// 'mcp', so its unknown list is always empty. An existing keyword re-seeded is
// left alone (ON CONFLICT DO NOTHING) and reported with before == after.
//
// Every statement is scoped to kind = 'app'. A keyword's identity in this table
// is (keyword, kind) — the same phrase can live in the app and funnel pools
// independently — and the app sweep is the pool this tool steers.
func ControlKeywords(ctx context.Context, g *gorm.DB, action string, keywords []string, cooldownDays int) ([]models.KeywordControlResult, []string, error) {
	var changed []models.KeywordControlResult
	var unknown []string

	// One transaction for the whole batch: a mid-batch failure must not leave
	// half the keywords moved.
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		for _, kw := range keywords {
			// The reported "before" is read from the row, never assumed.
			var before string
			row := tx.Raw(
				`SELECT status FROM discovery.dynamic_keywords WHERE keyword = ? AND kind = 'app'`, kw,
			).Row()
			if scanErr := row.Scan(&before); scanErr != nil {
				if action != "seed" {
					unknown = append(unknown, kw)
					continue
				}
				before = ""
			}

			var after string
			switch action {
			case "seed":
				if before != "" {
					changed = append(changed, models.KeywordControlResult{Keyword: kw, Before: before, After: before})
					continue
				}
				if e := tx.Exec(
					`INSERT INTO discovery.dynamic_keywords (keyword, source, status, kind)
					 VALUES (?, 'mcp', 'trial', 'app')
					 ON CONFLICT (keyword, kind) DO NOTHING`, kw).Error; e != nil {
					return e
				}
				after = "trial"
			case "activate":
				if e := tx.Exec(
					`UPDATE discovery.dynamic_keywords
					    SET status = 'active', dry_streak = 0, next_eligible_at = NULL
					  WHERE keyword = ? AND kind = 'app'`, kw).Error; e != nil {
					return e
				}
				after = "active"
			case "rest":
				if e := tx.Exec(
					`UPDATE discovery.dynamic_keywords
					    SET status = 'resting',
					        next_eligible_at = now() + make_interval(days => ?),
					        rest_count = rest_count + 1
					  WHERE keyword = ? AND kind = 'app'`, cooldownDays, kw).Error; e != nil {
					return e
				}
				after = "resting"
			case "retire":
				if e := tx.Exec(
					`UPDATE discovery.dynamic_keywords SET status = 'retired'
					  WHERE keyword = ? AND kind = 'app'`, kw).Error; e != nil {
					return e
				}
				after = "retired"
			}
			changed = append(changed, models.KeywordControlResult{Keyword: kw, Before: before, After: after})
		}
		return nil
	})
	return changed, unknown, err
}
