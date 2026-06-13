package controllers

import (
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// HealthController serves GET /api/health/metrics.
type HealthController struct{}

func NewHealthController() *HealthController { return &HealthController{} }

func (c *HealthController) Metrics(ctx *gin.Context) {
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	m, err := repositories.Metrics(rctx, db.DB(), time.Now())
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "metrics broke.")
		return
	}
	ctx.JSON(http.StatusOK, m)
}

// Coverage serves GET /api/health/coverage — the feed-health signal (confirmed
// feed size, incubating cohort, funnel coverage) that surfaces a plateau or
// funnel-capture regression without hand-querying the DB.
func (c *HealthController) Coverage(ctx *gin.Context) {
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	m, err := repositories.Coverage(rctx, db.DB(), time.Now())
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "coverage broke.")
		return
	}
	ctx.JSON(http.StatusOK, m)
}
