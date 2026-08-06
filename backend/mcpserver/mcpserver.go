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
	"net/http"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
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
		mcp.AddTool(s, &mcp.Tool{Name: "keyword_yield",
			Description: "Sweep keywords ranked by how many candidates survived triage. Shows status, sweeps used, candidates, keepers, and dry streak."},
			h.keywordYield)
		mcp.AddTool(s, &mcp.Tool{Name: "keyword_control",
			Description: "Change sweep keyword lifecycle: activate, rest, retire, or seed new trial keywords. Applies on the next flywheel tick."},
			h.keywordControl)
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
