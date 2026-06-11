package repositories

import (
	"context"
	"strings"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// ShortlistParams carries the Discover filter bar's server-side filters. The
// zero value means "no filter" on every axis (and the controller supplies the
// limit/offset defaults).
type ShortlistParams struct {
	Cohort    *string // exact cohort key
	Category  *string // public.apps.genre, reached through the matview's app_id
	Q         string  // case-insensitive substring of app_name or page_name
	MinScore  float64
	Rising    bool // hide gems whose weekly ad count fell (latest < first)
	HasFunnel bool // funnel_intent >= 0.5
	MinAdAge  int  // max_longevity_days >= N; NULL longevity is excluded
	Sort      string
	Limit     int
	Offset    int
}

// shortlistOrder maps the public sort keys to ORDER BY clauses. Pagination
// needs a deterministic order, so every key tie-breaks on advertiser_id.
var shortlistOrder = map[string]string{
	"gemscore": "gem_score DESC, advertiser_id",
	"fastest":  "scaling_velocity DESC NULLS LAST, advertiser_id",
	"newest":   "max_longevity_days ASC NULLS LAST, advertiser_id",
}

// ValidSort reports whether s is a known shortlist sort key.
func ValidSort(s string) bool {
	_, ok := shortlistOrder[s]
	return ok
}

// hasDownloadedCreative only surfaces advertisers we can actually show an ad
// for — i.e. with at least one downloaded creative. This hides incumbent/
// firehose advertisers that have a score but no scraped ad media (empty hero
// tiles, flat counts).
const hasDownloadedCreative = `EXISTS (
	SELECT 1 FROM public.creatives c
	JOIN public.ads a ON a.id = c.ad_id
	WHERE a.advertiser_id = frontend.emerging_scaling_apps.advertiser_id
	  AND c.s3_key IS NOT NULL
)`

// likePattern wraps q for a substring ILIKE, escaping LIKE metacharacters so
// user input matches literally.
func likePattern(q string) string {
	r := strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)
	return "%" + r.Replace(q) + "%"
}

// shortlistConds builds the WHERE clauses for p, shared by the page query and
// the total count. (The fixed-shape `CAST(? AS text) IS NULL` idiom from the
// queries.py port doesn't scale to this many optional axes.)
func shortlistConds(p ShortlistParams) ([]string, []any) {
	conds := []string{"gem_score >= ?", hasDownloadedCreative}
	args := []any{p.MinScore}
	if p.Cohort != nil {
		conds = append(conds, "cohort = ?")
		args = append(args, *p.Cohort)
	}
	if p.Category != nil {
		conds = append(conds, `EXISTS (
			SELECT 1 FROM public.apps
			WHERE public.apps.id = frontend.emerging_scaling_apps.app_id
			  AND public.apps.genre = ?)`)
		args = append(args, *p.Category)
	}
	if p.Q != "" {
		pat := likePattern(p.Q)
		conds = append(conds, "(app_name ILIKE ? OR page_name ILIKE ?)")
		args = append(args, pat, pat)
	}
	if p.Rising {
		// Mirrors the product rule the client used to apply: a gem only counts
		// as falling when its growth series exists and latest < first.
		conds = append(conds, "(latest_count IS NULL OR first_count IS NULL OR latest_count >= first_count)")
	}
	if p.HasFunnel {
		conds = append(conds, "funnel_intent >= 0.5")
	}
	if p.MinAdAge > 0 {
		conds = append(conds, "max_longevity_days >= ?")
		args = append(args, p.MinAdAge)
	}
	return conds, args
}

// Shortlist returns one page of the gems feed plus the total row count across
// all pages of the same filter set. Both queries run in one READ ONLY tx —
// note that's READ COMMITTED, so a concurrent matview refresh landing between
// the two statements can skew the total by one refresh generation; that's
// transient and self-corrects on the client's next fetch.
func Shortlist(ctx context.Context, g *gorm.DB, p ShortlistParams) ([]models.ShortlistRow, int, error) {
	conds, args := shortlistConds(p)
	where := strings.Join(conds, "\n   AND ")
	order, ok := shortlistOrder[p.Sort]
	if !ok {
		order = shortlistOrder["gemscore"] // controller validates; default defensively
	}

	rows := make([]models.ShortlistRow, 0)
	total := 0
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		if err := tx.Raw(
			`SELECT count(*) FROM frontend.emerging_scaling_apps WHERE `+where, args...,
		).Scan(&total).Error; err != nil {
			return err
		}
		scanned := make([]mvScanRow, 0)
		if err := tx.Raw(
			`SELECT `+mvCols+` FROM frontend.emerging_scaling_apps
			 WHERE `+where+`
			 ORDER BY `+order+`
			 LIMIT ? OFFSET ?`,
			append(append([]any{}, args...), p.Limit, p.Offset)...,
		).Scan(&scanned).Error; err != nil {
			return err
		}
		rows = toShortlistRows(scanned)
		return attachEnrichment(tx, rows)
	})
	if rows == nil {
		rows = make([]models.ShortlistRow, 0)
	}
	return rows, total, err
}

// ShortlistCategories returns the distinct app categories (public.apps.genre)
// of the feed under p's filters, with per-category gem counts — each dropdown
// number must equal what picking that category would yield, so every active
// filter EXCEPT the category itself applies. NULL genres are skipped: those
// gems are only reachable without a category filter.
func ShortlistCategories(ctx context.Context, g *gorm.DB, p ShortlistParams) ([]models.CategoryCount, error) {
	p.Category = nil // counts are for switching categories
	conds, args := shortlistConds(p)
	where := strings.Join(conds, "\n   AND ")
	cats := make([]models.CategoryCount, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT public.apps.genre AS category, count(*) AS count
			 FROM frontend.emerging_scaling_apps
			 JOIN public.apps ON public.apps.id = frontend.emerging_scaling_apps.app_id
			 WHERE public.apps.genre IS NOT NULL
			   AND `+where+`
			 GROUP BY public.apps.genre
			 ORDER BY count DESC, category ASC`, args...,
		).Scan(&cats).Error
	})
	if cats == nil {
		cats = make([]models.CategoryCount, 0)
	}
	return cats, err
}
