// Package controllers holds the gin HTTP handlers, one file per route group,
// mirroring src/agf/api/routers/*.
package controllers

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// ShortlistController serves GET /api/shortlist and /api/shortlist/categories.
type ShortlistController struct{}

func NewShortlistController() *ShortlistController { return &ShortlistController{} }

func (c *ShortlistController) List(ctx *gin.Context) {
	p := repositories.ShortlistParams{Q: strings.TrimSpace(ctx.Query("q"))}
	if v, ok := ctx.GetQuery("cohort"); ok {
		p.Cohort = &v
	}
	if v, ok := ctx.GetQuery("category"); ok {
		p.Category = &v
	}
	var err error
	if p.MinScore, err = helpers.QueryFloat(ctx, "min_score", 0.0); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad min_score.")
		return
	}
	if p.Rising, err = helpers.QueryBool(ctx, "rising", false); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad rising.")
		return
	}
	if p.HasFunnel, err = helpers.QueryBool(ctx, "has_funnel", false); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad has_funnel.")
		return
	}
	if p.MinAdAge, err = helpers.QueryInt(ctx, "min_ad_age", 0); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad min_ad_age.")
		return
	}
	p.Sort = ctx.DefaultQuery("sort", "gemscore")
	if !repositories.ValidSort(p.Sort) {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad sort.")
		return
	}
	if p.Limit, err = helpers.QueryInt(ctx, "limit", 50); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad limit.")
		return
	}
	if p.Offset, err = helpers.QueryInt(ctx, "offset", 0); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad offset.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, total, err := repositories.Shortlist(rctx, db.DB(), p)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	// The body stays a bare ShortlistRow[] (frozen contract); the all-pages
	// total rides a header so the client can paginate.
	ctx.Header("X-Total-Count", strconv.Itoa(total))
	ctx.JSON(http.StatusOK, rows)
}

func (c *ShortlistController) Categories(ctx *gin.Context) {
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	cats, err := repositories.ShortlistCategories(rctx, db.DB())
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.JSON(http.StatusOK, cats)
}
