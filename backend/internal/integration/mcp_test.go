package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/edaslakavicius/emerging-app-finder/backend/routers"
)

// bearerTransport injects a static Authorization header on every MCP HTTP
// request, standing in for the personal fd_ key a real client would carry.
type bearerTransport struct {
	token string
	base  http.RoundTripper
}

func (b bearerTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if b.token != "" {
		r.Header.Set("Authorization", "Bearer "+b.token)
	}
	return b.base.RoundTrip(r)
}

func mcpClientFor(token string) *http.Client {
	return &http.Client{Transport: bearerTransport{token: token, base: http.DefaultTransport}}
}

// seedKeyedUser creates a user with the given role and mints one API key,
// returning the plaintext key. Emails are per-run-unique so the shared test DB
// never collides across runs or with sibling tests.
func seedKeyedUser(t *testing.T, ctx context.Context, role string) (email, key string) {
	t.Helper()
	email = fmt.Sprintf("mcp-%s-%d@test.local", role, time.Now().UnixNano())
	if err := repositories.UpsertUser(ctx, db.DB(), email, "x", "MCP "+role, role); err != nil {
		t.Fatal(err)
	}
	key, err := repositories.CreateAPIKey(ctx, db.DB(), email, "mcp-test")
	if err != nil {
		t.Fatal(err)
	}
	return email, key
}

