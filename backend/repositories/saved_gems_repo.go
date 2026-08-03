package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// ListSavedGems returns a user's watchlist, newest save first.
func ListSavedGems(ctx context.Context, g *gorm.DB, userEmail string) ([]models.SavedGemRow, error) {
	rows := make([]models.SavedGemRow, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT user_email, advertiser_id, page_name, app_name, icon_url, cohort,
			        gem_score, scaling_velocity, under_radar, funnel_intent,
			        active_ad_count, tags, note, stage, verdict, confidence,
			        group_id, saved_at, updated_at
			 FROM frontend.saved_gems
			 WHERE user_email = ?
			 ORDER BY saved_at DESC, advertiser_id`,
			userEmail,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.SavedGemRow, 0)
	}
	return rows, err
}

// UpsertSavedGem inserts a saved gem (freezing the score snapshot) or, on a
// re-save, updates ONLY the mutable annotations — the snapshot + saved_at stay
// at their original values, which is the whole point of the snapshot.
func UpsertSavedGem(ctx context.Context, g *gorm.DB, in models.SavedGemIn, advertiserID int) error {
	stage := in.Stage
	if stage == "" {
		stage = "just_saw"
	}
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		// Filing a gem into a column requires owning that column.
		if err := assertGroupOwned(tx, in.UserEmail, in.GroupID); err != nil {
			return err
		}
		return tx.Exec(
			`INSERT INTO frontend.saved_gems
			   (user_email, advertiser_id, page_name, app_name, icon_url, cohort,
			    gem_score, scaling_velocity, under_radar, funnel_intent,
			    active_ad_count, tags, note, stage, verdict, confidence, group_id,
			    saved_at, updated_at)
			 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
			         clock_timestamp(), clock_timestamp())
			 ON CONFLICT (user_email, advertiser_id) DO UPDATE SET
			   tags       = EXCLUDED.tags,
			   note       = EXCLUDED.note,
			   stage      = EXCLUDED.stage,
			   verdict    = EXCLUDED.verdict,
			   confidence = EXCLUDED.confidence,
			   -- A re-save that carries no group (e.g. the Discover save button)
			   -- must not knock the card out of its board column.
			   group_id   = COALESCE(EXCLUDED.group_id, frontend.saved_gems.group_id),
			   updated_at = clock_timestamp()`,
			in.UserEmail, advertiserID, in.PageName, in.AppName, in.IconURL, in.Cohort,
			in.GemScore, in.ScalingVelocity, in.UnderRadar, in.FunnelIntent,
			in.ActiveAdCount, in.Tags, in.Note, stage, in.Verdict, in.Confidence, in.GroupID,
		).Error
	})
}

// DeleteSavedGem removes a gem from a user's watchlist.
func DeleteSavedGem(ctx context.Context, g *gorm.DB, userEmail string, advertiserID int) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(
			`DELETE FROM frontend.saved_gems WHERE user_email = ? AND advertiser_id = ?`,
			userEmail, advertiserID,
		).Error
	})
}
