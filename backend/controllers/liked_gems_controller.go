package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// LikedGemsController serves the built-in Liked collection on /saved. Identity
// comes from KeyAuth via UserFrom, never a query param — one user cannot read
// another's likes by guessing an email.
type LikedGemsController struct{}

func NewLikedGemsController() *LikedGemsController { return &LikedGemsController{} }

// List handles GET /api/liked-gems. A user who has liked nothing gets [].
func (c *LikedGemsController) List(ctx *gin.Context) {
	u, authed := middlewares.UserFrom(ctx.Request.Context())
	if !authed {
		helpers.Detail(ctx, http.StatusUnauthorized, "not signed in.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ListLikedGems(rctx, db.DB(), u.Email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load liked apps.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}
