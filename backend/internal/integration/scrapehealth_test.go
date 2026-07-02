package integration

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// TestScrapeHealth exercises the ported scraping-health repository against the
// real discovery schema. EnginePulse/DiscoveryRate SQL is inherently global
// (max/counts over discovery.candidates), so we assert structure and presence
// (backlog ≥ our seeded count, per_day contains our day) rather than exact
// global equality. discovery.candidate_embeddings is touched only by this test,
// so the "not enough valid embeddings" note is deterministic.
func TestScrapeHealth(t *testing.T) {
	ctx := context.Background()
	g := db.DB()

	uniq := time.Now().UnixNano()
	prefix := fmt.Sprintf("SH-%d-", uniq)
	kw := fmt.Sprintf("sh-kw-%d", uniq)
	today := time.Now().UTC().Format("2006-01-02")

	// Three fresh 'new' candidates (the triage backlog) + a sweep run just now,
	// so the engine reads as alive.
	for i := 0; i < 3; i++ {
		require.NoError(t, g.Exec(
			`INSERT INTO discovery.candidates (page_id, store, store_id, source_keyword, status)
			 VALUES (?, 'ios', ?, ?, 'new')`,
			fmt.Sprintf("%s%d", prefix, i), fmt.Sprintf("%s%d", prefix, i), kw,
		).Error)
	}
	require.NoError(t, g.Exec(`INSERT INTO discovery.sweep_runs DEFAULT VALUES`).Error)
	t.Cleanup(func() {
		g.Exec(`DELETE FROM discovery.candidates WHERE page_id LIKE ?`, prefix+"%")
	})

	// --- EnginePulse: a fresh candidate + a fresh sweep → healthy ---
	pulse, err := repositories.EnginePulse(ctx, g)
	require.NoError(t, err)
	require.Contains(t, []any{"healthy", "wedged"}, pulse["status"])
	require.Equal(t, "healthy", pulse["status"], "fresh candidate + fresh sweep")
	require.GreaterOrEqual(t, pulse["triage_backlog_new"].(int64), int64(3), "≥ our 3 seeded 'new'")
	require.NotNil(t, pulse["newest_candidate"])
	runs := pulse["runs"].(map[string]any)
	for _, tbl := range []string{"sweep_runs", "flywheel_runs", "funnel_sweep_runs"} {
		r, ok := runs[tbl].(map[string]any)
		require.True(t, ok, "runs[%s] present", tbl)
		require.Contains(t, r, "latest")
		require.Contains(t, r, "runs_24h")
		require.Contains(t, r, "runs_7d")
	}
	require.GreaterOrEqual(t, runs["sweep_runs"].(map[string]any)["runs_24h"].(int64), int64(1))
	flags := pulse["flags"].(map[string]any)
	require.Equal(t, false, flags["no_sweep_24h"])

	// --- DiscoveryRate: window math + clamps ---
	rate, err := repositories.DiscoveryRate(ctx, g, 14)
	require.NoError(t, err)
	require.Equal(t, 14, rate["window_days"])
	require.GreaterOrEqual(t, rate["total_in_window"].(int64), int64(3))
	perDay := rate["per_day"].([]map[string]any)
	var sawToday bool
	for _, d := range perDay {
		if d["day"] == today {
			sawToday = true
		}
	}
	require.True(t, sawToday, "per_day contains today (%s) — our 3 candidates landed now", today)
	mix := rate["status_mix"].(map[string]any)
	require.GreaterOrEqual(t, mix["new"].(int64), int64(3))

	clampHi, err := repositories.DiscoveryRate(ctx, g, 999)
	require.NoError(t, err)
	require.Equal(t, 120, clampHi["window_days"], "days clamped to 120")
	clampLo, err := repositories.DiscoveryRate(ctx, g, 0)
	require.NoError(t, err)
	require.Equal(t, 1, clampLo["window_days"], "days clamped to 1")

	// --- IdeaDiversity: no valid embeddings → the note branch ---
	div, err := repositories.IdeaDiversity(ctx, g)
	require.NoError(t, err)
	require.Equal(t, "not enough valid embeddings to measure idea diversity", div["note"])
	require.Contains(t, div, "embedding_coverage")
	require.Contains(t, div, "n_embeddings")
}
