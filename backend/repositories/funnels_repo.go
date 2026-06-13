package repositories

import (
	"context"
	"fmt"
	"strings"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// FunnelsParams carries the Funnels view's server-side filters. The zero value
// means "no filter" on every axis; the controller supplies limit/offset.
type FunnelsParams struct {
	Q       string // case-insensitive substring of landing_domain or page_name
	HasQuiz bool   // only funnels with a detected quiz (quiz_steps > 0)
	Sort    string // sort key; see funnelsOrder
	Dir     string // "asc"/"desc"; "" = the key's natural direction
	Limit   int
	Offset  int
}

// funnelsOrder maps the public sort keys to the SELECT alias they order by and
// the direction the key means by default. The expressions are aggregate
// aliases (the query GROUPs BY landing_domain), so ORDER BY references them by
// name — legal in Postgres against the grouped projection.
var funnelsOrder = map[string]struct {
	expr string
	def  string
}{
	"newest":      {"last_seen_at", "DESC"},
	"advertisers": {"advertiser_count", "DESC"},
	"seen":        {"times_seen", "DESC"},
}

// FunnelsValidSort reports whether s is a known sort key ("" = default).
func FunnelsValidSort(s string) bool {
	if s == "" {
		return true
	}
	_, ok := funnelsOrder[s]
	return ok
}

func funnelsOrderClause(sort, dir string) string {
	o, ok := funnelsOrder[sort]
	if !ok {
		o = funnelsOrder["newest"]
	}
	d := o.def
	if dir == "asc" || dir == "desc" {
		d = strings.ToUpper(dir)
	}
	// last_seen_at tiebreak keeps paging stable when the primary key ties.
	if o.expr == "last_seen_at" {
		return fmt.Sprintf("%s %s NULLS LAST", o.expr, d)
	}
	return fmt.Sprintf("%s %s, last_seen_at DESC NULLS LAST", o.expr, d)
}

// funnelsConds builds the WHERE clause over discovery.funnel_candidates. Only
// confirmed funnels (a landing page actually rendered) are ever returned — the
// "new"/"dropped" rows are pipeline state, not results.
func funnelsConds(p FunnelsParams) ([]string, []any) {
	conds := []string{"status = 'confirmed'", "landing_domain IS NOT NULL"}
	var args []any
	if p.Q != "" {
		pat := likePattern(p.Q)
		conds = append(conds, "(landing_domain ILIKE ? OR page_name ILIKE ?)")
		args = append(args, pat, pat)
	}
	if p.HasQuiz {
		conds = append(conds, "quiz_steps > 0")
	}
	return conds, args
}

// Funnels returns one page of web-to-app funnels grouped by landing domain,
// plus the total domain count across all pages of the same filter set. Many
// advertiser pages can drive one destination, so the row is the destination:
// advertiser_count is the distinct pages, and times_seen/first/last are
// aggregated across them. Runs both statements in one READ ONLY tx.
func Funnels(ctx context.Context, g *gorm.DB, p FunnelsParams) ([]models.FunnelRow, int, error) {
	conds, args := funnelsConds(p)
	where := strings.Join(conds, "\n   AND ")
	order := funnelsOrderClause(p.Sort, p.Dir)

	const grouped = `
		SELECT landing_domain,
		       count(DISTINCT page_id)        AS advertiser_count,
		       max(page_name)                 AS sample_page_name,
		       max(cta_type)                  AS cta_type,
		       bool_or(quiz_steps > 0)        AS has_quiz,
		       max(quiz_steps)                AS max_quiz_steps,
		       sum(times_seen)                AS times_seen,
		       min(first_seen_at)             AS first_seen_at,
		       max(last_seen_at)              AS last_seen_at,
		       max(landing_url)               AS sample_landing_url,
		       max(store)                     AS store,
		       max(store_id)                  AS store_id
		FROM discovery.funnel_candidates
		WHERE %s
		GROUP BY landing_domain`

	rows := make([]models.FunnelRow, 0)
	total := 0
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		countSQL := `SELECT count(*) FROM (` + fmt.Sprintf(grouped, where) + `) t`
		if err := tx.Raw(countSQL, args...).Scan(&total).Error; err != nil {
			return err
		}
		pageSQL := fmt.Sprintf(grouped, where) + `
		 ORDER BY ` + order + `
		 LIMIT ? OFFSET ?`
		return tx.Raw(pageSQL, append(append([]any{}, args...), p.Limit, p.Offset)...).Scan(&rows).Error
	})
	if rows == nil {
		rows = make([]models.FunnelRow, 0)
	}
	return rows, total, err
}
