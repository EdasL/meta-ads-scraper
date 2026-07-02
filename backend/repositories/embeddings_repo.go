package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/internal/reco"
	"gorm.io/gorm"
)

// RecommendedGems ranks the product feed for a user against the centroid of the
// content embeddings of the apps they liked. The bool return is whether the
// result is personalized: false when the user has liked nothing with a stored
// embedding, in which case the feed is returned in GemScore order (the caller
// tells the user it is not yet personalized). Embeddings are stored as REAL[]
// and selected as text because GORM cannot scan a Postgres array directly.
func RecommendedGems(ctx context.Context, g *gorm.DB, email string, limit int) ([]reco.RecoItem, bool, error) {
	var items []reco.RecoItem
	personalized := false
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		var liked []struct {
			Emb string `gorm:"column:emb"`
		}
		if err := tx.Raw(
			`SELECT e.embedding::text AS emb
			 FROM frontend.app_ratings r
			 JOIN frontend.app_embeddings e
			   ON e.advertiser_id = r.advertiser_id AND e.dim = 768
			 WHERE r.user_email = ? AND r.rating = 'like'`,
			email,
		).Scan(&liked).Error; err != nil {
			return err
		}
		vecs := make([][]float32, 0, len(liked))
		for _, l := range liked {
			v, err := reco.ParsePgFloatArray(l.Emb)
			if err != nil {
				return err
			}
			vecs = append(vecs, v)
		}
		centroid := reco.LikedCentroid(vecs)

		var feed []struct {
			AdvertiserID int64   `gorm:"column:advertiser_id"`
			AppName      string  `gorm:"column:app_name"`
			Cohort       string  `gorm:"column:cohort"`
			GemScore     float64 `gorm:"column:gem_score"`
			Emb          *string `gorm:"column:emb"`
		}
		if err := tx.Raw(
			`SELECT p.advertiser_id, COALESCE(p.app_name, p.page_name) AS app_name,
			        p.cohort, p.gem_score, e.embedding::text AS emb
			 FROM frontend.emerging_scaling_products p
			 LEFT JOIN frontend.app_embeddings e
			   ON e.advertiser_id = p.advertiser_id AND e.dim = 768
			 ORDER BY p.gem_score DESC`,
		).Scan(&feed).Error; err != nil {
			return err
		}

		items = make([]reco.RecoItem, 0, len(feed))
		for _, f := range feed {
			it := reco.RecoItem{
				AdvertiserID: f.AdvertiserID,
				AppName:      f.AppName,
				Cohort:       f.Cohort,
				GemScore:     f.GemScore,
			}
			if f.Emb != nil {
				v, err := reco.ParsePgFloatArray(*f.Emb)
				if err != nil {
					return err
				}
				it.SetVec(v)
			}
			items = append(items, it)
		}

		if centroid != nil {
			personalized = true
			items = reco.RankByCosine(centroid, items)
		}
		return nil
	})
	if err != nil {
		return nil, false, err
	}
	if limit > 0 && len(items) > limit {
		items = items[:limit]
	}
	return items, personalized, nil
}
