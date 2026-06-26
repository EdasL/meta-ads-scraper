package repositories

import (
	"context"
	"math/rand"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// candScanRow is the scan target for a queue candidate: matview columns plus the
// per-user preference inputs. GORM's Scan handles one level of embedding, so we
// embed models.ShortlistRow and repeat the flat growth fields (mirrors mvScanRow).
type candScanRow struct {
	models.ShortlistRow
	FirstCount  *int     `gorm:"column:first_count"`
	LatestCount *int     `gorm:"column:latest_count"`
	Weeks       *int     `gorm:"column:weeks_observed"`
	PctChange   *float64 `gorm:"column:pct_change"`
	PLike       float64  `gorm:"column:p_like"`
	Uncertainty float64  `gorm:"column:uncertainty"`
	Why         string   `gorm:"column:why"`
	Source      string   `gorm:"column:source"`
}

func (r candScanRow) toMvScanRow() mvScanRow {
	return mvScanRow{
		ShortlistRow: r.ShortlistRow,
		FirstCount:   r.FirstCount,
		LatestCount:  r.LatestCount,
		Weeks:        r.Weeks,
		PctChange:    r.PctChange,
	}
}

// PreferenceQueue returns the user's review queue. It builds a wide candidate
// pool — all unrated scored apps ∪ the top fresh-unscored matview apps — then
// blends exploit/uncertain/fresh buckets and caps per niche via assembleQueue so
// each fetch is diverse and non-repeating. Hero creative + growth are attached
// only to the final selection, inside the same read transaction.
func PreferenceQueue(ctx context.Context, g *gorm.DB, userEmail string, limit int) ([]models.PreferenceCardRow, error) {
	var outRows []models.PreferenceCardRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		freshCap := limit * poolMultiplier
		scanned := make([]candScanRow, 0)
		if err := tx.Raw(
			`WITH scored AS (
			   SELECT ps.advertiser_id AS adv_id, ps.p_like, ps.uncertainty,
			          ps.why, 'scored'::text AS source
			   FROM frontend.preference_scores ps
			   WHERE ps.user_email = ?
			     AND NOT EXISTS (
			       SELECT 1 FROM frontend.app_ratings r
			       WHERE r.user_email = ps.user_email
			         AND r.advertiser_id = ps.advertiser_id)
			 ),
			 fresh AS (
			   SELECT m.advertiser_id AS adv_id, 0.5::double precision AS p_like,
			          1.0::double precision AS uncertainty, 'new'::text AS why,
			          'fresh'::text AS source
			   FROM frontend.emerging_scaling_apps m
			   WHERE NOT EXISTS (
			       SELECT 1 FROM frontend.preference_scores ps
			       WHERE ps.user_email = ? AND ps.advertiser_id = m.advertiser_id)
			     AND NOT EXISTS (
			       SELECT 1 FROM frontend.app_ratings r
			       WHERE r.user_email = ? AND r.advertiser_id = m.advertiser_id)
			   ORDER BY m.gem_score DESC NULLS LAST
			   LIMIT ?
			 ),
			 cand AS (
			   SELECT * FROM scored
			   UNION ALL
			   SELECT * FROM fresh
			 )
			 SELECT `+mvCols+`, cand.p_like, cand.uncertainty, cand.why, cand.source
			 FROM frontend.emerging_scaling_apps
			 JOIN cand ON cand.adv_id = frontend.emerging_scaling_apps.advertiser_id`,
			userEmail, userEmail, userEmail, freshCap,
		).Scan(&scanned).Error; err != nil {
			return err
		}

		cands := make([]prefCandidate, len(scanned))
		for i, r := range scanned {
			short := toShortlistRows([]mvScanRow{r.toMvScanRow()})[0]
			cands[i] = prefCandidate{
				PreferenceCardRow: models.PreferenceCardRow{
					ShortlistRow: short,
					PLike:        r.PLike,
					Why:          r.Why,
				},
				Uncertainty: r.Uncertainty,
				Source:      r.Source,
			}
		}

		selected := assembleQueue(cands, queueOpts{
			Limit:         limit,
			PerNicheCap:   defaultPerNicheCap,
			ExploitFrac:   exploitFrac,
			UncertainFrac: uncertainFrac,
			Rng:           rand.New(rand.NewSource(time.Now().UnixNano())),
		})

		shortRows := make([]models.ShortlistRow, len(selected))
		for i := range selected {
			shortRows[i] = selected[i].ShortlistRow
		}
		if err := attachEnrichment(tx, shortRows); err != nil {
			return err
		}
		outRows = make([]models.PreferenceCardRow, len(selected))
		for i := range selected {
			outRows[i] = models.PreferenceCardRow{
				ShortlistRow: shortRows[i],
				PLike:        selected[i].PLike,
				Why:          selected[i].Why,
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return outRows, nil
}
