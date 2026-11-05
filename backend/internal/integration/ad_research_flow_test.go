package integration

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/routers"
	"github.com/gin-gonic/gin"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/stretchr/testify/require"
)

// Opt-in continuous workflow: every model input comes from the Meta scrape
// performed in this run, and candidate queries come from the finder response.
func TestAdResearchFullFlowLive(t *testing.T) {
	if os.Getenv("AGF_AD_RESEARCH_FLOW") != "1" {
		t.Skip("set AGF_AD_RESEARCH_FLOW=1 to use Meta, Gemini and Jev")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Minute)
	defer cancel()
	owner, key := seedKeyedUser(t, ctx, "admin")
	t.Cleanup(func() {
		require.NoError(t, db.DB().Exec(`DELETE FROM public.pipeline_jobs WHERE kind='ad_search' AND advertiser_id IN (SELECT id FROM frontend.ad_searches WHERE created_by=?)`, owner).Error)
		require.NoError(t, db.DB().Exec(`DELETE FROM frontend.ad_searches WHERE created_by=?`, owner).Error)
	})
	r := gin.New()
	routers.Init(r)
	server := httptest.NewServer(r)
	defer server.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "ad-research-full-flow", Version: "1"}, nil)
	session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: server.URL + "/mcp", HTTPClient: mcpClientFor(key)}, nil)
	require.NoError(t, err)
	defer session.Close()
	call := func(name string, args map[string]any) json.RawMessage {
		t.Helper()
		res, err := session.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: args})
		require.NoError(t, err)
		require.False(t, res.IsError, firstText(res))
		return json.RawMessage(firstText(res))
	}
	guide := call("ad_research_guide", map[string]any{})
	require.Contains(t, string(guide), "find_similar_brands")
	require.Contains(t, string(guide), "judge_ads")
	type scrapeResult struct {
		ID       int64                   `json:"scrape_id"`
		Status   string                  `json:"status"`
		Ads      []models.AdSearchResult `json:"ads"`
		Progress struct {
			Partial bool `json:"partial"`
		} `json:"progress"`
		Sources map[string]struct {
			Advertisers []struct {
				ID   string `json:"page_id"`
				Name string `json:"page_name"`
			} `json:"advertisers"`
		} `json:"sources"`
	}
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	require.NoError(t, err)
	scrape := func(name string, args map[string]any) scrapeResult {
		t.Helper()
		var result scrapeResult
		require.NoError(t, json.Unmarshal(call(name, args), &result))
		if result.Status == "queued" {
			require.NoError(t, db.DB().Exec(`UPDATE public.pipeline_jobs SET run_after='1970-01-01' WHERE kind='ad_search' AND advertiser_id=?`, result.ID).Error)
			cmd := exec.CommandContext(ctx, filepath.Join(root, ".venv/bin/python"), "-c", `
import sys
from agf.db.engine import get_engine
from agf.discovery.ad_search_daemon import process_once,run_subprocess
target=int(sys.argv[1])
def run(search_id,job_id):
    assert search_id==target, 'another job claimed'
    run_subprocess(search_id,job_id)
assert process_once(get_engine(),runner=run)
`, strconv.FormatInt(result.ID, 10))
			cmd.Dir = root
			cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Join(root, "src"), "AGF_DATABASE_URL="+os.Getenv("AGF_IT_DB_URL"), "GEMINI_API_KEY=", "TYPESAFE_API_KEY=")
			require.NoError(t, cmd.Run())
		}
		require.NoError(t, json.Unmarshal(call("ad_scrape_status", map[string]any{"scrape_id": result.ID, "limit": 50}), &result))
		require.Equal(t, "done", result.Status)
		require.False(t, result.Progress.Partial, "all requested Meta sources must complete")
		return result
	}
	started := time.Now()
	timings := map[string]float64{}
	brand := os.Getenv("AGF_FLOW_BRAND")
	if brand == "" {
		brand = "Femrun"
	}
	brief := os.Getenv("AGF_FLOW_BRIEF")
	if brief == "" {
		brief = "Accessible guided fitness for beginners, including women seeking a consistent routine and help with body-shape concerns. Look across activities; compare the advertised customer need."
	}
	lookup := scrape("find_advertisers", map[string]any{"queries": []string{brand}, "country": "US"})
	pageIDs := []string{}
	for _, source := range lookup.Sources {
		for _, p := range source.Advertisers {
			if strings.EqualFold(strings.TrimSpace(p.Name), brand) {
				pageIDs = append(pageIDs, p.ID)
			}
		}
	}
	require.Len(t, pageIDs, 1, "source identity needs manual selection")
	timings["lookup"] = time.Since(started).Seconds()
	t.Logf("Resolved %s to %s", brand, pageIDs[0])
	source := scrape("get_advertiser_ads", map[string]any{"page_id": pageIDs[0], "country": "US"})
	refs := []map[string]any{}
	seen := map[string]bool{}
	for _, a := range source.Ads {
		if a.BodyText == nil || len(strings.Fields(*a.BodyText)) < 8 || seen[*a.BodyText] {
			continue
		}
		seen[*a.BodyText] = true
		refs = append(refs, map[string]any{"scrape_id": source.ID, "ad_archive_id": a.AdArchiveID})
		if len(refs) == 3 {
			break
		}
	}
	require.NotEmpty(t, refs)
	timings["source_ads"] = time.Since(started).Seconds()
	finder := call("find_similar_brands", map[string]any{"source_brand": brand, "brief": brief, "reference_ads": refs, "limit": 5})
	var leads struct {
		SearchTerms []string                 `json:"search_terms"`
		Suggestions []helpers.SimilarAdBrand `json:"suggestions"`
	}
	require.NoError(t, json.Unmarshal(finder, &leads))
	require.NotEmpty(t, leads.Suggestions)
	require.NotEmpty(t, leads.SearchTerms)
	timings["finder"] = time.Since(started).Seconds()
	t.Logf("Finder returned %d leads; queries %v", len(leads.Suggestions), leads.SearchTerms)
	queries := leads.SearchTerms[:min(3, len(leads.SearchTerms))]
	var selected []map[string]any
	var candidateAds []models.AdSearchResult
	var candidates scrapeResult
	seen = map[string]bool{}
	for _, mode := range []string{"exact", "broad"} {
		candidates = scrape("search_meta_ads", map[string]any{"queries": queries, "country": "US", "match_mode": mode})
		for _, a := range candidates.Ads {
			if a.PageID == pageIDs[0] || a.BodyText == nil || len(strings.Fields(*a.BodyText)) < 8 || seen[*a.BodyText] {
				continue
			}
			seen[*a.BodyText] = true
			selected = append(selected, map[string]any{"scrape_id": candidates.ID, "ad_archive_id": a.AdArchiveID})
			candidateAds = append(candidateAds, a)
			if len(selected) == 12 {
				break
			}
		}
		t.Logf("%s searches returned %d ads, %d distinct candidates", mode, len(candidates.Ads), len(selected))
		// A page of near-identical variants is too narrow to evaluate discovery.
		if len(selected) >= 3 {
			break
		}
	}
	require.NotEmpty(t, selected)
	timings["candidate_ads"] = time.Since(started).Seconds()
	target := helpers.AdJudgeTarget{Need: os.Getenv("AGF_FLOW_NEED"), Audience: os.Getenv("AGF_FLOW_AUDIENCE")}
	if brand == "Femrun" && target.Need == "" && target.Audience == "" {
		target = helpers.AdJudgeTarget{Need: "Build a manageable exercise habit and improve body shape through short, guided beginner workouts.", Audience: "People starting fitness or returning after inactivity, rather than established athletes adding performance training."}
	}
	require.NoError(t, target.Validate(), "set AGF_FLOW_NEED and AGF_FLOW_AUDIENCE for a custom research target")
	judgements := call("judge_ads", map[string]any{"target": target, "candidate_ads": selected})
	var judged struct {
		Judgements []helpers.AdJudgement `json:"judgements"`
		Partial    bool                  `json:"partial"`
	}
	require.NoError(t, json.Unmarshal(judgements, &judged))
	require.Len(t, judged.Judgements, len(selected))
	require.False(t, judged.Partial)
	counts := map[string]int{}
	for i, v := range judged.Judgements {
		counts[v.Verdict]++
		t.Logf("%s: %s (%s)", candidateAds[i].PageName, v.Verdict, candidateAds[i].AdArchiveID)
	}
	timings["judged"] = time.Since(started).Seconds()
	warmStart := time.Now()
	warm := scrape("get_advertiser_ads", map[string]any{"page_id": pageIDs[0], "country": "US"})
	require.Equal(t, source.ID, warm.ID)
	timings["source_cache_replay"] = time.Since(warmStart).Seconds()
	t.Logf("Complete workflow timings (seconds, cumulative except cache): %v; verdicts: %v", timings, counts)
	if path := os.Getenv("AGF_FLOW_REPORT"); path != "" {
		data, err := json.Marshal(map[string]any{"brand": brand, "brief": brief, "target": target, "source_page_id": pageIDs[0], "source_ads": source.Ads, "reference_ads": refs, "timings": timings, "counts": counts, "finder": finder, "judgements": judgements, "candidate_ads": candidateAds})
		require.NoError(t, err)
		require.NoError(t, os.WriteFile(path, data, 0600))
	}
}
