package repositories

import (
	"context"
	"math"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/internal/diversity"
	"github.com/edaslakavicius/emerging-app-finder/backend/internal/reco"
	"gorm.io/gorm"
)

// scrapehealth_repo.go ports the read-only scraping-health metrics from the
// Python MCP (src/agf_mcp/metrics.py): discovery throughput (EnginePulse,
// DiscoveryRate) and semantic app-idea diversity (IdeaDiversity). The SQL is a
// 1:1 port — same aliases, same filters — adapted to GORM `?` placeholders; the
// returned map shapes and JSON keys match the Python dicts exactly so the MCP
// surface is identical. The numeric math lives in internal/diversity.
const (
	gemGate  = 60  // frontend.emerging_scaling_products.gem_score gate for "gem"
	embedDim = 768 // valid candidate_embeddings rows; dim=0 rows are placeholders
)

// runTables are the discovery run logs whose recency proves the engine is alive.
var runTables = []string{"sweep_runs", "flywheel_runs", "funnel_sweep_runs"}

// EnginePulse reports whether the discovery engine is alive or wedged: the
// newest candidate and its age, the triage backlog, and per-run-table activity
// (latest / last 24h / last 7d). Wedged when no candidate has arrived in 6h or
// no sweep has run in 24h. Ports metrics.py:engine_pulse.
func EnginePulse(ctx context.Context, g *gorm.DB) (map[string]any, error) {
	var out map[string]any
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		var newest *time.Time
		if err := tx.Raw(`SELECT max(first_seen_at) FROM discovery.candidates`).Scan(&newest).Error; err != nil {
			return err
		}
		var backlog int64
		if err := tx.Raw(`SELECT count(*) FROM discovery.candidates WHERE status = 'new'`).Scan(&backlog).Error; err != nil {
			return err
		}

		runs := map[string]any{}
		for _, tbl := range runTables {
			var row struct {
				Latest *time.Time `gorm:"column:latest"`
				D1     int64      `gorm:"column:d1"`
				D7     int64      `gorm:"column:d7"`
			}
			if err := tx.Raw(`SELECT max(started_at) AS latest,
				count(*) FILTER (WHERE started_at > now() - interval '24 hours') AS d1,
				count(*) FILTER (WHERE started_at > now() - interval '7 days')  AS d7
				FROM discovery.` + tbl).Scan(&row).Error; err != nil {
				return err
			}
			runs[tbl] = map[string]any{
				"latest":   iso(row.Latest),
				"runs_24h": row.D1,
				"runs_7d":  row.D7,
			}
		}

		// Wedged heuristic: no candidate in 6h, or no sweep in 24h.
		ageH := ageHours(newest)
		staleCandidates := ageH != nil && *ageH > 6
		noRecentSweep := runs["sweep_runs"].(map[string]any)["runs_24h"].(int64) == 0
		status := "healthy"
		if staleCandidates || noRecentSweep {
			status = "wedged"
		}

		out = map[string]any{
			"status":                     status,
			"newest_candidate":           iso(newest),
			"newest_candidate_age_hours": roundP(ageH),
			"triage_backlog_new":         backlog,
			"runs":                       runs,
			"flags": map[string]any{
				"stale_candidates_6h": staleCandidates,
				"no_sweep_24h":        noRecentSweep,
			},
		}
		return nil
	})
	return out, err
}

// DiscoveryRate reports new candidates/day over the window, the status mix and
// kept-rate over the whole corpus, and source-keyword breadth in the window.
// days is clamped to [1,120]. Ports metrics.py:discovery_rate.
func DiscoveryRate(ctx context.Context, g *gorm.DB, days int) (map[string]any, error) {
	days = clampDays(days)
	var out map[string]any
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		var perDayRows []struct {
			Day string `gorm:"column:day"`
			N   int64  `gorm:"column:n"`
		}
		if err := tx.Raw(`SELECT to_char(date_trunc('day', first_seen_at), 'YYYY-MM-DD') AS day,
			count(*) AS n
			FROM discovery.candidates
			WHERE first_seen_at > now() - make_interval(days => ?)
			GROUP BY 1 ORDER BY 1`, days).Scan(&perDayRows).Error; err != nil {
			return err
		}
		perDay := make([]map[string]any, 0, len(perDayRows))
		var total int64
		for _, r := range perDayRows {
			perDay = append(perDay, map[string]any{"day": r.Day, "n": r.N})
			total += r.N
		}

		var mixRows []struct {
			Status string `gorm:"column:status"`
			N      int64  `gorm:"column:n"`
		}
		if err := tx.Raw(`SELECT status, count(*) AS n FROM discovery.candidates GROUP BY 1`).Scan(&mixRows).Error; err != nil {
			return err
		}
		statusMix := map[string]any{}
		var kept, dropped int64
		for _, r := range mixRows {
			statusMix[r.Status] = r.N
			switch r.Status {
			case "kept":
				kept = r.N
			case "dropped":
				dropped = r.N
			}
		}
		triaged := kept + dropped
		var keptRate any
		if triaged > 0 {
			keptRate = round(float64(kept)/float64(triaged), 3)
		}

		var breadth int64
		if err := tx.Raw(`SELECT count(DISTINCT source_keyword)
			FROM discovery.candidates
			WHERE first_seen_at > now() - make_interval(days => ?)`, days).Scan(&breadth).Error; err != nil {
			return err
		}

		var avgPerDay any = 0
		if len(perDayRows) > 0 {
			avgPerDay = round(float64(total)/float64(len(perDayRows)), 2)
		}

		out = map[string]any{
			"window_days":                        days,
			"per_day":                            perDay,
			"total_in_window":                    total,
			"avg_per_day":                        avgPerDay,
			"status_mix":                         statusMix,
			"kept_rate":                          keptRate,
			"distinct_source_keywords_in_window": breadth,
		}
		return nil
	})
	return out, err
}

