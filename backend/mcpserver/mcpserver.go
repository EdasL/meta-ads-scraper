// Package mcpserver is the hosted Field Desk MCP: a streamable-HTTP MCP server
// that exposes a member's own Field Desk data (ratings, saved gems, taste
// profile, recommendations, the gem directory) plus, for admins, the discovery
// engine's health.
//
// Every request is authenticated upstream by middlewares.KeyAuth (a personal
// fd_ API key). The authenticated user is read from the request context ONCE,
// at server construction, and captured by the tool handlers as a closure — no
// tool accepts a user parameter and no tool trusts client-supplied identity.
// The tool set is built per request and keyed on role, so a member's
// tools/list never even lists the admin-only scraping_health tool.
package mcpserver

import (
	"context"
	"errors"
	"net/http"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// Handler returns the /mcp HTTP handler. It builds a fresh MCP server per
// request from the authenticated user; KeyAuth has already rejected anonymous
// requests before this runs, so a missing user is a defensive nil (the SDK
// then declines the session).
func Handler() http.Handler {
	return mcp.NewStreamableHTTPHandler(func(req *http.Request) *mcp.Server {
		u, ok := middlewares.UserFrom(req.Context())
		if !ok {
			return nil
		}
		return buildServer(u)
	}, nil)
}

// buildServer registers the tools this user is allowed to see. The member tools
// are always present; scraping_health is registered only for admins, which is
// what keeps it out of a member's tools/list (there is no per-tool "hidden"
// flag in the SDK — omission is the boundary).
func buildServer(u middlewares.AuthedUser) *mcp.Server {
	s := mcp.NewServer(&mcp.Implementation{Name: "fielddesk", Version: "1.0.0"}, nil)
	h := &handlers{u: u}

	mcp.AddTool(s, &mcp.Tool{Name: "my_taste_profile",
		Description: "Your rating counts, top liked cohorts, and the signals that drive your likes."},
		h.tasteProfile)
	mcp.AddTool(s, &mcp.Tool{Name: "my_ratings",
		Description: "Your app ratings. Filter by rating (like/neutral/dislike)."},
		h.myRatings)
	mcp.AddTool(s, &mcp.Tool{Name: "my_saved_gems",
		Description: "Your saved watchlist with stage, verdict, tags, and notes."},
		h.mySavedGems)
	mcp.AddTool(s, &mcp.Tool{Name: "recommended_gems",
		Description: "The gem feed ranked by similarity to the apps you liked."},
		h.recommended)
	mcp.AddTool(s, &mcp.Tool{Name: "search_gems",
		Description: "Search the gem directory by name, cohort, GemScore, and store."},
		h.searchGems)
	mcp.AddTool(s, &mcp.Tool{Name: "gem_detail",
		Description: "One gem: scores, axes, ads summary, funnel, store links."},
		h.gemDetail)

	if u.Role == "admin" {
		mcp.AddTool(s, &mcp.Tool{Name: "scraping_health",
			Description: "Discovery engine health: pulse, discovery rate, idea diversity."},
			h.scrapingHealth)
	}
	return s
}

// handlers carries the authenticated user for the lifetime of one request's
// server. Every tool reads identity from h.u, never from tool input.
type handlers struct {
	u middlewares.AuthedUser
}

// clampLimit normalizes a member-supplied page size: non-positive or absurdly
// large requests fall back to the default 50.
func clampLimit(n int) int {
	if n <= 0 || n > 200 {
		return 50
	}
	return n
}

// ---- my_taste_profile ----

type tasteProfileIn struct{}

func (h *handlers) tasteProfile(ctx context.Context, _ *mcp.CallToolRequest, _ tasteProfileIn) (*mcp.CallToolResult, any, error) {
	tp, err := repositories.MyTasteProfile(ctx, db.DB(), h.u.Email)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading your taste profile.")
	}
	if tp == nil || len(tp.Counts) == 0 {
		return nil, map[string]any{"note": "No ratings yet — rate apps in the review queue first."}, nil
	}
	return nil, tp, nil
}

// ---- my_ratings ----

type myRatingsIn struct {
	Rating string `json:"rating,omitempty" jsonschema:"like, neutral, or dislike; empty for all"`
	Limit  int    `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset int    `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) myRatings(ctx context.Context, _ *mcp.CallToolRequest, in myRatingsIn) (*mcp.CallToolResult, any, error) {
	rows, total, err := repositories.MyRatings(ctx, db.DB(), h.u.Email, in.Rating, clampLimit(in.Limit), in.Offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading your ratings.")
	}
	return nil, map[string]any{"total": total, "ratings": rows}, nil
}

