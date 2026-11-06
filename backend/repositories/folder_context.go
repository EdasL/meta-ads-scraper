package repositories

import (
	"context"
	"errors"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

var ErrFolderNotFound = errors.New("folder not found")

func CreateHuntInFolder(ctx context.Context, g *gorm.DB, email, title string, folderID *int64) (int, error) {
	if folderID == nil {
		return CreateHunt(ctx, g, email, title)
	}
	var id int
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		if err := tx.Raw(`INSERT INTO frontend.hunts (user_email, title, folder_id)
			SELECT ?, ?, id FROM frontend.saved_groups WHERE id = ? AND user_email = ?
			RETURNING id`, email, title, *folderID, email).Scan(&id).Error; err != nil {
			return err
		}
		if id == 0 {
			return ErrFolderNotFound
		}
		return nil
	})
	return id, err
}

// SavedFolderContext returns only the caller's selected folder and a bounded
// sample of its saved references. Names are context, not proof of an ad offer.
func SavedFolderContext(ctx context.Context, g *gorm.DB, email string, folderID int64) (*models.HuntFolderContext, error) {
	var rows []models.HuntFolderContext
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		if err := tx.Raw(`SELECT id AS folder_id, name, product FROM frontend.saved_groups
			WHERE id = ? AND user_email = ?`, folderID, email).Scan(&rows).Error; err != nil {
			return err
		}
		if len(rows) == 0 {
			return nil
		}
		rows[0].Brands = []models.FolderBrand{}
		return tx.Raw(`SELECT advertiser_id, name, note, description, ad_copy
			FROM frontend.folder_reference_brands WHERE folder_id = ? AND user_email = ?
			ORDER BY added_at DESC, advertiser_id LIMIT 12`, folderID, email).Scan(&rows[0].Brands).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

// The caller has already checked Hunt ownership. A deleted folder supplies no
// context; its ON DELETE SET NULL link leaves the hunt usable.
func HuntFolderContext(ctx context.Context, g *gorm.DB, huntID int) (*models.HuntFolderContext, error) {
	var rows []struct {
		UserEmail string
		FolderID  int64
	}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT user_email, folder_id FROM frontend.hunts
			WHERE id = ? AND folder_id IS NOT NULL`, huntID).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return SavedFolderContext(ctx, g, rows[0].UserEmail, rows[0].FolderID)
}
