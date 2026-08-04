package repositories

import (
	"context"
	"math"
	"math/rand"
	"os"
	"strconv"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/internal/reco"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// Crowding demotion knobs, read once at package load (mirrors the env-read
// pattern in config/config.go and monitoring/sentry.go). An app whose
// embedding-space crowding is >= crowdingTail sits in a clone cluster; its
// exploit-ordering score is multiplied by (1 - crowdingPenalty) so the review
// deck ranks it below equally-liked but distinctive apps. The displayed p_like
// is never touched. NULL crowding or crowdingPenalty=0 leaves ordering unchanged,
// so the penalty can never starve or empty the deck.
var (
	crowdingTail    = envFloat("AGF_CROWDING_TAIL", 0.80)
	crowdingPenalty = envFloat("AGF_CROWDING_PENALTY_GO", 0.5)
)

func envFloat(key string, def float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

// crowdingRankScore demotes an app's exploit-ordering score when it sits in a
// dense embedding cluster (crowding >= tail). It only affects the value the
// taste bucket sorts/samples by — never the displayed p_like. NULL crowding, an
// app below the tail, or penalty 0 all return pLike unchanged.
func crowdingRankScore(pLike float64, crowding *float64, tail, penalty float64) float64 {
	if crowding != nil && *crowding >= tail {
		return pLike * (1 - penalty)
	}
	return pLike
}

// PreferenceQueue returns the user's review queue, scored at serve time so the
// ranking adapts to ratings immediately (no offline preference_scores job in
// the path). Per request it fits the content-preference model from the user's
// like/dislike ratings × cached app embeddings, scores every unrated matview
// candidate, then blends exploit/uncertain/fresh buckets and caps per niche via
// assembleQueue. exclude lists advertiser_ids the client already holds (its
// card buffer / just-rated ids) so background refills never serve duplicates.
// Hero creative + growth are attached only to the final selection, inside the
// same read transaction.
func PreferenceQueue(ctx context.Context, g *gorm.DB, userEmail string, limit int, exclude []int64) ([]models.PreferenceCardRow, error) {
	var outRows []models.PreferenceCardRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		embs, crowd, err := prefEmbeddings(tx)
		if err != nil {
			return err
		}

		// The user's labels, with app names for the "similar to <name>" why.
		// Rated apps may have left the matview, hence the LEFT JOIN + COALESCE
		// (mirrors the Python loader's meta.get(a, ("", 0)) fallback).
		var rated []struct {
			AdvertiserID int64  `gorm:"column:advertiser_id"`
			Rating       string `gorm:"column:rating"`
			AppName      string `gorm:"column:app_name"`
		}
		if err := tx.Raw(
			`SELECT r.advertiser_id, r.rating,
			        COALESCE(m.app_name, m.page_name, '') AS app_name
			 FROM frontend.app_ratings r
			 LEFT JOIN frontend.emerging_scaling_apps m
			   ON m.advertiser_id = r.advertiser_id
			 WHERE r.user_email = ?`, userEmail,
		).Scan(&rated).Error; err != nil {
			return err
		}
		var liked, disliked []reco.LabeledVec
		for _, r := range rated {
			vec, ok := embs[r.AdvertiserID]
			if !ok {
				continue
			}
			lv := reco.LabeledVec{ID: r.AdvertiserID, Name: r.AppName, Vec: vec}
			switch r.Rating {
			case "like":
				liked = append(liked, lv)
			case "dislike":
				disliked = append(disliked, lv)
			}
		}
		model := reco.FitContentModel(liked, disliked, nil, reco.DefaultFitOpts())

		// Candidates: every unrated matview app, minus the client's exclude list.
		// The matview scan is wrapped in a subselect so the unqualified mvCols stay
		// unambiguous once app_topics joins on; topic_id is the diversity-cap key
		// (NULL for apps with no embedding cluster yet → keyword fallback).
		q := `SELECT s.*, t.topic_id
		      FROM (SELECT ` + mvCols + `
		            FROM frontend.emerging_scaling_apps
		            WHERE NOT EXISTS (
		              SELECT 1 FROM frontend.app_ratings r
		              WHERE r.user_email = ?
		                AND r.advertiser_id = frontend.emerging_scaling_apps.advertiser_id)`
		args := []interface{}{userEmail}
		if len(exclude) > 0 {
			q += ` AND frontend.emerging_scaling_apps.advertiser_id NOT IN (?)`
			args = append(args, exclude)
		}
		q += `) s LEFT JOIN frontend.app_topics t ON t.advertiser_id = s.advertiser_id`
		var scanned []mvScanRow
		if err := tx.Raw(q, args...).Scan(&scanned).Error; err != nil {
			return err
		}

		shortPool := toShortlistRows(scanned)
		cands := make([]prefCandidate, len(shortPool))
		for i, row := range shortPool {
			vec, hasVec := embs[int64(row.AdvertiserID)]
			var p float64
			var why, source string
			switch {
			case !hasVec:
				p, why, source = 0.5, "new", "fresh"
			case model.Coldstart():
				// Serving substitute for an unfitted model: rank by GemScore
				// (mirrors scripts/score_content_preferences.py's coldstart branch).
				p, why, source = row.GemScore/100.0, "", "scored"
			default:
				p, why, source = model.PLike(model.Score(vec)), model.Why(vec), "scored"
			}
			unc := 1.0 - math.Abs(2.0*p-1.0)
			// Demote clone-cluster apps in the taste ordering only; the p_like
			// shown to the client stays the honest calibrated probability.
			rank := crowdingRankScore(p, crowd[int64(row.AdvertiserID)], crowdingTail, crowdingPenalty)
			cands[i] = prefCandidate{
				PreferenceCardRow: models.PreferenceCardRow{
					ShortlistRow: row,
					PLike:        p,
					Why:          why,
				},
				RankScore:   rank,
				Uncertainty: unc,
				Source:      source,
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
