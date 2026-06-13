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

// FunnelsController serves GET /api/funnels — confirmed web-to-app funnels
// grouped by landing domain. Mirrors ShortlistController's shape.
type FunnelsController struct{}

func NewFunnelsController() *FunnelsController { return &FunnelsController{} }

// maxFunnelsLimit caps a page so an oversized ?limit= can't dump every domain
// at once. Larger values clamp, they don't error.
const maxFunnelsLimit = 500

func (c *FunnelsController) List(ctx *gin.Context) {
	var p repositories.FunnelsParams
	p.Q = strings.TrimSpace(ctx.Query("q"))
	var err error
	if p.HasQuiz, err = helpers.QueryBool(ctx, "has_quiz", false); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad has_quiz.")
		return
	}
	if p.Sort = ctx.Query("sort"); p.Sort == "" {
		p.Sort = "newest"
	}
	if !repositories.FunnelsValidSort(p.Sort) {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad sort.")
		return
	}
	if p.Dir = ctx.Query("dir"); !repositories.ValidDir(p.Dir) {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad dir.")
		return
	}
	if p.Limit, err = helpers.QueryInt(ctx, "limit", 50); err != nil || p.Limit < 0 {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad limit.")
		return
	}
	if p.Limit > maxFunnelsLimit {
		p.Limit = maxFunnelsLimit
	}
	if p.Offset, err = helpers.QueryInt(ctx, "offset", 0); err != nil || p.Offset < 0 {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad offset.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, total, err := repositories.Funnels(rctx, db.DB(), p)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.Header("X-Total-Count", strconv.Itoa(total))
	ctx.JSON(http.StatusOK, rows)
}
