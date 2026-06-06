package repositories

import (
	"context"
	"database/sql"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// dossierEnrichment scans the advertiser-enrichment row (self-join for the
// owning company name).
type dossierEnrichment struct {
	PageCategory  *string `gorm:"column:page_category"`
	IgUsername    *string `gorm:"column:ig_username"`
	IgFollowers   *int    `gorm:"column:ig_followers"`
	ParentCompany *string `gorm:"column:parent_company"`
}

// Dossier ports queries.py:gem_dossier — five reads in one read-only snapshot.
// Returns (nil, nil) when the advertiser is not in the matview (→ 404).
func Dossier(ctx context.Context, g *gorm.DB, advertiserID int) (*models.GemDossier, error) {
	var out *models.GemDossier
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		gems := make([]models.ShortlistRow, 0, 1)
		if err := tx.Raw(
			`SELECT `+mvCols+` FROM frontend.emerging_scaling_apps WHERE advertiser_id = ?`,
			advertiserID,
		).Scan(&gems).Error; err != nil {
			return err
		}
		if len(gems) == 0 {
			return nil // out stays nil → caller returns 404
		}

		// LEFT JOIN: an advertiser with no domain_id yields a NULL domain. GORM's
		// Scan leaves NullString invalid (not an error) on a null/absent row, so
		// d.Domain correctly stays nil — do NOT add a no-rows error check here.
		var domain sql.NullString
		if err := tx.Raw(
			`SELECT d.domain FROM public.advertisers a
			 LEFT JOIN public.domains d ON d.id = a.domain_id WHERE a.id = ?`,
			advertiserID,
		).Scan(&domain).Error; err != nil {
			return err
		}

		apps := make([]models.AppInfo, 0)
		if err := tx.Raw(
			`SELECT DISTINCT ap.store, ap.store_id, ap.name, ap.developer, ap.genre,
			        ap.release_date, ap.rating, ap.rating_count, ap.is_emerging
			 FROM public.apps ap JOIN public.ads a ON a.app_id = ap.id
			 WHERE a.advertiser_id = ?`,
			advertiserID,
		).Scan(&apps).Error; err != nil {
			return err
		}

		funnels := make([]models.FunnelInfo, 0)
		if err := tx.Raw(
			`SELECT f.landing_url, f.final_url_after_redirect, f.screenshot_s3_key
			 FROM public.funnels f JOIN public.ads a ON a.id = f.ad_id
			 WHERE a.advertiser_id = ?`,
			advertiserID,
		).Scan(&funnels).Error; err != nil {
			return err
		}

		creatives := make([]models.CreativeInfo, 0)
		if err := tx.Raw(
			`SELECT c.type, c.s3_key, c.body_text, c.title
			 FROM public.creatives c JOIN public.ads a ON a.id = c.ad_id
			 WHERE a.advertiser_id = ?`,
			advertiserID,
		).Scan(&creatives).Error; err != nil {
			return err
		}

		enr := make([]dossierEnrichment, 0, 1)
		if err := tx.Raw(
			`SELECT a.page_category, a.ig_username, a.ig_followers, p.page_name AS parent_company
			 FROM public.advertisers a
			 LEFT JOIN public.advertisers p ON p.id = a.parent_company_id
			 WHERE a.id = ?`,
			advertiserID,
		).Scan(&enr).Error; err != nil {
			return err
		}

		d := &models.GemDossier{
			Gem:       gems[0],
			Apps:      apps,
			Funnels:   funnels,
			Creatives: creatives,
		}
		if domain.Valid {
			d.Domain = &domain.String
		}
		if len(enr) > 0 {
			d.PageCategory = enr[0].PageCategory
			d.IgUsername = enr[0].IgUsername
			d.IgFollowers = enr[0].IgFollowers
			d.ParentCompany = enr[0].ParentCompany
		}
		out = d
		return nil
	})
	return out, err
}
