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
//
// Folder assignment has three cases: an omitted/null group_id keeps whatever the
// row already has, group_id 0 unfiles the gem (0 is never a real BIGSERIAL id),
// and any other id files it — if the caller owns that folder.
func UpsertSavedGem(ctx context.Context, g *gorm.DB, in models.SavedGemIn, advertiserID int) error {
	stage := in.Stage
	if stage == "" {
		stage = "just_saw"
	}
	groupID := in.GroupID
	clearGroup := groupID != nil && *groupID == 0
	if clearGroup {
		groupID = nil // no folder to own, and the insert path writes NULL
	}
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		// Filing a gem into a folder requires owning that folder.
		if err := assertGroupOwned(tx, in.UserEmail, groupID); err != nil {
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
			   -- must not knock the card out of its folder, so a NULL means
			   -- "keep". An explicit unfile therefore cannot ride the COALESCE:
			   -- the clear flag bypasses it and writes NULL outright.
			   group_id   = CASE WHEN ?::bool THEN NULL
			                     ELSE COALESCE(EXCLUDED.group_id, frontend.saved_gems.group_id)
			                END,
			   updated_at = clock_timestamp()`,
			in.UserEmail, advertiserID, in.PageName, in.AppName, in.IconURL, in.Cohort,
			in.GemScore, in.ScalingVelocity, in.UnderRadar, in.FunnelIntent,
			in.ActiveAdCount, in.Tags, in.Note, stage, in.Verdict, in.Confidence, groupID,
			clearGroup,
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
