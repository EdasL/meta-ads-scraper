package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// ListSavedAds returns a user's saved ads, newest save first. ad_started_on is
// served as YYYY-MM-DD text so the wire shape matches the save body.
func ListSavedAds(ctx context.Context, g *gorm.DB, userEmail string) ([]models.SavedAdRow, error) {
	rows := make([]models.SavedAdRow, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT user_email, external_ad_id, advertiser_id, page_name, app_name,
			        title, body_text, media_type, media_s3_key, link_url, cta_text,
			        to_char(ad_started_on, 'YYYY-MM-DD') AS ad_started_on,
			        ad_active, saved_at
			 FROM frontend.saved_ads
			 WHERE user_email = ?
			 ORDER BY saved_at DESC, external_ad_id`,
			userEmail,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.SavedAdRow, 0)
	}
	return rows, err
}

// UpsertSavedAd inserts a saved ad with its render snapshot. A re-save is a
// no-op: the snapshot and saved_at keep their original values (there are no
// mutable annotations on an ad, unlike saved gems).
func UpsertSavedAd(ctx context.Context, g *gorm.DB, in models.SavedAdIn, externalAdID string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(
			`INSERT INTO frontend.saved_ads
			   (user_email, external_ad_id, advertiser_id, page_name, app_name,
			    title, body_text, media_type, media_s3_key, link_url, cta_text,
			    ad_started_on, ad_active, saved_at)
			 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, clock_timestamp())
			 ON CONFLICT (user_email, external_ad_id) DO NOTHING`,
			in.UserEmail, externalAdID, in.AdvertiserID, in.PageName, in.AppName,
			in.Title, in.BodyText, in.MediaType, in.MediaS3Key, in.LinkURL,
			in.CtaText, in.AdStartedOn, in.AdActive,
		).Error
	})
}

// DeleteSavedAd removes one saved ad for a user.
func DeleteSavedAd(ctx context.Context, g *gorm.DB, userEmail, externalAdID string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(
			`DELETE FROM frontend.saved_ads WHERE user_email = ? AND external_ad_id = ?`,
			userEmail, externalAdID,
		).Error
	})
}
