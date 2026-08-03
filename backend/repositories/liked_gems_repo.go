package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// ListLikedGems returns every app the user has rated 'like' — from the Review
// thumbs-up, the Hunt heart, anywhere — newest like first. This is the built-in
// Liked collection, so it deliberately includes apps the user never saved.
//
// The card fields come from the same sources the hunt and shortlist rows read,
// so a liked app shows the numbers the user already saw elsewhere: page_name
// from public.advertisers, app_name/icon_url from the emerging_scaling_apps
// matview, gem_score from adtech.advertiser_scores. Every join is a LEFT join —
// a like must survive its app dropping out of the matview (vetoed, no longer
// emerging), which is exactly when those columns go null.
//
// gem_score falls back to the features snapshot frozen into the rating row when
// the live score is missing, so an old like still renders a number.
func ListLikedGems(ctx context.Context, g *gorm.DB, userEmail string) ([]models.LikedGemRow, error) {
	rows := make([]models.LikedGemRow, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT ar.advertiser_id,
			        adv.page_name,
			        esa.app_name,
			        esa.icon_url,
			        COALESCE(s.gem_score, (ar.features->>'gem_score')::float8) AS gem_score,
			        ar.rated_at,
			        (sg.advertiser_id IS NOT NULL) AS saved
			 FROM frontend.app_ratings ar
			 LEFT JOIN public.advertisers adv ON adv.id = ar.advertiser_id
			 LEFT JOIN adtech.advertiser_scores s ON s.advertiser_id = ar.advertiser_id
			 LEFT JOIN frontend.emerging_scaling_apps esa ON esa.advertiser_id = ar.advertiser_id
			 -- Matching the saved row on ar.user_email (not a second bind) keeps the
			 -- saved flag in lockstep with what /saved lists for this same identity.
			 LEFT JOIN frontend.saved_gems sg
			        ON sg.advertiser_id = ar.advertiser_id AND sg.user_email = ar.user_email
			 WHERE ar.user_email = ? AND ar.rating = 'like'
			 ORDER BY ar.rated_at DESC, ar.advertiser_id`,
			userEmail,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.LikedGemRow, 0)
	}
	return rows, err
}