// ideaRow is one kept candidate with a valid store-listing embedding.
type ideaRow struct {
	CandidateID   int64      `gorm:"column:candidate_id"`
	Emb           string     `gorm:"column:emb"`
	Store         string     `gorm:"column:store"`
	PageName      *string    `gorm:"column:page_name"`
	SourceKeyword *string    `gorm:"column:source_keyword"`
	FirstSeenAt   *time.Time `gorm:"column:first_seen_at"`
	IsGem         bool       `gorm:"column:is_gem"`
}

// IdeaDiversity measures the semantic diversity of the kept corpus from its
// store-listing embeddings: embedding coverage, effective concept count, cluster
// concentration (corpus + gems, GemScore gate 60), human-readable concept groups
// (top 12, most-central member as representative), novelty (newest 25% vs older
// baseline), and platform split. Ports metrics.py:idea_diversity.
func IdeaDiversity(ctx context.Context, g *gorm.DB) (map[string]any, error) {
	var out map[string]any
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		var coverage struct {
			Valid int64 `gorm:"column:valid"`
			Total int64 `gorm:"column:total_kept_with_row"`
		}
		if err := tx.Raw(`SELECT count(*) FILTER (WHERE e.dim = ?) AS valid,
			count(*) AS total_kept_with_row
			FROM discovery.candidates c
			LEFT JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id
			WHERE c.status = 'kept'`, embedDim).Scan(&coverage).Error; err != nil {
			return err
		}

		var rows []ideaRow
		if err := tx.Raw(`SELECT e.candidate_id,
			e.embedding::text AS emb,
			c.store,
			c.page_name,
			c.source_keyword,
			c.first_seen_at,
			(f.advertiser_id IS NOT NULL AND f.gem_score >= ?) AS is_gem
			FROM discovery.candidate_embeddings e
			JOIN discovery.candidates c ON c.id = e.candidate_id
			LEFT JOIN frontend.emerging_scaling_products f
			       ON f.advertiser_id = c.kept_advertiser_id::int
			WHERE e.dim = ? AND c.status = 'kept'`, gemGate, embedDim).Scan(&rows).Error; err != nil {
			return err
		}

		var coveragePct any
		if coverage.Total > 0 {
			coveragePct = round(float64(coverage.Valid)/float64(coverage.Total), 2)
		}
		cov := map[string]any{
			"valid_embeddings": coverage.Valid,
			"kept_candidates":  coverage.Total,
			"coverage_pct":     coveragePct,
		}

		n := len(rows)
		if n < 2 {
			out = map[string]any{
				"n_embeddings":       n,
				"embedding_coverage": cov,
				"note":               "not enough valid embeddings to measure idea diversity",
			}
			return nil
		}

		// L2-normalize each vector (cosine ≈ euclidean on unit vectors), and
		// collect gem flags, ages, stores in row order.
		vecs := make([][]float32, n)
		isGem := make([]bool, n)
		ages := make([]float64, n)
		var nGems int
		for i, r := range rows {
			v, err := reco.ParsePgFloatArray(r.Emb)
			if err != nil {
				return err
			}
			vecs[i] = l2normalize32(v)
			isGem[i] = r.IsGem
			if r.IsGem {
				nGems++
			}
			ages[i] = ageDays(r.FirstSeenAt)
		}

		k := n / 20
		if k < 2 {
			k = 2
		}
		if k > 30 {
			k = 30
		}
		if k > n {
			k = n
		}
		labels := diversity.KMeans(vecs, k, 0)

		// Cluster sizes and centroids (mean of members) over k clusters.
		sizes := make([]int, k)
		gemSizes := make([]int, k)
		centroids := make([][]float64, k)
		dim := len(vecs[0])
		for c := range centroids {
			centroids[c] = make([]float64, dim)
		}
		for i, lbl := range labels {
			sizes[lbl]++
			if isGem[i] {
				gemSizes[lbl]++
			}
			for j := 0; j < dim && j < len(vecs[i]); j++ {
				centroids[lbl][j] += float64(vecs[i][j])
			}
		}
		for c := range centroids {
			if sizes[c] == 0 {
				continue
			}
			for j := range centroids[c] {
				centroids[c][j] /= float64(sizes[c])
			}
		}

		nClusters := 0
		for _, s := range sizes {
			if s > 0 {
				nClusters++
			}
		}

		eff := diversity.EffectiveCount(sizes)
		groups := conceptGroups(rows, labels, vecs, centroids, sizes, 12)
		nov := diversity.Novelty(vecs, ages)

		out = map[string]any{
			"n_embeddings":       n,
			"embedding_coverage": cov,
			"n_gems":             nGems,
			"n_clusters":         nClusters,
			"effective_concepts": round(eff, 2),
			"concentration": map[string]any{
				"corpus": concentrationDict(sizes),
				"gems":   concentrationDict(gemSizes),
			},
			"concept_groups": groups,
			"novelty":        nov,
			"platform_split": map[string]any{
				"kept": storeSplit(rows, nil),
				"gems": storeSplit(rows, isGem),
			},
		}
		return nil
	})
	return out, err
}

