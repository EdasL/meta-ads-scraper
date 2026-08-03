package repositories

import (
	"context"
	"errors"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// ErrGroupNotOwned is returned when a write references a saved group that does
// not exist or belongs to another user. Controllers map it to a 400.
var ErrGroupNotOwned = errors.New("saved group not found for this user")

// defaultGroupNames are the columns a new board starts with. They mirror the
// legacy stage pipeline and MUST stay identical to the backfill in
// db/schema/160_saved_groups.sql, so a migrated user and a freshly-seeded user
// see the same board.
var defaultGroupNames = []string{"Just saw", "Watching", "Looks real", "Building"}

// ListSavedGroups returns a user's board columns ordered left to right, seeding
// the four defaults the first time the user is seen. Seeding is a single
// conditional INSERT inside the write tx, so two concurrent first-loads cannot
// double-seed.
func ListSavedGroups(ctx context.Context, g *gorm.DB, userEmail string) ([]models.SavedGroupRow, error) {
	rows := make([]models.SavedGroupRow, 0, len(defaultGroupNames))
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		if err := tx.Exec(
			`INSERT INTO frontend.saved_groups (user_email, name, position)
			 SELECT ?, d.name, d.position
			 FROM (VALUES (?, 0), (?, 1), (?, 2), (?, 3)) AS d(name, position)
			 WHERE NOT EXISTS (
			     SELECT 1 FROM frontend.saved_groups WHERE user_email = ?
			 )`,
			userEmail,
			defaultGroupNames[0], defaultGroupNames[1],
			defaultGroupNames[2], defaultGroupNames[3],
			userEmail,
		).Error; err != nil {
			return err
		}
		return tx.Raw(
			`SELECT id, name, "position"
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
			 RETURNING id, name, "position"`,
			userEmail, name, userEmail,
		).Scan(&row).Error
	})
	return row, err
}

// UpdateSavedGroup renames and/or repositions a group. Nil fields are left
// alone. Reports false when the id is unknown or owned by somebody else.
func UpdateSavedGroup(ctx context.Context, g *gorm.DB, userEmail string, id int64, name *string, position *int) (bool, error) {
	if name == nil && position == nil {
		// Nothing to change — still answer the ownership question truthfully.
		return savedGroupOwned(ctx, g, userEmail, id)
	}
	var affected int64
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		res := tx.Exec(
			`UPDATE frontend.saved_groups
			 SET name       = COALESCE(?, name),
			     "position" = COALESCE(?, "position")
			 WHERE id = ? AND user_email = ?`,
			name, position, id, userEmail,
		)
		affected = res.RowsAffected
		return res.Error
	})
	return affected > 0, err
}

// DeleteSavedGroup removes a column, moving its gems to the user's first
// remaining column so nothing falls off the board. A user's last group is kept:
// that call reports deleted=false, lastGroup=true.
func DeleteSavedGroup(ctx context.Context, g *gorm.DB, userEmail string, id int64) (bool, bool, error) {
	var deleted, lastGroup bool
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		var owned int64
		if err := tx.Raw(
			`SELECT count(*) FROM frontend.saved_groups WHERE id = ? AND user_email = ?`,
			id, userEmail,
		).Scan(&owned).Error; err != nil {
			return err
		}
		if owned == 0 {
			return nil // unknown id or foreign owner: both false
		}

		var total int64
		if err := tx.Raw(
			`SELECT count(*) FROM frontend.saved_groups WHERE user_email = ?`,
			userEmail,
		).Scan(&total).Error; err != nil {
			return err
		}
		if total <= 1 {
			lastGroup = true
			return nil
		}

		var fallback int64
		if err := tx.Raw(
			`SELECT id FROM frontend.saved_groups
			 WHERE user_email = ? AND id <> ?
			 ORDER BY "position", id
			 LIMIT 1`,
			userEmail, id,
		).Scan(&fallback).Error; err != nil {
			return err
		}
		if err := tx.Exec(
			`UPDATE frontend.saved_gems SET group_id = ?, updated_at = clock_timestamp()
			 WHERE user_email = ? AND group_id = ?`,
			fallback, userEmail, id,
		).Error; err != nil {
			return err
		}
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
	return deleted, lastGroup, err
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

// assertGroupOwned guards a saved-gem write against filing a gem into another
// user's column. Runs inside the caller's tx so the check and the write cannot
// straddle a concurrent group delete.
func assertGroupOwned(tx *gorm.DB, userEmail string, groupID *int64) error {
	if groupID == nil {
		return nil
	}
	var n int64
	if err := tx.Raw(
		`SELECT count(*) FROM frontend.saved_groups WHERE id = ? AND user_email = ?`,
		*groupID, userEmail,
	).Scan(&n).Error; err != nil {
		return err
	}
	if n == 0 {
		return ErrGroupNotOwned
	}
	return nil
}
