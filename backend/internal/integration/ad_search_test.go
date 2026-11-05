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
	"github.com/edaslakavicius/emerging-app-finder/backend/routers"
	"github.com/gin-gonic/gin"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/stretchr/testify/require"
)

func TestAdSearchMCP(t *testing.T) {
	ctx := context.Background()
	gin.SetMode(gin.TestMode)
	r := gin.New()
	routers.Init(r)
	server := httptest.NewServer(r)
	t.Cleanup(server.Close)
	connect := func(role string) (string, *mcp.ClientSession) {
		email, key := seedKeyedUser(t, ctx, role)
		client := mcp.NewClient(&mcp.Implementation{Name: "ad-search-it", Version: "1"}, nil)
		session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: server.URL + "/mcp", HTTPClient: mcpClientFor(key)}, nil)
		require.NoError(t, err)
		t.Cleanup(func() { session.Close() })
		return email, session
	}
	owner, admin := connect("admin")
	_, foreign := connect("admin")
	_, member := connect("member")
	t.Cleanup(func() {
		require.NoError(t, db.DB().Exec(`DELETE FROM public.pipeline_jobs WHERE kind='ad_search' AND advertiser_id IN (SELECT id FROM frontend.ad_searches WHERE created_by=?)`, owner).Error)
		require.NoError(t, db.DB().Exec(`DELETE FROM frontend.ad_searches WHERE created_by=?`, owner).Error)
	})
	for _, tc := range []struct {
		s       *mcp.ClientSession
		allowed bool
	}{{admin, true}, {member, false}} {
		list, err := tc.s.ListTools(ctx, nil)
		require.NoError(t, err)
		names := map[string]bool{}
		for _, tool := range list.Tools {
			names[tool.Name] = true
		}
		require.Equal(t, tc.allowed, names["find_advertisers"])
		require.Equal(t, tc.allowed, names["ad_scrape_status"])
		require.Equal(t, tc.allowed, names["find_similar_brands"])
		require.Equal(t, tc.allowed, names["judge_ads"])
	}
	call := func(s *mcp.ClientSession, name string, args map[string]any) *mcp.CallToolResult {
		res, err := s.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: args})
		require.NoError(t, err)
		return res
	}
	args := map[string]any{"queries": []string{"Example"}, "idempotency_key": "retry"}
	res := call(admin, "find_advertisers", args)
	require.False(t, res.IsError, firstText(res))
	var created struct {
		SearchID int64 `json:"scrape_id"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(res)), &created))
	require.Positive(t, created.SearchID)
	retry := call(admin, "find_advertisers", args)
	require.False(t, retry.IsError, firstText(retry))
	var again struct {
		SearchID int64 `json:"scrape_id"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(retry)), &again))
	require.Equal(t, created.SearchID, again.SearchID)
	require.True(t, call(admin, "find_advertisers", map[string]any{"queries": []string{"Different"}, "idempotency_key": "retry"}).IsError)
	status := call(admin, "ad_scrape_status", map[string]any{"scrape_id": created.SearchID})
	require.False(t, status.IsError, firstText(status))
	var output struct {
		Status      string `json:"status"`
		Provisional bool   `json:"provisional"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(status)), &output))
	require.Equal(t, "queued", output.Status)
	require.True(t, output.Provisional)
	require.True(t, call(foreign, "ad_scrape_status", map[string]any{"scrape_id": created.SearchID}).IsError)
	guide := call(admin, "ad_research_guide", map[string]any{})
	require.False(t, guide.IsError)
	require.Contains(t, firstText(guide), "calling agent")
	require.Contains(t, firstText(guide), "Brands without ads")
	page := call(admin, "search_meta_ads", map[string]any{"queries": []string{"walking"}})
	require.False(t, page.IsError, firstText(page))
	var scrape struct {
		ID int64 `json:"scrape_id"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(page)), &scrape))
	require.NoError(t, db.DB().Exec(`UPDATE frontend.ad_searches SET status='done',progress=?::jsonb WHERE id=?`, `{"sources":{"query:walking":{"has_more":true,"cursor":"opaque-provider-cursor","query":"walking"}}}`, scrape.ID).Error)
	require.NoError(t, db.DB().Exec(`INSERT INTO frontend.ad_search_results(search_id,ad_archive_id,page_id,page_name,source_term,country,is_active,creative_hash,body_text) VALUES (?, '12','123','Example','query:walking','US',true,'hash',?)`, scrape.ID, strings.Repeat("a", 7000)).Error)
	raw := call(admin, "ad_scrape_status", map[string]any{"scrape_id": scrape.ID})
	require.NotContains(t, firstText(raw), "opaque-provider-cursor")
	require.Contains(t, firstText(raw), `"copy_truncated":true`)
	require.Contains(t, firstText(raw), `"interpretation":"unclassified"`)
	require.NotContains(t, firstText(raw), `"relevance_score"`)
	copy := call(admin, "read_ad_copy", map[string]any{"scrape_id": scrape.ID, "ad_archive_id": "12", "offset": 6000})
	require.False(t, copy.IsError, firstText(copy))
	var tail struct {
		Text string `json:"text"`
		More bool   `json:"has_more"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(copy)), &tail))
	require.Len(t, tail.Text, 1000)
	require.False(t, tail.More)
	require.True(t, call(foreign, "read_ad_copy", map[string]any{"scrape_id": scrape.ID, "ad_archive_id": "12"}).IsError)
	selected := []map[string]any{{"scrape_id": scrape.ID, "ad_archive_id": "12"}}
	require.True(t, call(foreign, "judge_ads", map[string]any{"brief": "gentle fitness", "candidate_ads": selected}).IsError)
	require.True(t, call(foreign, "find_similar_brands", map[string]any{"brief": "gentle fitness", "reference_ads": selected}).IsError)
	t.Setenv("TYPESAFE_API_KEY", "")
	t.Setenv("GEMINI_API_KEY", "")
	judge := call(admin, "judge_ads", map[string]any{"brief": "gentle fitness", "candidate_ads": selected})
	require.True(t, judge.IsError)
	require.Contains(t, firstText(judge), "TYPESAFE_API_KEY")
	finder := call(admin, "find_similar_brands", map[string]any{"brief": "gentle fitness"})
	require.True(t, finder.IsError)
	require.Contains(t, firstText(finder), "GEMINI_API_KEY")
	require.True(t, call(foreign, "next_ad_page", map[string]any{"scrape_id": scrape.ID}).IsError)
	next := call(admin, "next_ad_page", map[string]any{"scrape_id": scrape.ID})
	require.False(t, next.IsError, firstText(next))
	var continuation struct {
		ID int64 `json:"scrape_id"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(next)), &continuation))
	replayed := call(admin, "next_ad_page", map[string]any{"scrape_id": scrape.ID})
	require.False(t, replayed.IsError, firstText(replayed))
	var replay struct {
		ID int64 `json:"scrape_id"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(replayed)), &replay))
	require.Equal(t, continuation.ID, replay.ID)
	_, err := member.CallTool(ctx, &mcp.CallToolParams{Name: "find_advertisers", Arguments: args})
	require.Error(t, err)
}

// Opt-in real provider gate. Requires the disposable integration database,
// Python dependencies and proxies. Model keys are explicitly cleared.
func TestAdSearchLiveWorker(t *testing.T) {
	if os.Getenv("AGF_AD_SEARCH_LIVE") != "1" {
		t.Skip("set AGF_AD_SEARCH_LIVE=1 to call Meta")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Minute)
	defer cancel()
	owner, key := seedKeyedUser(t, ctx, "admin")
	r := gin.New()
	routers.Init(r)
	server := httptest.NewServer(r)
	defer server.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "ad-search-live", Version: "1"}, nil)
	session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: server.URL + "/mcp", HTTPClient: mcpClientFor(key)}, nil)
	require.NoError(t, err)
	defer session.Close()
	t.Cleanup(func() {
		require.NoError(t, db.DB().Exec(`DELETE FROM public.pipeline_jobs WHERE kind='ad_search' AND advertiser_id IN (SELECT id FROM frontend.ad_searches WHERE created_by=?)`, owner).Error)
		require.NoError(t, db.DB().Exec(`DELETE FROM frontend.ad_searches WHERE created_by=?`, owner).Error)
	})
	t.Setenv("GEMINI_API_KEY", "")
	t.Setenv("TYPESAFE_API_KEY", "")
	tool := os.Getenv("AGF_AD_SCRAPE_TOOL")
	if tool == "" {
		tool = "get_advertiser_ads"
	}
	args := map[string]any{"page_id": "851229608079602", "country": "US"}
	if raw := os.Getenv("AGF_AD_SEARCH_REQUEST"); raw != "" {
		args = map[string]any{}
		require.NoError(t, json.Unmarshal([]byte(raw), &args))
	}
	created, err := session.CallTool(ctx, &mcp.CallToolParams{Name: tool, Arguments: args})
	require.NoError(t, err)
	require.False(t, created.IsError, firstText(created))
	var search struct {
		SearchID int64 `json:"scrape_id"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(created)), &search))
	require.Positive(t, search.SearchID)
	require.NoError(t, db.DB().Exec(`UPDATE public.pipeline_jobs SET run_after='1970-01-01' WHERE kind='ad_search' AND advertiser_id=?`, search.SearchID).Error)
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	require.NoError(t, err)
	cmd := exec.CommandContext(ctx, filepath.Join(root, ".venv/bin/python"), "-c", `
import sys
from agf.db.engine import get_engine
from agf.discovery.ad_search_daemon import process_once, run_subprocess
target=int(sys.argv[1])
def runner(search_id, job_id):
    assert search_id == target, 'another job claimed; live gate did not reach target'
    run_subprocess(search_id, job_id)
assert process_once(get_engine(), runner=runner)
`, strconv.FormatInt(search.SearchID, 10))
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Join(root, "src"), "AGF_DATABASE_URL="+os.Getenv("AGF_IT_DB_URL"))
	worker := make(chan error, 1)
	go func() { worker <- cmd.Run() }()
	partialObserved := false
	for {
		res, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "ad_scrape_status", Arguments: map[string]any{"scrape_id": search.SearchID, "limit": 50}})
		require.NoError(t, err)
		require.False(t, res.IsError, firstText(res))
		var output struct {
			Status      string                     `json:"status"`
			Provisional bool                       `json:"provisional"`
			Results     []json.RawMessage          `json:"ads"`
			Suggestions []json.RawMessage          `json:"suggestions"`
			Sources     map[string]json.RawMessage `json:"sources"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &output))
		partialObserved = partialObserved || (output.Provisional && len(output.Results)+len(output.Suggestions) > 0)
		if !output.Provisional {
			require.NoError(t, <-worker)
			require.Contains(t, []string{"done", "needs_reference"}, output.Status, firstText(res))
			if tool == "find_advertisers" {
				require.NotEmpty(t, output.Sources)
				require.Contains(t, firstText(res), `"page_name"`)
			} else {
				require.NotZero(t, len(output.Results), "real ads must reach MCP status")
			}
			var jobStatus string
			require.NoError(t, db.DB().Raw(`SELECT status FROM public.pipeline_jobs WHERE kind='ad_search' AND advertiser_id=?`, search.SearchID).Scan(&jobStatus).Error)
			require.Equal(t, "done", jobStatus)
			if path := os.Getenv("AGF_AD_SEARCH_REPORT"); path != "" {
				require.NoError(t, os.WriteFile(path, []byte(firstText(res)), 0600))
			}
			t.Logf("live search %d: status=%s incremental=%v results=%d suggestions=%d", search.SearchID, output.Status, partialObserved, len(output.Results), len(output.Suggestions))
			warmStart := time.Now()
			retry, err := session.CallTool(ctx, &mcp.CallToolParams{Name: tool, Arguments: args})
			require.NoError(t, err)
			require.False(t, retry.IsError, firstText(retry))
			var warm struct {
				ScrapeID int64  `json:"scrape_id"`
				Status   string `json:"status"`
			}
			require.NoError(t, json.Unmarshal([]byte(firstText(retry)), &warm))
			require.Equal(t, search.SearchID, warm.ScrapeID)
			require.Equal(t, "done", warm.Status)
			t.Logf("identical request returned completed cached evidence in %s", time.Since(warmStart))
			if os.Getenv("AGF_AD_SCRAPE_NEXT") == "1" {
				next, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "next_ad_page", Arguments: map[string]any{"scrape_id": search.SearchID}})
				require.NoError(t, err)
				require.False(t, next.IsError, firstText(next))
				var continuation struct {
					ID int64 `json:"scrape_id"`
				}
				require.NoError(t, json.Unmarshal([]byte(firstText(next)), &continuation))
				require.NoError(t, db.DB().Exec(`UPDATE public.pipeline_jobs SET run_after='1970-01-01' WHERE kind='ad_search' AND advertiser_id=?`, continuation.ID).Error)
				args := append([]string{}, cmd.Args[1:]...)
				args[len(args)-1] = strconv.FormatInt(continuation.ID, 10)
				continuationWorker := exec.CommandContext(ctx, cmd.Path, args...)
				continuationWorker.Dir = cmd.Dir
				continuationWorker.Env = cmd.Env
				require.NoError(t, continuationWorker.Run())
				page, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "ad_scrape_status", Arguments: map[string]any{"scrape_id": continuation.ID}})
				require.NoError(t, err)
				require.False(t, page.IsError, firstText(page))
				var result struct {
					Status string            `json:"status"`
					Ads    []json.RawMessage `json:"ads"`
				}
				require.NoError(t, json.Unmarshal([]byte(firstText(page)), &result))
				require.Equal(t, "done", result.Status, firstText(page))
				require.NotEmpty(t, result.Ads)
				t.Logf("provider continuation returned %d raw ads", len(result.Ads))
			}
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("live search deadline exceeded")
		case <-time.After(time.Second):
		}
	}
}
