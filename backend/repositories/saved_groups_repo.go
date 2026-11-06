package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// ListSavedGroups returns a user's folders in display order.
//
// Folders are optional organization, so nothing is seeded: a user who has never
// made one gets an empty list, and the grid shows every saved gem unfiled.
// (Users migrated from the stage board keep their backfilled folders and may
// delete them all.)
func ListSavedGroups(ctx context.Context, g *gorm.DB, userEmail string) ([]models.SavedGroupRow, error) {
	rows := make([]models.SavedGroupRow, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT id, name, "position", product
			 FROM frontend.saved_groups
			 WHERE user_email = ?
			 ORDER BY "position", id`,
			userEmail,
		).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.SavedGroupRow, 0)
	}
	return rows, err
}

// CreateSavedGroup appends a column at the right edge of the user's board.
func CreateSavedGroup(ctx context.Context, g *gorm.DB, userEmail, name string) (models.SavedGroupRow, error) {
	var row models.SavedGroupRow
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`INSERT INTO frontend.saved_groups (user_email, name, position)
			 SELECT ?, ?, COALESCE(MAX("position") + 1, 0)
			 FROM frontend.saved_groups WHERE user_email = ?
			 RETURNING id, name, "position", product`,
			userEmail, name, userEmail,
		).Scan(&row).Error
	})
	return row, err
}

// UpdateSavedGroup renames and/or repositions a group. Nil fields are left
// alone. Reports false when the id is unknown or owned by somebody else.
func UpdateSavedGroup(ctx context.Context, g *gorm.DB, userEmail string, id int64, name *string, position *int) (bool, error) {
	return UpdateSavedGroupProduct(ctx, g, userEmail, id, name, position, nil)
}

// An empty product clears the association; an omitted product preserves it.
func UpdateSavedGroupProduct(ctx context.Context, g *gorm.DB, userEmail string, id int64, name *string, position *int, product *string) (bool, error) {
	if name == nil && position == nil && product == nil {
		// Nothing to change — still answer the ownership question truthfully.
		return savedGroupOwned(ctx, g, userEmail, id)
	}
	var affected int64
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		res := tx.Exec(
			`UPDATE frontend.saved_groups
			 SET name       = COALESCE(?, name),
			     "position" = COALESCE(?, "position"),
			     product = COALESCE(?, product)
			 WHERE id = ? AND user_email = ?`,
			name, position, product, id, userEmail,
		)
		affected = res.RowsAffected
		return res.Error
	})
	return affected > 0, err
}

// DeleteSavedGroup removes a folder. Its membership rows go with it (the FK
// cascades) and the saved gems themselves are untouched — deleting a playlist
// does not delete the songs. Deleting down to zero folders is allowed. Reports
// deleted=false when the id is unknown or owned by somebody else.
func DeleteSavedGroup(ctx context.Context, g *gorm.DB, userEmail string, id int64) (bool, error) {
	var deleted bool
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		res := tx.Exec(
			`DELETE FROM frontend.saved_groups WHERE id = ? AND user_email = ?`,
			id, userEmail,
		)
		if res.Error != nil {
			return res.Error
		}
		deleted = res.RowsAffected > 0
		return nil
	})
	return deleted, err
}

// AddSavedGroupMember files a gem into a folder. Idempotent: adding a gem that
// is already there succeeds and changes nothing. Reports found=false when the
// folder is unknown or owned by somebody else, which controllers answer with a
// 404 — the same "never confirm another user's ids exist" rule as the rest of
// this repo.
func AddSavedGroupMember(ctx context.Context, g *gorm.DB, userEmail string, groupID int64, advertiserID int64) (bool, error) {
	var found bool
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		var owned int64
		if err := tx.Raw(
			`SELECT count(*) FROM frontend.saved_groups WHERE id = ? AND user_email = ?`,
			groupID, userEmail,
		).Scan(&owned).Error; err != nil {
			return err
		}
		if owned == 0 {
			return nil
		}
		found = true
		// The ownership check and the insert share one tx, so a concurrent
		// folder delete cannot slip between them.
		return tx.Exec(
			`INSERT INTO frontend.saved_group_members (user_email, group_id, advertiser_id)
			 VALUES (?, ?, ?)
			 ON CONFLICT (group_id, advertiser_id) DO NOTHING`,
			userEmail, groupID, advertiserID,
		).Error
	})
	return found, err
}

// RemoveSavedGroupMember takes a gem out of a folder, leaving the gem saved.
// Idempotent: removing something that is not in the folder still succeeds.
// found=false means the folder is unknown or foreign (404), never "the gem was
// not a member".
func RemoveSavedGroupMember(ctx context.Context, g *gorm.DB, userEmail string, groupID int64, advertiserID int64) (bool, error) {
	var found bool
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		var owned int64
		if err := tx.Raw(
			`SELECT count(*) FROM frontend.saved_groups WHERE id = ? AND user_email = ?`,
			groupID, userEmail,
		).Scan(&owned).Error; err != nil {
			return err
		}
		if owned == 0 {
			return nil
		}
		found = true
		return tx.Exec(
			`DELETE FROM frontend.saved_group_members
			 WHERE group_id = ? AND advertiser_id = ? AND user_email = ?`,
			groupID, advertiserID, userEmail,
		).Error
	})
	return found, err
}

// savedGroupOwned answers whether the group exists under this user.
func savedGroupOwned(ctx context.Context, g *gorm.DB, userEmail string, id int64) (bool, error) {
	var n int64
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT count(*) FROM frontend.saved_groups WHERE id = ? AND user_email = ?`,
			id, userEmail,
		).Scan(&n).Error
	})
	return n > 0, err
}
