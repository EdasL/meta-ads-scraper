// Package controllers holds the gin HTTP handlers, one file per route group,
// mirroring src/agf/api/routers/*.
package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// ShortlistController serves GET /api/shortlist.
type ShortlistController struct{}

func NewShortlistController() *ShortlistController { return &ShortlistController{} }

func (c *ShortlistController) List(ctx *gin.Context) {
	var cohort *string
	if v, ok := ctx.GetQuery("cohort"); ok {
		cohort = &v
	}
	minScore, err := helpers.QueryFloat(ctx, "min_score", 0.0)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad min_score.")
		return
	}
	limit, err := helpers.QueryInt(ctx, "limit", 50)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad limit.")
		return
	}
	offset, err := helpers.QueryInt(ctx, "offset", 0)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad offset.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.Shortlist(rctx, db.DB(), cohort, minScore, limit, offset)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "grug board broke.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}
