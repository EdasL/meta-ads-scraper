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
