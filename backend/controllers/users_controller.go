package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// UsersController serves the authenticated caller's own profile. Identity
// comes from KeyAuth (fd_ key or internal token + X-User-Email) — never from
// a query param, so a caller can only ever read or update themselves.
type UsersController struct{}

func NewUsersController() *UsersController { return &UsersController{} }

func (uc *UsersController) Me(ctx *gin.Context) {
	u, ok := middlewares.UserFrom(ctx.Request.Context())
	if !ok {
		helpers.Detail(ctx, http.StatusUnauthorized, "not signed in.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	p, err := repositories.GetUserProfile(rctx, db.DB(), u.Email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if p == nil {
		helpers.Detail(ctx, http.StatusNotFound, "no such user.")
		return
	}
	ctx.JSON(http.StatusOK, p)
}

func (uc *UsersController) MarkOnboarded(ctx *gin.Context) {
	u, ok := middlewares.UserFrom(ctx.Request.Context())
	if !ok {
		helpers.Detail(ctx, http.StatusUnauthorized, "not signed in.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.MarkOnboarded(rctx, db.DB(), u.Email); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.Status(http.StatusNoContent)
}
