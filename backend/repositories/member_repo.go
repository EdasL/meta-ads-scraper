package repositories

import (
	"context"
	"time"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// MyRating is one of a member's own app ratings, enriched with the app name and
// cohort from the live feed. A rated app may have since left the feed, so the
// matview join is LEFT and the two enrichment fields fall back to "".
type MyRating struct {
	AdvertiserID int64     `json:"advertiser_id"`
	AppName      string    `json:"app_name"`
	Cohort       string    `json:"cohort"`
	Rating       string    `json:"rating"`
	Comment      string    `json:"comment"`
	RatedAt      time.Time `json:"rated_at"`
}

// MySavedGem is one row of a member's saved shortlist.
type MySavedGem struct {
	AdvertiserID int64         `json:"advertiser_id"`
	PageName     string        `json:"page_name"`
	AppName      string        `json:"app_name"`
	Cohort       string        `json:"cohort"`
	GemScore     float64       `json:"gem_score"`
	Stage        string        `json:"stage"`
	Verdict      *string       `json:"verdict"`
	Tags         string        `json:"tags"`
	Note         string        `json:"note"`
	SavedAt      time.Time     `json:"saved_at"`
	GroupIDs     models.IDList `json:"group_ids" gorm:"column:group_ids"`
}

// TasteProfile summarizes what a member has liked: rating tallies, the cohorts
// their likes cluster in, and the numeric scoring signals that separate their
// likes from their dislikes (averaged over the features snapshot each rating
// carried at the time).
type TasteProfile struct {
	Counts          map[string]int64 `json:"counts"`
	TopLikedCohorts []CohortCount    `json:"top_liked_cohorts"`
	LikeSignals     []SignalAvg      `json:"like_signals"`
}

// CohortCount is one cohort and how many of the member's likes fall in it.
type CohortCount struct {
	Cohort string `json:"cohort"`
	Likes  int64  `json:"likes"`
}

// SignalAvg is one numeric scoring signal's mean value across the member's
// liked vs disliked apps. DislikeAvg is a pointer's worth of NULL when the
// member has no disliked app carrying that signal — served as 0.
type SignalAvg struct {
	Signal     string  `json:"signal"`
	LikeAvg    float64 `json:"like_avg"`
	DislikeAvg float64 `json:"dislike_avg"`
}

// MyRatings returns one page of a member's ratings (newest first) plus the
// total row count for the filter before LIMIT. rating == "" means all ratings.
func MyRatings(ctx context.Context, g *gorm.DB, email, rating string, limit, offset int) ([]MyRating, int64, error) {
	rows := make([]MyRating, 0)
	var total int64
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		if err := tx.Raw(
			`SELECT count(*) FROM frontend.app_ratings
			 WHERE user_email = ? AND (? = '' OR rating = ?)`,
			email, rating, rating,
		).Scan(&total).Error; err != nil {
			return err
		}
		return tx.Raw(
			`SELECT r.advertiser_id,
			        COALESCE(mv.app_name, '') AS app_name,
			        COALESCE(mv.cohort, '')   AS cohort,
			        r.rating, r.comment, r.rated_at
			 FROM frontend.app_ratings r
			 LEFT JOIN frontend.emerging_scaling_apps mv ON mv.advertiser_id = r.advertiser_id
			 WHERE r.user_email = ? AND (? = '' OR r.rating = ?)
			 ORDER BY r.rated_at DESC
			 LIMIT ? OFFSET ?`,
			email, rating, rating, limit, offset,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]MyRating, 0)
	}
	return rows, total, err
}

// MySavedGems returns a member's saved shortlist, newest first.
func MySavedGems(ctx context.Context, g *gorm.DB, email string) ([]MySavedGem, error) {
	rows := make([]MySavedGem, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT advertiser_id,
			        COALESCE(page_name, '') AS page_name,
			        COALESCE(app_name, '')  AS app_name,
			        cohort, gem_score, stage, verdict, tags, note, saved_at,
			        COALESCE((SELECT jsonb_agg(m.group_id ORDER BY m.group_id)
			          FROM frontend.saved_group_members m
			          WHERE m.user_email=sg.user_email AND m.advertiser_id=sg.advertiser_id), '[]'::jsonb) AS group_ids
			 FROM frontend.saved_gems sg
			 WHERE user_email = ?
			 ORDER BY saved_at DESC`,
			email,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]MySavedGem, 0)
	}
	return rows, err
}

// MyTasteProfile derives a member's preference summary from their ratings: the
// like/neutral/dislike tallies, the cohorts their likes cluster in, and the
// numeric signal averages that separate likes from dislikes.
func MyTasteProfile(ctx context.Context, g *gorm.DB, email string) (*TasteProfile, error) {
	tp := &TasteProfile{
		Counts:          map[string]int64{},
		TopLikedCohorts: make([]CohortCount, 0),
		LikeSignals:     make([]SignalAvg, 0),
	}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		var counts []struct {
			Rating string
			N      int64
		}
		if err := tx.Raw(
			`SELECT rating, count(*) AS n FROM frontend.app_ratings
			 WHERE user_email = ? GROUP BY 1`, email,
		).Scan(&counts).Error; err != nil {
			return err
		}
		for _, c := range counts {
			tp.Counts[c.Rating] = c.N
		}

		if err := tx.Raw(
			`SELECT COALESCE(mv.cohort, 'unknown') AS cohort, count(*) AS likes
			 FROM frontend.app_ratings r
			 LEFT JOIN frontend.emerging_scaling_apps mv ON mv.advertiser_id = r.advertiser_id
			 WHERE r.user_email = ? AND r.rating = 'like'
			 GROUP BY 1 ORDER BY 2 DESC LIMIT 8`, email,
		).Scan(&tp.TopLikedCohorts).Error; err != nil {
			return err
		}

		// avg(...) FILTER can be NULL (no disliked app carries the signal); scan
		// into a nullable pointer and serve 0.
		var signals []struct {
			Signal     string
			LikeAvg    float64
			DislikeAvg *float64
		}
		if err := tx.Raw(
			`SELECT f.key AS signal,
			        avg(f.value::float) FILTER (WHERE r.rating = 'like')    AS like_avg,
			        avg(f.value::float) FILTER (WHERE r.rating = 'dislike') AS dislike_avg
			 FROM frontend.app_ratings r, jsonb_each_text(r.features) f
			 WHERE r.user_email = ? AND f.value ~ '^-?[0-9.]+$'
			 GROUP BY 1
			 HAVING count(*) FILTER (WHERE r.rating = 'like') > 0
			 ORDER BY 2 DESC NULLS LAST LIMIT 12`, email,
		).Scan(&signals).Error; err != nil {
			return err
		}
		for _, s := range signals {
			d := 0.0
			if s.DislikeAvg != nil {
				d = *s.DislikeAvg
			}
			tp.LikeSignals = append(tp.LikeSignals, SignalAvg{
				Signal: s.Signal, LikeAvg: s.LikeAvg, DislikeAvg: d,
			})
		}
		return nil
	})
	return tp, err
}
