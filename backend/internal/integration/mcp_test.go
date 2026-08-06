package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
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

	// seedCreative inserts one ad plus a stored creative for advID and returns
	// the external ad id that identifies it in tool output. Cleanup lives here so
	// the FK-safe order is written once: t.Cleanup is LIFO, so registering the ad
	// delete first makes it run last, after the creatives that reference it.
	seedCreative := func(t *testing.T, s3Key string) string {
		ext := fmt.Sprintf("MCPCREATIVE-%d", time.Now().UnixNano())
		var adID int
		require.NoError(t, db.DB().Raw(
			`INSERT INTO public.ads (external_ad_id, advertiser_id, is_active)
			 VALUES (?, ?, true) RETURNING id`, ext, advID).Row().Scan(&adID))
		require.NoError(t, db.DB().Exec(
			`INSERT INTO public.creatives (ad_id, type, s3_key, content_type, file_size, title)
			 VALUES (?, 'image', ?, 'image/jpeg', 1234, 'Test creative')`,
			adID, s3Key).Error)
		t.Cleanup(func() { db.DB().Exec(`DELETE FROM public.ads WHERE id = ?`, adID) })
		t.Cleanup(func() { db.DB().Exec(`DELETE FROM public.creatives WHERE ad_id = ?`, adID) })
		return ext
	}

	// creativeURL calls ad_creatives as the member and returns the download URL
	// of the creative belonging to the given external ad id.
	creativeURL := func(t *testing.T, ext string) string {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "ad_creatives",
			Arguments: map[string]any{"advertiser_id": advID},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Creatives []struct {
				ExternalAdID string `json:"external_ad_id"`
				Type         string `json:"type"`
				ContentType  string `json:"content_type"`
				URL          string `json:"url"`
			} `json:"creatives"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		for _, c := range out.Creatives {
			if c.ExternalAdID == ext {
				require.Equal(t, "image", c.Type)
				require.Equal(t, "image/jpeg", c.ContentType)
				return c.URL
			}
		}
		require.FailNow(t, "the seeded creative is returned", "ext %s missing", ext)
		return ""
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

	t.Run("keyword_yield lists the app pool only", func(t *testing.T) {
		// A keyword's identity is (keyword, kind), so the same phrase can sit in
		// both pools. keyword_control writes to kind='app'; this read must agree
		// with it, or an operator could act on a row the write cannot reach.
		kw := fmt.Sprintf("mcp-kind-kw-%d", time.Now().UnixNano())
		src := fmt.Sprintf("mcp-kind-%d", time.Now().UnixNano())
		require.NoError(t, db.DB().Exec(
			`INSERT INTO discovery.dynamic_keywords (keyword, source, status, kind)
			 VALUES (?, ?, 'active', 'app'), (?, ?, 'active', 'funnel')`,
			kw, src, kw, src).Error)
		defer db.DB().Exec(`DELETE FROM discovery.dynamic_keywords WHERE keyword = ?`, kw)

		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_yield",
			Arguments: map[string]any{"source": src},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Keywords []struct {
				Keyword string `json:"keyword"`
			} `json:"keywords"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Len(t, out.Keywords, 1, "the funnel-pool row must not be listed")
		require.Equal(t, kw, out.Keywords[0].Keyword)
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

	t.Run("keyword_control seeds and retires keywords", func(t *testing.T) {
		kw := fmt.Sprintf("mcp-ctl-kw-%d", time.Now().UnixNano())
		defer db.DB().Exec(`DELETE FROM discovery.dynamic_keywords WHERE keyword = ?`, kw)

		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()

		// seed creates the row as a trial keyword sourced 'mcp'.
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "seed", "keywords": []string{kw}},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var status, source string
		require.NoError(t, db.DB().Raw(
			`SELECT status, source FROM discovery.dynamic_keywords WHERE keyword = ?`, kw,
		).Row().Scan(&status, &source))
		require.Equal(t, "trial", status)
		require.Equal(t, "mcp", source)

		// retire flips it and reports the before/after pair.
		res, err = sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "retire", "keywords": []string{kw}},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Changed []struct {
				Keyword string `json:"keyword"`
				Before  string `json:"before"`
				After   string `json:"after"`
			} `json:"changed"`
			Unknown []string `json:"unknown"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Len(t, out.Changed, 1)
		require.Equal(t, "trial", out.Changed[0].Before)
		require.Equal(t, "retired", out.Changed[0].After)
		require.Empty(t, out.Unknown)

		require.NoError(t, db.DB().Raw(
			`SELECT status FROM discovery.dynamic_keywords WHERE keyword = ?`, kw,
		).Row().Scan(&status))
		require.Equal(t, "retired", status, "the DB row actually moved")
	})

	t.Run("keyword_control rests a keyword and leaves a re-seed alone", func(t *testing.T) {
		kw := fmt.Sprintf("mcp-rest-kw-%d", time.Now().UnixNano())
		defer db.DB().Exec(`DELETE FROM discovery.dynamic_keywords WHERE keyword = ?`, kw)
		require.NoError(t, db.DB().Exec(
			`INSERT INTO discovery.dynamic_keywords (keyword, source, status, kind)
			 VALUES (?, 'mcp-test', 'active', 'app')`, kw).Error)

		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()

		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "rest", "keywords": []string{kw}, "cooldown_days": 3},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		// The cooldown lands as a real interval: a parameterized make_interval is
		// the kind of call that fails on argument type, so assert the stored date.
		var status string
		var restCount int
		var nextEligible time.Time
		require.NoError(t, db.DB().Raw(
			`SELECT status, rest_count, next_eligible_at
			   FROM discovery.dynamic_keywords WHERE keyword = ?`, kw,
		).Row().Scan(&status, &restCount, &nextEligible))
		require.Equal(t, "resting", status)
		require.Equal(t, 1, restCount, "rest increments the cooldown counter")
		require.WithinDuration(t, time.Now().Add(72*time.Hour), nextEligible, 12*time.Hour,
			"cooldown_days 3 sets next_eligible_at three days out")

		// Re-seeding an existing keyword must not overwrite its status.
		res, err = sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "seed", "keywords": []string{kw}},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Changed []struct {
				Before string `json:"before"`
				After  string `json:"after"`
			} `json:"changed"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Len(t, out.Changed, 1)
		require.Equal(t, "resting", out.Changed[0].Before)
		require.Equal(t, "resting", out.Changed[0].After, "a re-seed reports no movement")

		require.NoError(t, db.DB().Raw(
			`SELECT status FROM discovery.dynamic_keywords WHERE keyword = ?`, kw,
		).Row().Scan(&status))
		require.Equal(t, "resting", status, "the row was left alone")
	})

	t.Run("keyword_control reports unknown keywords without failing", func(t *testing.T) {
		missing := fmt.Sprintf("mcp-absent-%d", time.Now().UnixNano())
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "activate", "keywords": []string{missing}},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "an unknown keyword is reported, not an error")

		var out struct {
			Changed []any    `json:"changed"`
			Unknown []string `json:"unknown"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Empty(t, out.Changed)
		require.Equal(t, []string{missing}, out.Unknown)
	})

	t.Run("keyword_control rejects an unknown action", func(t *testing.T) {
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "delete", "keywords": []string{"x"}},
		})
		if err == nil {
			require.True(t, res.IsError, "unknown action must be rejected")
		}
	})

	t.Run("member cannot see or call keyword_control", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		require.False(t, toolNames(sess)["keyword_control"], "write tool must not be listed")
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "retire", "keywords": []string{"x"}},
		})
		if err == nil {
			require.True(t, res.IsError, "member keyword_control must not succeed")
		}
	})

	t.Run("hunt_term_yield tallies triage per source term", func(t *testing.T) {
		hEmail := fmt.Sprintf("mcp-hunt-%d@test.local", time.Now().UnixNano())
		require.NoError(t, repositories.UpsertUser(ctx, db.DB(), hEmail, "x", "Hunt", "admin"))
		huntID, err := repositories.CreateHunt(ctx, db.DB(), hEmail, "mcp term yield")
		require.NoError(t, err)
		defer db.DB().Exec(`DELETE FROM frontend.hunts WHERE id = ?`, huntID)

		roundID, err := repositories.InsertHuntRound(ctx, db.DB(), huntID, nil,
			json.RawMessage(`["sleep tracker"]`), nil, nil, nil)
		require.NoError(t, err)
		// InsertHuntRound also enqueues a pipeline job, and pipeline_jobs has no
		// FK to the round, so the hunt delete above does not reach it.
		defer db.DB().Exec(`DELETE FROM public.pipeline_jobs WHERE kind = 'hunt' AND advertiser_id = ?`, roundID)

		require.NoError(t, db.DB().Exec(
			`INSERT INTO frontend.hunt_results
			   (round_id, page_id, page_name, store, store_id, source_term, was_new, triage, relevance)
			 VALUES (?, 'p1', 'One', 'ios', 's1', 'sleep tracker', true, 'kept',   'relevant'),
			        (?, 'p2', 'Two', 'ios', 's2', 'sleep tracker', true, 'vetoed', 'off_target')`,
			roundID, roundID).Error)

		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "hunt_term_yield",
			Arguments: map[string]any{"round_id": roundID},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Terms []struct {
				SourceTerm string `json:"source_term"`
				Found      int    `json:"found"`
				Kept       int    `json:"kept"`
				Relevant   int    `json:"relevant"`
				OffTarget  int    `json:"off_target"`
			} `json:"terms"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Len(t, out.Terms, 1)
		require.Equal(t, "sleep tracker", out.Terms[0].SourceTerm)
		require.Equal(t, 2, out.Terms[0].Found)
		require.Equal(t, 1, out.Terms[0].Kept)
		require.Equal(t, 1, out.Terms[0].Relevant)
		require.Equal(t, 1, out.Terms[0].OffTarget)
	})

	// Regression guard for the unscoped read. Omitting round_id makes the
	// repository pass a nil *int, which gorm sends as an untyped NULL — a bare
	// "? IS NULL" predicate is rejected by Postgres with 42P18 and the whole
	// tool errors. The scoped subtest above never exercises that path, so
	// without this one the casts in HuntTermYield could be dropped and the
	// suite would still pass. Terms are per-run-unique and asserted by lookup
	// rather than by position, so other rows on the shared DB cannot break it.
	t.Run("hunt_term_yield without a round id spans every round", func(t *testing.T) {
		n := time.Now().UnixNano()
		termA := fmt.Sprintf("mcp-all-rounds-%d", n)
		termB := fmt.Sprintf("page:mcp-all-rounds-%d", n)

		hEmail := fmt.Sprintf("mcp-hunt-all-%d@test.local", n)
		require.NoError(t, repositories.UpsertUser(ctx, db.DB(), hEmail, "x", "HuntAll", "admin"))
		huntID, err := repositories.CreateHunt(ctx, db.DB(), hEmail, "mcp all rounds")
		require.NoError(t, err)
		// hunts -> hunt_rounds -> hunt_results are both ON DELETE CASCADE, so this
		// one delete removes every hunt row seeded below. The pipeline jobs that
		// InsertHuntRound enqueues are NOT covered — they key off the round id
		// without an FK — so they are removed separately once the ids are known.
		defer db.DB().Exec(`DELETE FROM frontend.hunts WHERE id = ?`, huntID)

		// Two rounds under one hunt. termA appears in BOTH, so an aggregate that
		// silently scoped to a single round would report rounds=1, not 2.
		round1, err := repositories.InsertHuntRound(ctx, db.DB(), huntID, nil,
			json.RawMessage(`["a"]`), nil, nil, nil)
		require.NoError(t, err)
		round2, err := repositories.InsertHuntRound(ctx, db.DB(), huntID, nil,
			json.RawMessage(`["a"]`), nil, nil, nil)
		require.NoError(t, err)
		defer db.DB().Exec(
			`DELETE FROM public.pipeline_jobs WHERE kind = 'hunt' AND advertiser_id IN (?, ?)`,
			round1, round2)

		require.NoError(t, db.DB().Exec(
			`INSERT INTO frontend.hunt_results
			   (round_id, page_id, page_name, store, store_id, source_term, was_new, triage, relevance)
			 VALUES (?, ?, 'A', 'ios', ?, ?, true, 'kept',   'relevant'),
			        (?, ?, 'B', 'ios', ?, ?, true, 'kept',   NULL),
			        (?, ?, 'C', 'ios', ?, ?, true, 'vetoed', 'off_target')`,
			round1, fmt.Sprintf("ar1-%d", n), fmt.Sprintf("as1-%d", n), termA,
			round2, fmt.Sprintf("ar2-%d", n), fmt.Sprintf("as2-%d", n), termA,
			round2, fmt.Sprintf("ar3-%d", n), fmt.Sprintf("as3-%d", n), termB).Error)

		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		// No round_id key at all: this is the "which terms have ever worked" read.
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "hunt_term_yield",
			Arguments: map[string]any{"limit": 200},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Terms []struct {
				SourceTerm string `json:"source_term"`
				Rounds     int    `json:"rounds"`
				Found      int    `json:"found"`
				Kept       int    `json:"kept"`
				Relevant   int    `json:"relevant"`
				OffTarget  int    `json:"off_target"`
			} `json:"terms"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))

		byTerm := map[string]int{}
		for i, tr := range out.Terms {
			byTerm[tr.SourceTerm] = i
		}
		ia, ok := byTerm[termA]
		require.True(t, ok, "the term seeded into both rounds must appear")
		require.Equal(t, 2, out.Terms[ia].Rounds, "the tally spans both rounds")
		require.Equal(t, 2, out.Terms[ia].Found)
		require.Equal(t, 2, out.Terms[ia].Kept)
		// One of the two rows has a NULL relevance, so relevant+off_target < found.
		require.Equal(t, 1, out.Terms[ia].Relevant)
		require.Equal(t, 0, out.Terms[ia].OffTarget)

		ib, ok := byTerm[termB]
		require.True(t, ok, "a competitor page pull from the second round must appear")
		require.Equal(t, 1, out.Terms[ib].Rounds)
		require.Equal(t, 1, out.Terms[ib].OffTarget)
	})

	t.Run("users_overview includes the seeded member with its rating count", func(t *testing.T) {
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "users_overview",
			Arguments: map[string]any{"limit": 200},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Users []struct {
				Email   string `json:"email"`
				Role    string `json:"role"`
				Ratings int    `json:"ratings"`
			} `json:"users"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		found := false
		for _, u := range out.Users {
			if u.Email == memberEmail {
				found = true
				require.Equal(t, "member", u.Role)
				require.GreaterOrEqual(t, u.Ratings, 1, "the seeded like is counted")
			}
		}
		require.True(t, found, "the seeded member appears in the overview")
	})

	t.Run("ad_creatives returns absolute media urls for an advertiser", func(t *testing.T) {
		got := creativeURL(t, seedCreative(t, "creatives/mcp-test.jpg"))

		// The URL crosses a process boundary: an MCP client on another machine
		// curls it. A root-relative path it cannot resolve is a broken download,
		// so the origin has to be there with nothing configured.
		u, err := url.Parse(got)
		require.NoError(t, err)
		require.NotEmpty(t, u.Scheme, "download url must be absolute: %s", got)
		require.NotEmpty(t, u.Host, "download url must carry a host: %s", got)
		require.True(t, strings.HasSuffix(got, "/api/media/creatives/mcp-test.jpg"),
			"url is the media route plus the s3 key: %s", got)
	})

	t.Run("ad_creatives prefers the configured public api url", func(t *testing.T) {
		// A deployment behind a proxy that rewrites Host can state its own
		// address; the override wins over the request-derived origin, and its
		// trailing slash is trimmed rather than doubled into the path.
		t.Setenv("AGF_PUBLIC_API_URL", "https://api.example.test/")

		got := creativeURL(t, seedCreative(t, "creatives/mcp-override.jpg"))
		require.Equal(t, "https://api.example.test/api/media/creatives/mcp-override.jpg", got)
	})

	t.Run("member cannot see the admin history tools", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		names := toolNames(sess)
		require.False(t, names["hunt_term_yield"])
		require.False(t, names["users_overview"])
	})

	// /mcp is rate-limited per bearer token (PerKeyRateLimit(60)) and the
	// subtests above have spent most of adminKey's burst. The hunt rounds open
	// their own sessions, so they get their own key and their own budget.
	_, huntAdminKey := seedKeyedUser(t, ctx, "admin")

	t.Run("run_hunt enqueues a competitor pull with custom settings", func(t *testing.T) {
		sess, err := connect(huntAdminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name: "run_hunt",
			Arguments: map[string]any{
				"competitors": []string{"Quizlooker"},
				"intent":      "pull one advertiser's ads",
				"settings":    map[string]any{"listing_pages": 5},
			},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			HuntID  int `json:"hunt_id"`
			RoundID int `json:"round_id"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Greater(t, out.RoundID, 0)
		// The hunt cascades to its rounds and results, but pipeline_jobs has no FK
		// to a round (its advertiser_id carries the round id by convention), so the
		// queued job outlives the round unless it is removed explicitly. Both
		// deletes live in one closure so their order is fixed rather than LIFO.
		defer func() {
			db.DB().Exec(`DELETE FROM public.pipeline_jobs WHERE kind = 'hunt' AND advertiser_id = ?`, out.RoundID)
			db.DB().Exec(`DELETE FROM frontend.hunts WHERE id = ?`, out.HuntID)
		}()

		// The round carries the competitor and the settings verbatim.
		var competitors, settings string
		require.NoError(t, db.DB().Raw(
			`SELECT competitor_apps::text, settings::text FROM frontend.hunt_rounds WHERE id = ?`,
			out.RoundID).Row().Scan(&competitors, &settings))
		require.Contains(t, competitors, "Quizlooker")
		require.Contains(t, settings, "listing_pages")
		// Only the knob the caller set may reach the worker. An unset knob must
		// be absent, not a zero — clamp_hunt_settings would raise a zero to the
		// bound's minimum and silently cripple the round.
		require.NotContains(t, settings, "page_cap")
		require.NotContains(t, settings, "patience")
		require.NotContains(t, settings, "min_pages")

		// Exactly one queued hunt job for this round.
		var jobs int64
		require.NoError(t, db.DB().Raw(
			`SELECT count(*) FROM public.pipeline_jobs WHERE kind = 'hunt' AND advertiser_id = ?`,
			out.RoundID).Row().Scan(&jobs))
		require.EqualValues(t, 1, jobs)

		// hunt_status reads it back as queued.
		res, err = sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "hunt_status",
			Arguments: map[string]any{"round_id": out.RoundID},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))
		var st struct {
			Status string `json:"status"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &st))
		require.Equal(t, "queued", st.Status)
	})

	t.Run("run_hunt rejects a call with no terms and no competitors", func(t *testing.T) {
		sess, err := connect(huntAdminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "run_hunt",
			Arguments: map[string]any{"intent": "nothing to search"},
		})
		if err == nil {
			require.True(t, res.IsError, "an empty hunt must be rejected")
		}
	})

	t.Run("member cannot see or call run_hunt", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		require.False(t, toolNames(sess)["run_hunt"])
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "run_hunt",
			Arguments: map[string]any{"competitors": []string{"x"}},
		})
		if err == nil {
			require.True(t, res.IsError, "member run_hunt must not succeed")
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
