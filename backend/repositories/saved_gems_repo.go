package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// ListSavedGems returns a user's watchlist, newest save first, each row carrying
// the folders it belongs to.
//
// Folder membership is aggregated as a JSON array (json_agg → text → IDList) so
// no Postgres array plumbing is needed, and COALESCE turns "in no folders" into
// [] rather than null. Member ids are ordered by group_id: any stable order
// would do, and id order is the one the client can rely on without a second
// lookup into the folder list.
func ListSavedGems(ctx context.Context, g *gorm.DB, userEmail string) ([]models.SavedGemRow, error) {
	rows := make([]models.SavedGemRow, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT sg.user_email, sg.advertiser_id, sg.page_name, sg.app_name,
			        sg.icon_url, sg.cohort, sg.gem_score, sg.scaling_velocity,
			        sg.under_radar, sg.funnel_intent, sg.active_ad_count, sg.tags,
			        sg.note, sg.stage, sg.verdict, sg.confidence,
			        COALESCE((
			            SELECT json_agg(m.group_id ORDER BY m.group_id)
			            FROM frontend.saved_group_members m
			            WHERE m.user_email = sg.user_email
			              AND m.advertiser_id = sg.advertiser_id
			        ), '[]'::json)::text AS group_ids,
			        sg.saved_at, sg.updated_at
			 FROM frontend.saved_gems sg
			 WHERE sg.user_email = ?
			 ORDER BY sg.saved_at DESC, sg.advertiser_id`,
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
// Folder membership is NOT part of this write: it lives in
// frontend.saved_group_members and moves through the membership endpoints, so a
// re-save from anywhere in the product can never disturb which folders a gem is
// in. The legacy saved_gems.group_id column is left untouched.
func UpsertSavedGem(ctx context.Context, g *gorm.DB, in models.SavedGemIn, advertiserID int) error {
	stage := in.Stage
	if stage == "" {
		stage = "just_saw"
	}
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(
			`INSERT INTO frontend.saved_gems
			   (user_email, advertiser_id, page_name, app_name, icon_url, cohort,
			    gem_score, scaling_velocity, under_radar, funnel_intent,
			    active_ad_count, tags, note, stage, verdict, confidence,
			    saved_at, updated_at)
			 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
			         clock_timestamp(), clock_timestamp())
			 ON CONFLICT (user_email, advertiser_id) DO UPDATE SET
			   tags       = EXCLUDED.tags,
			   note       = EXCLUDED.note,
			   stage      = EXCLUDED.stage,
			   verdict    = EXCLUDED.verdict,
			   confidence = EXCLUDED.confidence,
			   updated_at = clock_timestamp()`,
			in.UserEmail, advertiserID, in.PageName, in.AppName, in.IconURL, in.Cohort,
			in.GemScore, in.ScalingVelocity, in.UnderRadar, in.FunnelIntent,
			in.ActiveAdCount, in.Tags, in.Note, stage, in.Verdict, in.Confidence,
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