// conceptGroups returns the largest clusters (up to top), each labeled by its
// most-central member (highest cosine to the cluster centroid). Ports
// metrics.py:_concept_groups.
func conceptGroups(rows []ideaRow, labels []int, vecs [][]float32, centroids [][]float64, sizes []int, top int) []map[string]any {
	order := make([]int, len(sizes))
	for i := range order {
		order[i] = i
	}
	sortByDesc(order, func(c int) int { return sizes[c] })

	groups := make([]map[string]any, 0, top)
	for _, cid := range order {
		if sizes[cid] == 0 {
			continue
		}
		var rep int = -1
		var bestSim = math.Inf(-1)
		for i, lbl := range labels {
			if lbl != cid {
				continue
			}
			if s := dotF64(vecs[i], centroids[cid]); s > bestSim {
				bestSim, rep = s, i
			}
		}
		groups = append(groups, map[string]any{
			"size":                   sizes[cid],
			"representative_app":     rows[rep].PageName,
			"representative_keyword": rows[rep].SourceKeyword,
		})
		if len(groups) >= top {
			break
		}
	}
	return groups
}

// concentrationDict wraps diversity.Concentration in the {total, top1_share,
// top5_share} shape, with null shares when the corpus is empty (metrics.py's
// _concentration). Shares are rounded to 2 dp.
func concentrationDict(sizes []int) map[string]any {
	total := 0
	for _, s := range sizes {
		total += s
	}
	if total == 0 {
		return map[string]any{"total": 0, "top1_share": nil, "top5_share": nil}
	}
	top1, top5 := diversity.Concentration(sizes)
	return map[string]any{
		"total":      total,
		"top1_share": round(top1, 2),
		"top5_share": round(top5, 2),
	}
}

// storeSplit counts kept candidates by store; when mask is non-nil only masked
// (gem) rows are counted. An empty store reads as "?". Ports metrics.py:
// _store_split.
func storeSplit(rows []ideaRow, mask []bool) map[string]any {
	out := map[string]any{}
	counts := map[string]int64{}
	for i, r := range rows {
		if mask != nil && !mask[i] {
			continue
		}
		s := r.Store
		if s == "" {
			s = "?"
		}
		counts[s]++
	}
	for k, v := range counts {
		out[k] = v
	}
	return out
}

// --------------------------------------------------------------------------- //
// helpers
// --------------------------------------------------------------------------- //

func iso(ts *time.Time) any {
	if ts == nil {
		return nil
	}
	return ts.Format(time.RFC3339)
}

func ageHours(ts *time.Time) *float64 {
	if ts == nil {
		return nil
	}
	h := time.Since(*ts).Hours()
	return &h
}

func ageDays(ts *time.Time) float64 {
	if ts == nil {
		return 1e9
	}
	return time.Since(*ts).Hours() / 24.0
}

func clampDays(days int) int {
	if days < 1 {
		return 1
	}
	if days > 120 {
		return 120
	}
	return days
}

func round(x float64, nd int) float64 {
	p := math.Pow(10, float64(nd))
	return math.Round(x*p) / p
}

// roundP rounds a nullable float to 2 dp, preserving null (metrics.py's _round).
func roundP(x *float64) any {
	if x == nil {
		return nil
	}
	return round(*x, 2)
}

func l2normalize32(v []float32) []float32 {
	var norm float64
	for _, x := range v {
		norm += float64(x) * float64(x)
	}
	norm = math.Sqrt(norm)
	if norm == 0 {
		return v
	}
	out := make([]float32, len(v))
	for i, x := range v {
		out[i] = float32(float64(x) / norm)
	}
	return out
}

func dotF64(a []float32, b []float64) float64 {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	var d float64
	for i := 0; i < n; i++ {
		d += float64(a[i]) * b[i]
	}
	return d
}

// sortByDesc stable-sorts idx in place by key(idx) descending.
func sortByDesc(idx []int, key func(int) int) {
	for i := 1; i < len(idx); i++ {
		for j := i; j > 0 && key(idx[j]) > key(idx[j-1]); j-- {
			idx[j], idx[j-1] = idx[j-1], idx[j]
		}
	}
}
