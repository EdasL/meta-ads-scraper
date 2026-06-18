package repositories

import (
	"context"
	"encoding/json"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// UpsertRating inserts or updates a per-user rating for an advertiser.
// It snapshots the app's current scoring signals from the matview into the
// features JSONB column so the label captures the model state at rating time.
// If the advertiser is not in the matview (not yet visible), zero rows are
// inserted — this is acceptable; only rate visible apps.
func UpsertRating(ctx context.Context, g *gorm.DB, userEmail string, advertiserID int64, rating, comment string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(`
			INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, comment, features)
			SELECT ?, ?, ?, ?, to_jsonb(f) FROM (
			  SELECT m.gem_score, m.scaling_velocity, m.under_radar, m.funnel_intent,
			         m.demand, m.whitespace, m.active_ad_count, m.cohort, m.installs,
			         m.rating_count, adv.page_like_count,
			         (m.funnel_url IS NOT NULL) AS has_funnel
			  FROM frontend.emerging_scaling_apps m
			  LEFT JOIN public.advertisers adv ON adv.id = m.advertiser_id
			  WHERE m.advertiser_id = ?
			) f
			ON CONFLICT (user_email, advertiser_id) DO UPDATE SET
			  rating   = EXCLUDED.rating,
			  comment  = EXCLUDED.comment,
			  features = EXCLUDED.features,
			  updated_at = now()`,
			userEmail, advertiserID, rating, comment, advertiserID,
		).Error
	})
}

// DeleteRating removes a user's rating for an advertiser.
func DeleteRating(ctx context.Context, g *gorm.DB, userEmail string, advertiserID int64) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(
			`DELETE FROM frontend.app_ratings WHERE user_email = ? AND advertiser_id = ?`,
			userEmail, advertiserID,
		).Error
	})
}

// ratingRaw is an intermediate scan target for ListRatings; features is raw
// JSON bytes that we decode into a map for the caller.
type ratingRaw struct {
	AdvertiserID int64           `gorm:"column:advertiser_id"`
	Rating       string          `gorm:"column:rating"`
	Comment      string          `gorm:"column:comment"`
	Features     json.RawMessage `gorm:"column:features"`
}

// ListRatings returns all ratings for a user, keyed by advertiser ID.
func ListRatings(ctx context.Context, g *gorm.DB, userEmail string) (map[int64]models.RatingRow, error) {
	var raw []ratingRaw
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT advertiser_id, rating, comment, features
			 FROM frontend.app_ratings
			 WHERE user_email = ?
			 ORDER BY updated_at DESC, advertiser_id`,
			userEmail,
		).Scan(&raw).Error
	})
	if err != nil {
		return nil, err
	}
	out := make(map[int64]models.RatingRow, len(raw))
	for _, r := range raw {
		var features map[string]any
		if len(r.Features) > 0 {
			_ = json.Unmarshal(r.Features, &features)
		}
		out[r.AdvertiserID] = models.RatingRow{
			Rating:   r.Rating,
			Comment:  r.Comment,
			Features: features,
		}
	}
	return out, nil
}