// ---- my_saved_gems ----

type mySavedGemsIn struct{}

func (h *handlers) mySavedGems(ctx context.Context, _ *mcp.CallToolRequest, _ mySavedGemsIn) (*mcp.CallToolResult, any, error) {
	rows, err := repositories.MySavedGems(ctx, db.DB(), h.u.Email)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading your saved gems.")
	}
	return nil, map[string]any{"saved_gems": rows}, nil
}

// ---- recommended_gems ----

type recommendedIn struct {
	Limit int `json:"limit,omitempty" jsonschema:"max gems to return (default 50, max 200)"`
}

func (h *handlers) recommended(ctx context.Context, _ *mcp.CallToolRequest, in recommendedIn) (*mcp.CallToolResult, any, error) {
	gems, personalized, err := repositories.RecommendedGems(ctx, db.DB(), h.u.Email, clampLimit(in.Limit))
	if err != nil {
		return nil, nil, errors.New("something went wrong building your recommendations.")
	}
	out := map[string]any{"personalized": personalized, "gems": gems}
	if !personalized {
		out["note"] = "No likes yet — showing GemScore order."
	}
	return nil, out, nil
}

// ---- search_gems ----

type searchGemsIn struct {
	Q         string  `json:"q,omitempty" jsonschema:"case-insensitive substring of app or page name"`
	Cohort    string  `json:"cohort,omitempty" jsonschema:"exact cohort key"`
	Category  string  `json:"category,omitempty" jsonschema:"store genre/category"`
	MinScore  float64 `json:"min_score,omitempty" jsonschema:"minimum GemScore"`
	HasFunnel bool    `json:"has_funnel,omitempty" jsonschema:"only gems with a confirmed funnel"`
	Sort      string  `json:"sort,omitempty" jsonschema:"sort key (e.g. gem_score, scaling_velocity)"`
	Dir       string  `json:"dir,omitempty" jsonschema:"asc or desc"`
	Limit     int     `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset    int     `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) searchGems(ctx context.Context, _ *mcp.CallToolRequest, in searchGemsIn) (*mcp.CallToolResult, any, error) {
	p := repositories.ShortlistParams{
		Q:         in.Q,
		MinScore:  in.MinScore,
		HasFunnel: in.HasFunnel,
		Sort:      in.Sort,
		Dir:       in.Dir,
		Limit:     clampLimit(in.Limit),
		Offset:    in.Offset,
	}
	if in.Cohort != "" {
		p.Cohort = &in.Cohort
	}
	if in.Category != "" {
		p.Category = &in.Category
	}
	rows, total, err := repositories.Shortlist(ctx, db.DB(), p)
	if err != nil {
		return nil, nil, errors.New("something went wrong searching the gem directory.")
	}
	return nil, map[string]any{"total": total, "gems": rows}, nil
}

// ---- gem_detail ----

type gemDetailIn struct {
	AdvertiserID int `json:"advertiser_id" jsonschema:"the gem's advertiser id"`
}

func (h *handlers) gemDetail(ctx context.Context, _ *mcp.CallToolRequest, in gemDetailIn) (*mcp.CallToolResult, any, error) {
	// Dossier reads the matview (frontend.emerging_scaling_apps); it MUST stay on
	// that read so non-representative advertiser ids resolve to their feed rep
	// (regression noted 2026-06-27 — the products view 404s them).
	d, err := repositories.Dossier(ctx, db.DB(), in.AdvertiserID)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading that gem.")
	}
	if d == nil {
		return nil, nil, errors.New("gem not found.")
	}
	return nil, d, nil
}

// ---- scraping_health (admin only) ----

type scrapingHealthIn struct {
	Days int `json:"days,omitempty" jsonschema:"discovery-rate window in days (default 14)"`
}

func (h *handlers) scrapingHealth(ctx context.Context, _ *mcp.CallToolRequest, in scrapingHealthIn) (*mcp.CallToolResult, any, error) {
	// Defense in depth: this tool is only registered on an admin's server, but
	// re-check here so a non-admin can never reach the engine internals even if
	// registration ever changes.
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	days := in.Days
	if days <= 0 {
		days = 14
	}
	g := db.DB()
	pulse, err := repositories.EnginePulse(ctx, g)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading engine health.")
	}
	rate, err := repositories.DiscoveryRate(ctx, g, days)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading engine health.")
	}
	diversity, err := repositories.IdeaDiversity(ctx, g)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading engine health.")
	}
	return nil, map[string]any{
		"pulse":          pulse,
		"discovery_rate": rate,
		"idea_diversity": diversity,
	}, nil
}
