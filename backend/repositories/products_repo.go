package repositories

import (
	"context"
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

var ErrProductNotFound = errors.New("product not found")
var ErrProductFolderMismatch = errors.New("the folder belongs to a different product")

// ResolveProductLink reads existing shared discovery and the caller's private
// MCP captures. It neither starts a scrape nor guesses between matching pages.
func ResolveProductLink(ctx context.Context, g *gorm.DB, email, raw string) (*models.ProductResolution, error) {
	link, err := helpers.ParseProductLink(raw)
	if err != nil {
		return nil, err
	}
	out := &models.ProductResolution{URL: link.URL, IdentityKey: link.Kind + ":" + link.Key, Matches: []models.ProductMatch{}}
	err = db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		if link.Kind == "ad" {
			var pages []string
			if e := tx.Raw(`SELECT DISTINCT page_id FROM (
    SELECT adv.page_id FROM public.ads a JOIN public.advertisers adv ON adv.id=a.advertiser_id WHERE a.external_ad_id=?
    UNION SELECT r.page_id FROM frontend.ad_search_results r JOIN frontend.ad_searches s ON s.id=r.search_id WHERE r.ad_archive_id=? AND s.created_by=?
   ) pages LIMIT 2`, link.Key, link.Key, email).Scan(&pages).Error; e != nil {
				return e
			}
			if len(pages) != 1 {
				return helpers.InvalidProduct("this ad is not in your saved scrape data; use the advertiser’s Ad Library page link")
			}
			link.Kind = "meta"
			link.Key = pages[0]
			out.URL = "https://www.facebook.com/ads/library/?view_all_page_id=" + pages[0]
			out.IdentityKey = "meta:" + pages[0]
		}
		if link.Kind == "meta" {
			out.PageID = link.Key
		}
		if link.Kind == "ios" || link.Kind == "android" {
			out.Store = link.Kind
			out.StoreID = link.Key
			var listing struct{ Name, Description string }
			if e := tx.Raw(`SELECT COALESCE(name,'') AS name,COALESCE(left(description,2000),'') AS description FROM public.apps WHERE store=? AND store_id=? LIMIT 1`, link.Kind, link.Key).Scan(&listing).Error; e != nil {
				return e
			}
			out.Name, out.Description = listing.Name, listing.Description
		}
		if link.Kind == "meta" || link.Kind == "web" {
			// Limit the website lookup to known product destinations, not tracking URL
			// guesses. Multiple pages are returned to the caller for explicit selection.
			query := `SELECT DISTINCT ON (adv.page_id) adv.page_id, COALESCE(NULLIF(adv.web_name,''),NULLIF(adv.page_name,''),NULLIF(m.app_name,''),'') AS name,
    COALESCE(left(adv.web_description,2000),'') AS description
    FROM public.advertisers adv LEFT JOIN frontend.emerging_scaling_apps m ON m.advertiser_id=adv.id WHERE `
			args := []any{}
			if link.Kind == "meta" {
				query += `adv.page_id=?`
				args = append(args, link.Key)
			} else {
				query += `lower(regexp_replace(split_part(split_part(split_part(COALESCE(adv.web_landing_url,''),'://',2),'/',1),chr(63),1),'^www[.]',''))=?
     OR lower(regexp_replace(split_part(split_part(split_part(COALESCE(m.funnel_url,''),'://',2),'/',1),chr(63),1),'^www[.]',''))=?`
				args = append(args, link.Host, link.Host)
			}
			query += ` ORDER BY adv.page_id,adv.id LIMIT 10`
			if e := tx.Raw(query, args...).Scan(&out.Matches).Error; e != nil {
				return e
			}
			var captures []models.ProductMatch
			q := `SELECT DISTINCT ON (r.page_id) r.page_id,r.page_name AS name,'' AS description FROM frontend.ad_search_results r
     JOIN frontend.ad_searches s ON s.id=r.search_id WHERE s.created_by=? AND `
			args = []any{email}
			if link.Kind == "meta" {
				q += `r.page_id=?`
				args = append(args, link.Key)
			} else {
				q += `lower(regexp_replace(split_part(split_part(split_part(COALESCE(r.link_url,''),'://',2),'/',1),chr(63),1),'^www[.]',''))=?`
				args = append(args, link.Host)
			}
			q += ` ORDER BY r.page_id,r.observed_at DESC LIMIT 10`
			if e := tx.Raw(q, args...).Scan(&captures).Error; e != nil {
				return e
			}
			for _, c := range captures {
				found := false
				for i, m := range out.Matches {
					if m.PageID == c.PageID {
						found = true
						if m.Name == "" {
							out.Matches[i].Name = c.Name
						}
					}
				}
				if !found {
					out.Matches = append(out.Matches, c)
				}
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if len(out.Matches) == 1 {
		out.Name = out.Matches[0].Name
		out.Description = out.Matches[0].Description
	}
	return out, nil
}

func ListResearchProducts(ctx context.Context, g *gorm.DB, email string) ([]models.ResearchProduct, error) {
	rows := []models.ResearchProduct{}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT id,name,url,description,page_id,store,store_id,advertiser_id,left(source_description,2000) AS source_description,ad_copy FROM frontend.research_product_context WHERE user_email=? ORDER BY id DESC`, email).Scan(&rows).Error
	})
	return rows, err
}
func ResearchProductByID(ctx context.Context, g *gorm.DB, email string, id int64) (*models.ResearchProduct, error) {
	var rows []models.ResearchProduct
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT id,name,url,description,page_id,store,store_id,advertiser_id,left(source_description,2000) AS source_description,ad_copy FROM frontend.research_product_context WHERE user_email=? AND id=?`, email, id).Scan(&rows).Error
	})
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, ErrProductNotFound
	}
	return &rows[0], nil
}
func CreateResearchProduct(ctx context.Context, g *gorm.DB, email, raw, name, description, pageID string) (*models.ResearchProduct, error) {
	resolved, err := ResolveProductLink(ctx, g, email, raw)
	if err != nil {
		return nil, err
	}
	if pageID != "" {
		valid := resolved.PageID == pageID
		for _, m := range resolved.Matches {
			if m.PageID == pageID {
				valid = true
				if name == "" {
					name = m.Name
				}
				if description == "" {
					description = m.Description
				}
			}
		}
		if !valid {
			return nil, helpers.InvalidProduct("select a brand matched to this link")
		}
	} else if len(resolved.Matches) > 1 {
		return nil, helpers.InvalidProduct("choose which brand this product belongs to")
	} else if len(resolved.Matches) == 1 {
		pageID = resolved.Matches[0].PageID
	}
	if pageID == "" {
		pageID = resolved.PageID
	}
	name = strings.TrimSpace(name)
	description = strings.TrimSpace(description)
	if name == "" {
		name = resolved.Name
	}
	if description == "" {
		description = resolved.Description
	}
	if utf8.RuneCountInString(name) < 1 || utf8.RuneCountInString(name) > 200 {
		return nil, helpers.InvalidProduct("enter a product name of 1–200 characters")
	}
	if utf8.RuneCountInString(description) > 2000 {
		return nil, helpers.InvalidProduct("description must be 2000 characters or fewer")
	}
	identity := resolved.IdentityKey
	if pageID != "" {
		identity = "meta:" + pageID
	}
	var id int64
	err = db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`INSERT INTO frontend.research_products(user_email,identity_key,name,url,description,page_id,store,store_id)
  VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (user_email,identity_key) DO UPDATE SET identity_key=EXCLUDED.identity_key RETURNING id`, email, identity, name, resolved.URL, description, pageID, resolved.Store, resolved.StoreID).Scan(&id).Error
	})
	if err != nil {
		return nil, err
	}
	return ResearchProductByID(ctx, g, email, id)
}

// A zero product ID detaches the folder. Ownership is checked in the write.
func AttachFolderProduct(ctx context.Context, g *gorm.DB, email string, folderID, productID int64) (bool, error) {
	var changed int64
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		res := tx.Exec(`UPDATE frontend.saved_groups SET product_id=NULLIF(?,0), product=''
    WHERE id=? AND user_email=? AND (?=0 OR EXISTS (SELECT 1 FROM frontend.research_products WHERE id=? AND user_email=?))`, productID, folderID, email, productID, productID, email)
		changed = res.RowsAffected
		return res.Error
	})
	return changed > 0, err
}
