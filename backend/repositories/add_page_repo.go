package repositories

import (
	"context"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// addPageJobSelect is the shared read shape for an add_page job. phase and
// triage_note come out of the job's payload jsonb (no schema change needed);
// icon_url comes from the gem matview, which only has a row once the job has
// finished enriching.
//
// page_name falls back to the matview's app_name because the Meta listing
// scrape does not write public.advertisers.page_name — a pasted page therefore
// keeps a NULL page_name and the result card would read "This advertiser is in
// your feed" for an advertiser we can perfectly well name (verified on prod
// 2026-08-06: page_name NULL, app_name "Zutobi: Permit & Driving Prep").
const addPageJobSelect = `
	SELECT j.id, j.advertiser_id, j.status,
	       j.payload->>'phase'       AS phase,
	       j.payload->>'triage_note' AS triage_note,
	       j.error_text,
	       COALESCE(NULLIF(btrim(adv.page_name), ''), esa.app_name) AS page_name,
	       esa.icon_url,
	       (SELECT count(*) FROM public.ads a WHERE a.advertiser_id = j.advertiser_id) AS ad_count
	  FROM public.pipeline_jobs j
	  JOIN public.advertisers adv ON adv.id = j.advertiser_id
	  LEFT JOIN frontend.emerging_scaling_apps esa ON esa.advertiser_id = j.advertiser_id`

// PageIDForAdID resolves a single-ad Ad Library link to its advertiser's page
// id using ads we have already scraped. Returns "" when we have never seen it —
// Meta's ad-details response carries no page id, so there is nothing cheaper to
// fall back on.
func PageIDForAdID(ctx context.Context, g *gorm.DB, adID string) (string, error) {
	var pageID string
	res := g.WithContext(ctx).Raw(
		`SELECT adv.page_id
		   FROM public.ads a
		   JOIN public.advertisers adv ON adv.id = a.advertiser_id
		  WHERE a.external_ad_id = ?
		  LIMIT 1`, adID).Scan(&pageID)
	return pageID, res.Error
}

// UpsertAdvertiserByPageID returns the advertiser id for a page id, creating a
// minimal row when we have never seen the page. alreadyKnown reports whether
// the row pre-existed; the scrape fills in page_name either way.
func UpsertAdvertiserByPageID(ctx context.Context, g *gorm.DB, pageID string) (int64, bool, error) {
	var existing int64
	res := g.WithContext(ctx).Raw(
		`SELECT id FROM public.advertisers WHERE page_id = ?`, pageID).Scan(&existing)
	if res.Error != nil {
		return 0, false, res.Error
	}
	if res.RowsAffected > 0 {
		return existing, true, nil
	}
	var created int64
	res = g.WithContext(ctx).Raw(
		`INSERT INTO public.advertisers (page_id)
		 VALUES (?)
		 ON CONFLICT (page_id) DO UPDATE SET last_seen = now()
		 RETURNING id`, pageID).Scan(&created)
	return created, false, res.Error
}

// ActiveAddPageJob returns the queued/running add_page job for an advertiser,
// or nil. The partial unique index pipeline_jobs_active_uq guarantees at most
// one, so this is the dedupe read.
func ActiveAddPageJob(ctx context.Context, g *gorm.DB, advertiserID int64) (*models.AddPageJobRow, error) {
	return scanAddPageJob(g.WithContext(ctx).Raw(addPageJobSelect+`
		 WHERE j.kind = 'add_page' AND j.advertiser_id = ?
		   AND j.status IN ('queued','running')
		 ORDER BY j.id DESC LIMIT 1`, advertiserID))
}

// AddPageJobByID reads one add_page job for the status poll.
func AddPageJobByID(ctx context.Context, g *gorm.DB, jobID int64) (*models.AddPageJobRow, error) {
	return scanAddPageJob(g.WithContext(ctx).Raw(addPageJobSelect+`
		 WHERE j.kind = 'add_page' AND j.id = ?`, jobID))
}

// InsertAddPageJob enqueues one job. ON CONFLICT against the partial active
// unique index makes a concurrent double-paste a no-op; the caller re-reads the
// active job rather than treating that as an error.
func InsertAddPageJob(ctx context.Context, g *gorm.DB, advertiserID int64) error {
	return g.WithContext(ctx).Exec(
		`INSERT INTO public.pipeline_jobs (kind, advertiser_id)
		 VALUES ('add_page', ?)
		 ON CONFLICT (kind, advertiser_id) WHERE status IN ('queued','running')
		 DO NOTHING`, advertiserID).Error
}

func scanAddPageJob(tx *gorm.DB) (*models.AddPageJobRow, error) {
	var row models.AddPageJobRow
	res := tx.Scan(&row)
	if res.Error != nil {
		return nil, res.Error
	}
	if res.RowsAffected == 0 {
		return nil, nil
	}
	return &row, nil
}
