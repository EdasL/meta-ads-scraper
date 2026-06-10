// Package routers wires the /api route group and /healthz onto the engine.
package routers

import (
	"context"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/controllers"
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/monitoring"
	"github.com/edaslakavicius/emerging-app-finder/backend/storage"
)

// Init registers every route. GETs and the single POST live in separate gin
// method-trees, so /review/queue (GET) and /review/:advertiser_id/decision
// (POST) never conflict.
func Init(r *gin.Engine) {
	shortlist := controllers.NewShortlistController()
	gems := controllers.NewGemsController()
	review := controllers.NewReviewController()
	saved := controllers.NewSavedGemsController()
	health := controllers.NewHealthController()
	media := controllers.NewMediaController(storage.Client(), storage.Bucket())

	api := r.Group("/api")
	{
		api.GET("/shortlist", shortlist.List)
		api.GET("/gems/:advertiser_id", gems.Get)
		api.GET("/gems/:advertiser_id/scaling", gems.Scaling)
		api.GET("/gems/:advertiser_id/app-metrics", gems.AppMetrics)
		api.GET("/review/queue", review.Queue)
		// The single write is guarded by an optional bearer token (no-op unless
		// AGF_API_TOKEN is set), so production can require auth on mutations.
		api.POST("/review/:advertiser_id/decision", middlewares.RequireToken(), review.Decision)
		// Saved gems (a user's watchlist). Writes use POST (CORS allows GET/POST
		// only) and are token-guarded like the review decision.
		api.GET("/saved-gems", saved.List)
		api.POST("/saved-gems/:advertiser_id", middlewares.RequireToken(), saved.Save)
		api.POST("/saved-gems/:advertiser_id/remove", middlewares.RequireToken(), saved.Remove)
		api.GET("/health/metrics", health.Metrics)
		api.GET("/media/*key", media.Get)
	}

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
