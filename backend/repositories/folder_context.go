package repositories

import (
	"context"
	"errors"
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
	"strings"
)

var ErrFolderNotFound = errors.New("folder not found")

func CreateHuntInFolder(ctx context.Context, g *gorm.DB, email, title string, folderID *int64) (int, error) {
	return CreateHuntWithProduct(ctx, g, email, title, folderID, nil)
}

// Snapshot the chosen product on the chat. Reattaching a folder later must not
// silently switch the product under an existing conversation.
func CreateHuntWithProduct(ctx context.Context, g *gorm.DB, email, title string, folderID, productID *int64) (int, error) {
	var id int
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		if folderID != nil {
			var groups []models.SavedGroupRow
			if e := tx.Raw(`SELECT id,product_id FROM frontend.saved_groups WHERE id=? AND user_email=? FOR SHARE`, *folderID, email).Scan(&groups).Error; e != nil {
				return e
			}
			if len(groups) == 0 {
				return ErrFolderNotFound
			}
			if productID == nil {
				productID = groups[0].ProductID
			} else if groups[0].ProductID != nil && *groups[0].ProductID != *productID {
				return ErrProductFolderMismatch
			}
		}
		if productID != nil {
			var count int64
			if e := tx.Raw(`SELECT count(*) FROM frontend.research_products WHERE id=? AND user_email=?`, *productID, email).Scan(&count).Error; e != nil {
				return e
			}
			if count != 1 {
				return ErrProductNotFound
			}
		}
		return tx.Raw(`INSERT INTO frontend.hunts(user_email,title,folder_id,product_id) VALUES (?,?,?,?) RETURNING id`, email, title, folderID, productID).Scan(&id).Error
	})
	return id, err
}

func ProductHuntContext(ctx context.Context, g *gorm.DB, email string, productID int64, folderID *int64) (*models.HuntFolderContext, error) {
	product, err := ResearchProductByID(ctx, g, email, productID)
	if err != nil {
		return nil, err
	}
	out := &models.HuntFolderContext{ReferenceProduct: product, Product: strings.TrimSpace(product.Name + "\n" + product.Description), Brands: []models.FolderBrand{}, Folders: []models.SavedGroupRow{}}
	err = db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		if e := tx.Raw(`SELECT id,name,position,product,product_id FROM frontend.saved_groups WHERE user_email=? AND product_id=? ORDER BY position,id`, email, productID).Scan(&out.Folders).Error; e != nil {
			return e
		}
		// A folder launch narrows references while it still belongs to this product.
		// Otherwise use the product's current folders, never another product's saves.
		var selected *int64
		for _, f := range out.Folders {
			if folderID != nil && f.ID == *folderID {
				selected = folderID
				out.FolderID = f.ID
				out.Name = f.Name
			}
		}
		return tx.Raw(`SELECT advertiser_id,name,note,description,ad_copy FROM (
    SELECT DISTINCT ON (v.advertiser_id) v.* FROM frontend.folder_reference_brands v
    JOIN frontend.saved_groups gr ON gr.id=v.folder_id AND gr.user_email=v.user_email
    WHERE v.user_email=? AND gr.product_id=? AND (?::bigint IS NULL OR gr.id=?)
    ORDER BY v.advertiser_id,v.added_at DESC
   ) refs ORDER BY added_at DESC,advertiser_id LIMIT 12`, email, productID, selected, selected).Scan(&out.Brands).Error
	})
	return out, err
}

func SavedFolderContext(ctx context.Context, g *gorm.DB, email string, folderID int64) (*models.HuntFolderContext, error) {
	var groups []models.SavedGroupRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT id,name,product,product_id FROM frontend.saved_groups WHERE id=? AND user_email=?`, folderID, email).Scan(&groups).Error
	})
	if err != nil || len(groups) == 0 {
		return nil, err
	}
	if groups[0].ProductID != nil {
		return ProductHuntContext(ctx, g, email, *groups[0].ProductID, &folderID)
	}
	out := &models.HuntFolderContext{FolderID: folderID, Name: groups[0].Name, Product: groups[0].Product, Brands: []models.FolderBrand{}}
	err = db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT advertiser_id,name,note,description,ad_copy FROM frontend.folder_reference_brands WHERE folder_id=? AND user_email=? ORDER BY added_at DESC,advertiser_id LIMIT 12`, folderID, email).Scan(&out.Brands).Error
	})
	return out, err
}
func HuntFolderContext(ctx context.Context, g *gorm.DB, huntID int) (*models.HuntFolderContext, error) {
	var rows []struct {
		UserEmail string
		FolderID  *int64
		ProductID *int64
	}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT user_email,folder_id,product_id FROM frontend.hunts WHERE id=?`, huntID).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	h := rows[0]
	if h.ProductID != nil {
		return ProductHuntContext(ctx, g, h.UserEmail, *h.ProductID, h.FolderID)
	}
	if h.FolderID == nil {
		return nil, nil
	}
	return SavedFolderContext(ctx, g, h.UserEmail, *h.FolderID)
}