// TestMCPServer drives the hosted MCP end to end over an httptest server: role
// gates the tool set, personal reads are scoped to the caller, admin-only tools
// reject members, and anonymous requests are rejected before the MCP handshake.
func TestMCPServer(t *testing.T) {
	ctx := context.Background()

	memberEmail, memberKey := seedKeyedUser(t, ctx, "member")
	_, adminKey := seedKeyedUser(t, ctx, "admin")

	// A like by THIS member on a seeded matview advertiser (advID → Acme App).
	// A foreign member's rating on the same advertiser must never leak into the
	// caller's my_ratings.
	otherEmail := fmt.Sprintf("mcp-other-%d@test.local", time.Now().UnixNano())
	require.NoError(t, repositories.UpsertUser(ctx, db.DB(), otherEmail, "x", "Other", "member"))
	require.NoError(t, db.DB().Exec(`INSERT INTO frontend.app_ratings
		(user_email, advertiser_id, rating, comment, rated_at) VALUES
		(?, ?, 'like', 'mine', now()),
		(?, ?, 'like', 'theirs', now())`,
		memberEmail, advID, otherEmail, advID).Error)

	// scraping_health reads discovery.candidates (max first_seen_at). Seed one
	// candidate + a sweep run under a unique prefix so the engine-health read has
	// data on the shared DB, then clean up. Mirrors TestScrapeHealth.
	prefix := fmt.Sprintf("MCPHEALTH-%d-", time.Now().UnixNano())
	require.NoError(t, db.DB().Exec(
		`INSERT INTO discovery.candidates (page_id, store, store_id, source_keyword, status)
		 VALUES (?, 'ios', ?, 'mcp test', 'new')`, prefix+"1", prefix+"1").Error)
	require.NoError(t, db.DB().Exec(`INSERT INTO discovery.sweep_runs DEFAULT VALUES`).Error)
	defer db.DB().Exec(`DELETE FROM discovery.candidates WHERE page_id LIKE ?`, prefix+"%")

	gin.SetMode(gin.TestMode)
	r := gin.New()
	// routers.Init mounts /mcp with KeyAuth(false)+PerKeyRateLimit(60).
	routers.Init(r)
	ts := httptest.NewServer(r)
	defer ts.Close()

	connect := func(token string) (*mcp.ClientSession, error) {
		cli := mcp.NewClient(&mcp.Implementation{Name: "it", Version: "0"}, nil)
		tr := &mcp.StreamableClientTransport{
			Endpoint:   ts.URL + "/mcp",
			HTTPClient: mcpClientFor(token),
		}
		return cli.Connect(ctx, tr, nil)
	}

	toolNames := func(sess *mcp.ClientSession) map[string]bool {
		res, err := sess.ListTools(ctx, nil)
		require.NoError(t, err)
		names := map[string]bool{}
		for _, tl := range res.Tools {
			names[tl.Name] = true
		}
		return names
	}

	t.Run("member tools/list excludes scraping_health", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		names := toolNames(sess)
		require.True(t, names["my_ratings"], "member has member tools")
		require.True(t, names["gem_detail"])
		require.False(t, names["scraping_health"], "member must not see the admin tool")
	})

	t.Run("admin tools/list includes scraping_health", func(t *testing.T) {
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		names := toolNames(sess)
		require.True(t, names["scraping_health"], "admin sees the engine-health tool")
		require.True(t, names["my_ratings"], "admin also has the member tools")
	})

	t.Run("my_ratings returns only the caller's rating", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{Name: "my_ratings"})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Total   int64 `json:"total"`
			Ratings []struct {
				AdvertiserID int64  `json:"advertiser_id"`
				Comment      string `json:"comment"`
			} `json:"ratings"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.EqualValues(t, 1, out.Total, "only the caller's one like")
		require.Len(t, out.Ratings, 1)
		require.EqualValues(t, advID, out.Ratings[0].AdvertiserID)
		require.Equal(t, "mine", out.Ratings[0].Comment, "never the other member's rating")
	})

	t.Run("member calling scraping_health is rejected", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{Name: "scraping_health"})
		// The tool is not registered on a member's server, so the SDK returns an
		// error result — the member can never reach the engine internals.
		if err == nil {
			require.True(t, res.IsError, "member scraping_health must not succeed")
		}
	})

	t.Run("admin scraping_health succeeds", func(t *testing.T) {
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{Name: "scraping_health"})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))
		var out map[string]any
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Contains(t, out, "pulse")
		require.Contains(t, out, "discovery_rate")
		require.Contains(t, out, "idea_diversity")
	})

	t.Run("keyword_yield reports keeper counts per keyword", func(t *testing.T) {
		kw := fmt.Sprintf("mcp-yield-kw-%d", time.Now().UnixNano())
		pfx := fmt.Sprintf("MCPYIELD-%d-", time.Now().UnixNano())
		// Both timestamps are seeded non-NULL so the assertions below catch a
		// column-name mismatch: gorm derives "next_eligible" from the field name,
		// which silently scans the value away as null unless the model pins the
		// column explicitly.
		require.NoError(t, db.DB().Exec(
			`INSERT INTO discovery.dynamic_keywords
			   (keyword, source, status, sweeps_used, new_candidates_total, last_swept_at, next_eligible_at)
			 VALUES (?, 'mcp-test', 'active', 3, 2, now(), now() + interval '1 day')`, kw).Error)
		require.NoError(t, db.DB().Exec(
			`INSERT INTO discovery.candidates (page_id, store, store_id, source_keyword, status)
			 VALUES (?, 'ios', ?, ?, 'kept'), (?, 'ios', ?, ?, 'dropped')`,
			pfx+"1", pfx+"1", kw, pfx+"2", pfx+"2", kw).Error)
		defer db.DB().Exec(`DELETE FROM discovery.candidates WHERE page_id LIKE ?`, pfx+"%")
		defer db.DB().Exec(`DELETE FROM discovery.dynamic_keywords WHERE keyword = ?`, kw)

		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_yield",
			Arguments: map[string]any{"source": "mcp-test"},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Keywords []struct {
				Keyword      string     `json:"keyword"`
				Status       string     `json:"status"`
				SweepsUsed   int        `json:"sweeps_used"`
				Candidates   int        `json:"candidates"`
				Keepers      int        `json:"keepers"`
				LastSweptAt  *time.Time `json:"last_swept_at"`
				NextEligible *time.Time `json:"next_eligible_at"`
			} `json:"keywords"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Len(t, out.Keywords, 1, "source filter isolates the seeded keyword")
		require.Equal(t, kw, out.Keywords[0].Keyword)
		require.Equal(t, "active", out.Keywords[0].Status)
		require.Equal(t, 3, out.Keywords[0].SweepsUsed)
		require.Equal(t, 2, out.Keywords[0].Candidates, "both candidates counted")
		require.Equal(t, 1, out.Keywords[0].Keepers, "only the kept one")
		require.NotNil(t, out.Keywords[0].LastSweptAt, "last_swept_at survives the scan")
		require.False(t, out.Keywords[0].LastSweptAt.IsZero())
		require.NotNil(t, out.Keywords[0].NextEligible, "next_eligible_at survives the scan")
		require.False(t, out.Keywords[0].NextEligible.IsZero())

		// min_sweeps is a floor: the seeded keyword has sweeps_used = 3, so a
		// floor of 5 must exclude it.
		res, err = sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_yield",
			Arguments: map[string]any{"source": "mcp-test", "min_sweeps": 5},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))
		out.Keywords = nil
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Empty(t, out.Keywords, "min_sweeps 5 excludes a keyword swept 3 times")
	})

	t.Run("member cannot see or call keyword_yield", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		require.False(t, toolNames(sess)["keyword_yield"], "admin tool must not be listed")
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{Name: "keyword_yield"})
		if err == nil {
			require.True(t, res.IsError, "member keyword_yield must not succeed")
		}
	})

	t.Run("no bearer is rejected before handshake", func(t *testing.T) {
		_, err := connect("")
		require.Error(t, err, "anonymous connect must fail at the HTTP 401")
	})

	t.Run("bogus bearer is rejected before handshake", func(t *testing.T) {
		_, err := connect("fd_not_a_real_key")
		require.Error(t, err, "unknown key must fail at the HTTP 401")
	})
}

// firstText returns the text of a tool result's first content block. Handlers
// return structured output, which the SDK also serializes as JSON text content.
func firstText(res *mcp.CallToolResult) string {
	if res == nil || len(res.Content) == 0 {
		return ""
	}
	if tc, ok := res.Content[0].(*mcp.TextContent); ok {
		return tc.Text
	}
	return ""
}
