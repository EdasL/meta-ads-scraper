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

// maxShortlistLimit caps a page so an oversized ?limit= can't dump the whole
// matview with full per-row enrichment. Larger values clamp, they don't error.
const maxShortlistLimit = 500

// filterParams parses the filter axes shared by /shortlist and its categories
// endpoint. A present-but-empty string param means "no filter", exactly like
// an absent one (truncated share URLs must not silently empty the feed). On a
// bad value it writes the 422 and returns ok=false.
func filterParams(ctx *gin.Context) (p repositories.ShortlistParams, ok bool) {
	p.Q = strings.TrimSpace(ctx.Query("q"))
	if v, k := ctx.GetQuery("cohort"); k && v != "" {
		p.Cohort = &v
	}
	if v, k := ctx.GetQuery("category"); k && v != "" {
		p.Category = &v
	}
	var err error
	if p.MinScore, err = helpers.QueryFloat(ctx, "min_score", 0.0); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad min_score.")
		return p, false
	}
	if p.Rising, err = helpers.QueryBool(ctx, "rising", false); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad rising.")
		return p, false
	}
	if p.HasFunnel, err = helpers.QueryBool(ctx, "has_funnel", false); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad has_funnel.")
		return p, false
	}
	if p.MinAdAge, err = helpers.QueryInt(ctx, "min_ad_age", 0); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad min_ad_age.")
		return p, false
	}
	// incubating is tri-state: absent → confirmed feed (the default); present →
	// parsed as bool so the Fresh view can request incubating=true explicitly.
	if _, k := ctx.GetQuery("incubating"); k {
		v, err := helpers.QueryBool(ctx, "incubating", false)
		if err != nil {
			helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad incubating.")
			return p, false
		}
		p.Incubating = &v
	}
	return p, true
}

func (c *ShortlistController) List(ctx *gin.Context) {
	p, ok := filterParams(ctx)
	if !ok {
		return
	}
	if p.Sort = ctx.Query("sort"); p.Sort == "" {
		p.Sort = "gemscore"
	}
	if !repositories.ValidSort(p.Sort) {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad sort.")
		return
	}
	if p.Dir = ctx.Query("dir"); !repositories.ValidDir(p.Dir) {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad dir.")
		return
	}
	var err error
	if p.Limit, err = helpers.QueryInt(ctx, "limit", 50); err != nil || p.Limit < 0 {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad limit.")
		return
	}
	if p.Limit > maxShortlistLimit {
		p.Limit = maxShortlistLimit
	}
	if p.Offset, err = helpers.QueryInt(ctx, "offset", 0); err != nil || p.Offset < 0 {
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
	// Categories take the same filter axes as the list so each count answers
	// "what would the feed hold if I picked this category?".
	p, ok := filterParams(ctx)
	if !ok {
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	cats, err := repositories.ShortlistCategories(rctx, db.DB(), p)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.JSON(http.StatusOK, cats)
}
