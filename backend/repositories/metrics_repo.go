package repositories

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// metrics needle sets and thresholds mirror src/agf/monitoring/metrics.py.
var (
	docIDNeedles    = []string{"doc_id", "http 400", "400 ", "1357004", "1357001"}
	rateLimitNeedle = []string{"429", "rate_limit", "1675004"}
)

const (
	docIDThreshold     = 5
	rateLimitThreshold = 10
	// str(timedelta(hours=1)) in Python — used verbatim in the alert messages.
	oneHourLabel = "1:00:00"
	// FeedStallFloor: a confirmed feed smaller than this almost certainly means a
	// regression (vetoes too aggressive, scoring/refresh broken), not real scarcity.
	// The healthy feed sits in the low hundreds; 50 is well below any normal day.
	FeedStallFloor = 50
)

type crawlCount struct {
	CrawlResult *string `gorm:"column:crawl_result"`
	Count       int64   `gorm:"column:count"`
}

// Metrics ports compute_metrics (24h) + rotation_alerts (1h) and the health
// router's assembly of {window_hours,total,errors,ok,alerts,degraded}.
func Metrics(ctx context.Context, g *gorm.DB, now time.Time) (models.HealthMetrics, error) {
	out := models.HealthMetrics{WindowHours: 24, Alerts: make([]models.Alert, 0)}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		since24 := now.UTC().Add(-24 * time.Hour)
		var counts []crawlCount
		if err := tx.Raw(
			`SELECT crawl_result, count(*) AS count FROM logging.crawl_log
			 WHERE crawled_at >= ? GROUP BY crawl_result`,
			since24,
		).Scan(&counts).Error; err != nil {
			return err
		}
		var total, errs int64
		for _, c := range counts {
			total += c.Count
			if c.CrawlResult != nil && strings.HasPrefix(strings.ToLower(*c.CrawlResult), "error") {
				errs += c.Count
			}
		}
		out.Total = total
		out.Errors = errs
		out.Ok = total - errs

		since1 := now.UTC().Add(-1 * time.Hour)
		var results []string
		if err := tx.Raw(
			`SELECT crawl_result FROM logging.crawl_log
			 WHERE crawled_at >= ? AND crawl_result IS NOT NULL`,
			since1,
		).Scan(&results).Error; err != nil {
			return err
		}
		if docid := countNeedles(results, docIDNeedles); docid >= docIDThreshold {
			out.Alerts = append(out.Alerts, models.Alert{
				Kind:     "doc_id_rotation",
				Message:  fmt.Sprintf("%d doc_id/400 failures in %s — re-heal token/doc_id", docid, oneHourLabel),
				Severity: "critical",
			})
		}
		if rl := countNeedles(results, rateLimitNeedle); rl >= rateLimitThreshold {
			out.Alerts = append(out.Alerts, models.Alert{
				Kind:     "rate_limit",
				Message:  fmt.Sprintf("%d rate-limit hits in %s — back off / rotate proxies", rl, oneHourLabel),
				Severity: "warning",
			})
		}
		out.Degraded = len(out.Alerts) > 0
		return nil
	})
	return out, err
}

// Coverage reports feed-health counts in one read-only pass: the confirmed feed
// size, the incubating (Fresh) cohort, how many confirmed gems carry a real
// funnel fingerprint, and how many web2app funnels are confirmed. Stalled flags
// an implausibly small confirmed feed (< FeedStallFloor) — the signal that the
// "stuck at ~300" plateau has degraded into an outright regression.
func Coverage(ctx context.Context, g *gorm.DB, now time.Time) (models.CoverageMetrics, error) {
	var out models.CoverageMetrics
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		// One pass over the matview: confirmed, incubating, and funnel-carrying.
		if err := tx.Raw(
			`SELECT
			   count(*) FILTER (WHERE NOT incubating)                          AS confirmed_feed,
			   count(*) FILTER (WHERE incubating)                              AS incubating,
			   count(*) FILTER (WHERE NOT incubating AND funnel_intent >= 0.7) AS with_funnel
			 FROM frontend.emerging_scaling_apps`,
		).Scan(&out).Error; err != nil {
			return err
		}
		// Confirmed web2app funnels live in their own discovery table.
		if err := tx.Raw(
			`SELECT count(*) FROM discovery.funnel_candidates WHERE status = 'confirmed'`,
		).Scan(&out.FunnelsConfirmed).Error; err != nil {
			return err
		}
		out.Stalled = out.ConfirmedFeed < FeedStallFloor
		return nil
	})
	return out, err
}

// countNeedles mirrors metrics.py:_count — a result counts once if any needle is
// a substring of its lowercased text.
func countNeedles(results, needles []string) int {
	n := 0
	for _, r := range results {
		lr := strings.ToLower(r)
		for _, ndl := range needles {
			if strings.Contains(lr, ndl) {
				n++
				break
			}
		}
	}
	return n
}
