package integration

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/routers"
	"github.com/gin-gonic/gin"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Reads previously observed real ad snapshots, then exercises the HTTP MCP,
// ownership-scoped DB reads and real model providers. No fabricated ad copy.
func TestAdResearchModelsLive(t *testing.T) {
	if os.Getenv("AGF_AD_RESEARCH_LIVE") != "1" {
		t.Skip("set AGF_AD_RESEARCH_LIVE=1 to judge observed ads with real providers")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	var fixture struct {
		Target      helpers.AdJudgeTarget   `json:"target"`
		Brief       string                  `json:"brief"`
		SourceBrand string                  `json:"source_brand"`
		References  []models.AdSearchResult `json:"references"`
		Candidates  []models.AdSearchResult `json:"candidates"`
		Expected    map[string]string       `json:"expected"`
	}
	fixturePath := os.Getenv("AGF_AD_RESEARCH_FIXTURE")
	if fixturePath == "" {
		fixturePath = "testdata/ad_research_fitness.json"
	}
	data, err := os.ReadFile(fixturePath)
	require.NoError(t, err)
	require.NoError(t, json.Unmarshal(data, &fixture))
	require.NotEmpty(t, fixture.References)
	require.NotEmpty(t, fixture.Candidates)
	owner, key := seedKeyedUser(t, ctx, "admin")
	var scrapeID int64
	require.NoError(t, db.DB().Raw(`INSERT INTO frontend.ad_searches(created_by,status) VALUES (?,'done') RETURNING id`, owner).Scan(&scrapeID).Error)
	t.Cleanup(func() {
		require.NoError(t, db.DB().Exec(`DELETE FROM frontend.ad_searches WHERE id=?`, scrapeID).Error)
	})
	insert := func(ads []models.AdSearchResult) []map[string]any {
		selected := []map[string]any{}
		for _, a := range ads {
			require.NoError(t, db.DB().Exec(`INSERT INTO frontend.ad_search_results(search_id,ad_archive_id,page_id,page_name,source_term,country,is_active,creative_hash,body_text)
			 VALUES (?,?,?,?,'live-provider-evaluation','US',true,?,?) ON CONFLICT(search_id,ad_archive_id) DO NOTHING`, scrapeID, a.AdArchiveID, a.PageID, a.PageName, a.CreativeHash, a.BodyText).Error)
			selected = append(selected, map[string]any{"scrape_id": scrapeID, "ad_archive_id": a.AdArchiveID})
		}
		return selected
	}
	refs, candidates := insert(fixture.References), insert(fixture.Candidates)
	r := gin.New()
	routers.Init(r)
	server := httptest.NewServer(r)
	defer server.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "research-live", Version: "1"}, nil)
	session, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: server.URL + "/mcp", HTTPClient: mcpClientFor(key)}, nil)
	require.NoError(t, err)
	defer session.Close()
	call := func(name string, args map[string]any) *mcp.CallToolResult {
		t.Helper()
		result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: args})
		require.NoError(t, err)
		require.False(t, result.IsError, firstText(result))
		return result
	}
	start := time.Now()
	result := call("judge_ads", map[string]any{"target": fixture.Target, "candidate_ads": candidates})
	var judged struct {
		Judgements []helpers.AdJudgement `json:"judgements"`
		Partial    bool                  `json:"partial"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(result)), &judged))
	require.Len(t, judged.Judgements, len(candidates))
	require.False(t, judged.Partial)
	t.Logf("Jev: %d real ads in %s", len(candidates), time.Since(start))
	for i, v := range judged.Judgements {
		t.Logf("%s (%s): %s, %v", fixture.Candidates[i].AdArchiveID, fixture.Candidates[i].PageName, v.Verdict, v.Signals)
		if expected := fixture.Expected[fixture.Candidates[i].AdArchiveID]; expected != "" {
			// This is an advisory-tool gate, not an accuracy benchmark: positive
			// abstentions require agent review; confident contradictions fail.
			if expected == "match" {
				assert.Contains(t, []string{"match", "uncertain"}, v.Verdict)
				if v.Verdict == "uncertain" {
					t.Logf("positive abstention: %s needs agent review", fixture.Candidates[i].AdArchiveID)
				}
			} else if expected == "not_match" || expected == "mismatch" {
				// A weak or conflicting peer may remain uncertain, but must not
				// be promoted to a confident match.
				assert.NotEqual(t, "match", v.Verdict)
				if v.Verdict == "uncertain" {
					t.Logf("negative abstention: %s; exact accuracy is measured by TestAdJudgeQualityLive", fixture.Candidates[i].AdArchiveID)
				}
			} else {
				assert.Equal(t, expected, v.Verdict)
			}
		}
	}
	start = time.Now()
	finder := call("find_similar_brands", map[string]any{"brief": fixture.Brief, "source_brand": fixture.SourceBrand, "reference_ads": refs, "limit": 5})
	var found struct {
		Suggestions []helpers.SimilarAdBrand `json:"suggestions"`
		Verified    bool                     `json:"active_ads_verified"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(finder)), &found))
	require.NotEmpty(t, found.Suggestions)
	require.False(t, found.Verified)
	for _, b := range found.Suggestions {
		require.False(t, b.Verified)
		require.NotEqual(t, fixture.SourceBrand, b.Name)
	}
	t.Logf("Brand finder: %d unverified leads in %s", len(found.Suggestions), time.Since(start))
	var scrapeCount, jobCount int64
	require.NoError(t, db.DB().Raw(`SELECT count(*) FROM frontend.ad_searches WHERE created_by=?`, owner).Scan(&scrapeCount).Error)
	require.NoError(t, db.DB().Raw(`SELECT count(*) FROM public.pipeline_jobs WHERE kind='ad_search' AND advertiser_id=?`, scrapeID).Scan(&jobCount).Error)
	require.EqualValues(t, 1, scrapeCount, "assistance must not start another scrape")
	require.Zero(t, jobCount, "assistance must not enqueue scraping")
	if path := os.Getenv("AGF_AD_RESEARCH_REPORT"); path != "" {
		data, err := json.Marshal(map[string]any{"judge": json.RawMessage(firstText(result)), "finder": json.RawMessage(firstText(finder))})
		require.NoError(t, err)
		require.NoError(t, os.WriteFile(path, data, 0600))
	}
}
