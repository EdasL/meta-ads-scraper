// Package routers wires the /api route group and /healthz onto the engine.
package routers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/controllers"
	"github.com/edaslakavicius/emerging-app-finder/backend/storage"
)

// Init registers every route. GETs and the single POST live in separate gin
// method-trees, so /review/queue (GET) and /review/:advertiser_id/decision
// (POST) never conflict.
func Init(r *gin.Engine) {
	shortlist := controllers.NewShortlistController()
	gems := controllers.NewGemsController()
	review := controllers.NewReviewController()
	health := controllers.NewHealthController()
	media := controllers.NewMediaController(storage.Client(), storage.Bucket())

	api := r.Group("/api")
	{
		api.GET("/shortlist", shortlist.List)
		api.GET("/gems/:advertiser_id", gems.Get)
		api.GET("/gems/:advertiser_id/scaling", gems.Scaling)
		api.GET("/review/queue", review.Queue)
		api.POST("/review/:advertiser_id/decision", review.Decision)
		api.GET("/health/metrics", health.Metrics)
		api.GET("/media/*key", media.Get)
	}

	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})
}
