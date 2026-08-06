// Package routers wires the /api route group and /healthz onto the engine.
package routers

import (
	"context"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/controllers"
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/mcpserver"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/monitoring"
)

// Init registers every route. GETs and the single POST live in separate gin
// method-trees, so /review/queue (GET) and /review/:advertiser_id/decision
// (POST) never conflict.
func Init(r *gin.Engine) {
	shortlist := controllers.NewShortlistController()
	gems := controllers.NewGemsController()
	review := controllers.NewReviewController()
	enrich := controllers.NewEnrichController()
	saved := controllers.NewSavedGemsController()
	savedAds := controllers.NewSavedAdsController()
	savedGroups := controllers.NewSavedGroupsController()
	likedGems := controllers.NewLikedGemsController()
	ratings := controllers.NewRatingsController()
	preference := controllers.NewPreferenceController()
	health := controllers.NewHealthController()
	media := controllers.NewMediaController()
	users := controllers.NewUsersController()
	hunt := controllers.NewHuntController(helpers.NewGeminiHelper())
	addGem := controllers.NewAddGemController()

	// Every /api route requires auth: a personal fd_ key OR the static internal
	// token with a resolvable X-User-Email (KeyAuth(true)). This closes the
	// previously-open GETs. Writes keep their per-route RequireToken() as a
	// second gate that FAILS CLOSED (401 when AGF_API_TOKEN is unset) and only
	// accepts the internal token — so a personal fd_ key clears the group but can
	// never mutate: REST writes stay internal-token-only. The /api/internal group
	// is a SEPARATE group off the engine (RequireInternalToken only) and does not
	// inherit this middleware.
	api := r.Group("/api", middlewares.KeyAuth(true))
	{
		api.GET("/shortlist", shortlist.List)
		api.GET("/shortlist/categories", shortlist.Categories)
		api.GET("/gems/:advertiser_id", gems.Get)
		api.GET("/gems/:advertiser_id/scaling", gems.Scaling)
		api.GET("/gems/:advertiser_id/app-metrics", gems.AppMetrics)
		api.GET("/review/queue", review.Queue)
		api.GET("/preference-queue", preference.Queue)
		// Writes are guarded by the internal bearer token (RequireToken fails
		// closed: 401 unless AGF_API_TOKEN is set), so mutations are never open.
		api.POST("/review/:advertiser_id/decision", middlewares.RequireToken(), review.Decision)
		// On-demand enrich: POST triggers a refresh (token-guarded like review),
		// GET polls status (open, status reads are safe to expose).
		api.POST("/enrich/:advertiser_id", middlewares.RequireToken(), enrich.Trigger)
		api.GET("/enrich/:advertiser_id", enrich.Status)
		// On-demand gem add from a pasted Ad Library link. Same auth shape as
		// enrich: the POST is a write, the status poll is a read. The paths are
		// /add-gem rather than /gems/add because gin rejects a literal segment
		// beside the existing /gems/:advertiser_id wildcard.
		api.POST("/add-gem", middlewares.RequireToken(), addGem.Add)
		api.GET("/add-gem/:job_id", addGem.Status)
		// Saved gems (a user's watchlist). Writes use POST (CORS allows GET/POST
		// only) and are token-guarded like the review decision.
		api.GET("/saved-gems", saved.List)
		api.POST("/saved-gems/:advertiser_id", middlewares.RequireToken(), saved.Save)
		api.POST("/saved-gems/:advertiser_id/remove", middlewares.RequireToken(), saved.Remove)
		// Saved ads (the ad-level sibling of saved gems). Same auth shape.
		api.GET("/saved-ads", savedAds.List)
		// Every saved creative as one zip. Static path, so it can't collide with
		// the :external_ad_id writes below (different method trees anyway).
		api.GET("/saved-ads/download", savedAds.Download)
		api.POST("/saved-ads/:external_ad_id", middlewares.RequireToken(), savedAds.Save)
		api.POST("/saved-ads/:external_ad_id/remove", middlewares.RequireToken(), savedAds.Remove)
		// Folders for /saved. Identity comes from KeyAuth (never a param);
		// an unknown or foreign :id is a 404 so existence stays hidden.
		api.GET("/saved-groups", savedGroups.List)
		api.POST("/saved-groups", middlewares.RequireToken(), savedGroups.Create)
		api.POST("/saved-groups/:id/update", middlewares.RequireToken(), savedGroups.Update)
		api.POST("/saved-groups/:id/remove", middlewares.RequireToken(), savedGroups.Remove)
		// Folder membership (playlist semantics: a gem can be in many folders).
		api.POST("/saved-groups/:id/gems", middlewares.RequireToken(), savedGroups.AddGem)
		api.POST("/saved-groups/:id/gems/remove", middlewares.RequireToken(), savedGroups.RemoveGem)
		// The built-in Liked collection: everything the caller rated 'like',
		// saved or not. Read-only — membership is the rating itself.
		api.GET("/liked-gems", likedGems.List)
		// Per-user app ratings (like/neutral/dislike + comment). Writes are
		// token-guarded like saved gems and review decisions.
		api.GET("/ratings", ratings.List)
		api.POST("/ratings/:advertiser_id", middlewares.RequireToken(), ratings.Save)
		// The caller's own account (identity from KeyAuth, never a query param).
		api.GET("/me", users.Me)
		api.POST("/me/onboarded", middlewares.RequireToken(), users.MarkOnboarded)
		// On-demand AI-assisted discovery (Hunt). Reads are open to the owner;
		// the chat turns and round-start are token-guarded writes. Every :hunt_id
		// route 404s on an ownership miss (never 403 — existence stays hidden).
		api.GET("/hunts", hunt.List)
		api.POST("/hunts", middlewares.RequireToken(), hunt.Create)
		api.GET("/hunts/:hunt_id", hunt.Get)
		api.DELETE("/hunts/:hunt_id", middlewares.RequireToken(), hunt.Delete)
		api.POST("/hunts/:hunt_id/messages", middlewares.RequireToken(), hunt.Message)
		api.POST("/hunts/:hunt_id/rounds", middlewares.RequireToken(), hunt.StartRound)
		api.GET("/health/metrics", health.Metrics)
		api.GET("/health/coverage", health.Coverage)
		api.GET("/media/*key", media.Get)
	}

	// Internal auth + key management, for the Next.js server only. Guarded by
	// the static internal token and FAILS CLOSED (RequireInternalToken): if
	// AGF_API_TOKEN is unset every request is 401, so a misconfigured deploy
	// cannot expose these key-minting endpoints. Never exposed to browsers.
	ia := controllers.NewInternalAuthController()
	internal := r.Group("/api/internal", middlewares.RequireInternalToken())
	internal.POST("/login", ia.Login)
	internal.POST("/oauth-login", ia.OAuthLogin)
	internal.GET("/keys", ia.ListKeys)
	internal.POST("/keys", ia.MintKey)
	internal.POST("/keys/revoke", ia.RevokeKey)
	// Hunt round-completion hook: the worker VM's hunt daemon reports a
	// finished round; Peon writes the wrap-up message. Internal token only.
	internal.POST("/hunts/rounds/:round_id/completed", hunt.RoundCompleted)

	// Hosted multi-user MCP. Personal fd_ key auth (KeyAuth(false) — the internal
	// static token is NOT accepted here), then a per-key rate limit. The limiter
	// is mounted ONLY on /mcp, where every caller holds a personal key; it must
	// not guard the /api tree. The tool set is built per request from the
	// authenticated user, so admins get scraping_health and members do not.
	mcpHandler := mcpserver.Handler()
	r.Any("/mcp", middlewares.KeyAuth(false), middlewares.PerKeyRateLimit(60), gin.WrapH(mcpHandler))
	r.Any("/mcp/*path", middlewares.KeyAuth(false), middlewares.PerKeyRateLimit(60), gin.WrapH(mcpHandler))

	// Prometheus exposition.
	r.GET("/metrics", monitoring.MetricsHandler())

	// Liveness: the process is up (no dependencies checked).
	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})

	// Readiness: the process can reach its database. Used by orchestrators to
	// gate traffic; returns 503 when the DB is unreachable.
	r.GET("/readyz", func(c *gin.Context) {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
		defer cancel()
		if err := db.Ready(ctx); err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"status": "not ready"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})
}
