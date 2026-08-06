// Package repositories — creative reads for the MCP download path.
//
// public.creatives(ad_id) is indexed (migration 144); filtering by advertiser
// goes through public.ads so the join stays on that index rather than scanning
// creatives.
package repositories

import (
	"context"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// AdCreatives returns one advertiser's creatives, newest ad first. The URL
// field is left empty here — the caller fills it in, because the public base
// URL is a transport concern the repository has no business knowing.
func AdCreatives(ctx context.Context, g *gorm.DB, advertiserID int, limit, offset int) ([]models.AdCreativeRow, error) {
	var rows []models.AdCreativeRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT a.id            AS ad_id,
			        a.external_ad_id,
			        a.link_url,
			        a.is_active,
			        c.type,
			        c.content_type,
			        c.file_size,
			        c.title,
			        c.body_text,
			        c.s3_key
			   FROM public.ads a
			   JOIN public.creatives c ON c.ad_id = a.id
			  WHERE a.advertiser_id = ?
			    AND c.s3_key IS NOT NULL
			  ORDER BY a.last_seen DESC, c.id
			  LIMIT ? OFFSET ?`,
			advertiserID, limit, offset,
		).Scan(&rows).Error
	})
	return rows, err
}
