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
	"os"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
)

// publicAPIURLEnv optionally pins the origin that download URLs are built
// against. It is an override, not a requirement — see publicOrigin.
const publicAPIURLEnv = "AGF_PUBLIC_API_URL"

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
		return buildServer(u, publicOrigin(req))
	}, nil)
}

// publicOrigin is the absolute origin an MCP client should use to reach this
// API. Tool results travel to a client in another process on another machine,
// so a download URL has to carry scheme and host; a root-relative path is a
// broken download there.
//
// The origin is read off the request, which means it is right with nothing
// configured and cannot drift from how the deployment is actually addressed.
// AGF_PUBLIC_API_URL overrides it for the case the request cannot describe: a
// proxy that rewrites Host, or an origin that differs from the one dialed.
// X-Forwarded-Proto carries the original scheme through the GKE ingress, which
// terminates TLS and forwards plaintext; it may list several hops, and the
// first is the client-facing one.
func publicOrigin(req *http.Request) string {
	if v := strings.TrimSuffix(os.Getenv(publicAPIURLEnv), "/"); v != "" {
		return v
	}
	scheme := "http"
	if fwd := req.Header.Get("X-Forwarded-Proto"); fwd != "" {
		scheme, _, _ = strings.Cut(fwd, ",")
		scheme = strings.TrimSpace(scheme)
	} else if req.TLS != nil {
		scheme = "https"
	}
	return scheme + "://" + req.Host
}

// buildServer registers the tools this user is allowed to see. The member tools
// are always present; scraping_health is registered only for admins, which is
// what keeps it out of a member's tools/list (there is no per-tool "hidden"
// flag in the SDK — omission is the boundary).
func buildServer(u middlewares.AuthedUser, base string) *mcp.Server {
	s := mcp.NewServer(&mcp.Implementation{Name: "fielddesk", Version: "1.0.0"}, &mcp.ServerOptions{Instructions: "For Meta ad research, read ad_research_guide first. Scraping tools return unclassified evidence; the calling agent chooses references, judges relevance, and controls pagination."})
	h := &handlers{u: u, base: base}

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
	mcp.AddTool(s, &mcp.Tool{Name: "ad_creatives",
		Description: "An advertiser's ad creatives with download URLs, ad copy, and link targets."},
		h.adCreatives)

	if u.Role == "admin" {
		mcp.AddTool(s, &mcp.Tool{Name: "read_ad_copy", Description: "Read stored full ad copy in chunks when copy_truncated=true. No network scrape or model call; preserve the actual offer when judging long advertorials."}, h.readAdCopy)
		mcp.AddTool(s, &mcp.Tool{Name: "ad_research_guide", Description: "Read first: workflow and relevance rules for agent-led Meta ad research, including brands without ads."}, h.researchGuide)
		mcp.AddTool(s, &mcp.Tool{Name: "find_advertisers", Description: "Resolve up to three names to Meta page candidates. The agent selects the right page; no automatic brand choice. Poll ad_scrape_status if queued."}, h.findAdvertisers)
		mcp.AddTool(s, &mcp.Tool{Name: "get_advertiser_ads", Description: "Fetch one page of active Meta ads for a numeric page ID. Returns raw copy, media, links and timestamps. No model filtering. Poll ad_scrape_status if queued."}, h.getAdvertiserAds)
		mcp.AddTool(s, &mcp.Tool{Name: "search_meta_ads", Description: "Search one Meta page for each of up to three phrases concurrently. Exact phrases by default; broad is optional. No relevance judgements. Poll ad_scrape_status if queued."}, h.searchMetaAds)
		mcp.AddTool(s, &mcp.Tool{Name: "ad_scrape_status", Description: "Read raw ads and per-source progress, errors and pagination. Results arrive incrementally. Offset paginates stored ads; next_ad_page fetches more from Meta."}, h.adScrapeStatus)
		mcp.AddTool(s, &mcp.Tool{Name: "next_ad_page", Description: "Fetch one more Meta page for a completed scrape source with has_more=true. Repeated calls are idempotent. Judge a shallow sample before choosing depth."}, h.nextAdPage)
		mcp.AddTool(s, &mcp.Tool{Name: "scraping_health",
			Description: "Discovery engine health: pulse, discovery rate, idea diversity."},
			h.scrapingHealth)
		mcp.AddTool(s, &mcp.Tool{Name: "keyword_yield",
			Description: "Sweep keywords ranked by how many candidates survived triage. Shows status, sweeps used, candidates, keepers, and dry streak."},
			h.keywordYield)
		mcp.AddTool(s, &mcp.Tool{Name: "keyword_control",
			Description: "Change sweep keyword lifecycle: activate, rest, retire, or seed new trial keywords. Applies on the next flywheel tick."},
			h.keywordControl)
		mcp.AddTool(s, &mcp.Tool{Name: "hunt_term_yield",
			Description: "Hunt search terms tallied by outcome: found, kept, relevant, off target. Competitor pulls appear as page:<name>."},
			h.huntTermYield)
		mcp.AddTool(s, &mcp.Tool{Name: "users_overview",
			Description: "Accounts with rating counts, saved-gem counts, provider, and onboarding date."},
			h.usersOverview)
		mcp.AddTool(s, &mcp.Tool{Name: "run_hunt",
			Description: "Queue an on-demand Ad Library run. Terms are US keyword searches; competitors are resolved to their ad page and pulled globally. Optional per-round depth settings."},
			h.runHunt)
		mcp.AddTool(s, &mcp.Tool{Name: "hunt_status",
			Description: "One hunt round: status, live progress, and results with triage and relevance."},
			h.huntStatus)
	}
	return s
}

// handlers carries the authenticated user for the lifetime of one request's
// server. Every tool reads identity from h.u, never from tool input.
// base is that request's public origin, used to build absolute media URLs.
type handlers struct {
	u    middlewares.AuthedUser
	base string
}

// clampLimit normalizes a member-supplied page size: non-positive or absurdly
// large requests fall back to the default 50.
func clampLimit(n int) int {
	if n <= 0 || n > 200 {
		return 50
	}
	return n
}
