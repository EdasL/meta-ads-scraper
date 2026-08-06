package controllers

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// SavedGemsController serves a user's watchlist (list + upsert + remove). Writes
// are guarded by the optional bearer token, like the review decision.
//
// Identity on every route comes from KeyAuth via callerEmail — never the
// ?user_email query param or the body's user_email field. Both are still sent
// by the web client and both are deliberately ignored.
type SavedGemsController struct{}

func NewSavedGemsController() *SavedGemsController { return &SavedGemsController{} }

func (c *SavedGemsController) List(ctx *gin.Context) {
	email, ok := callerEmail(ctx)
	if !ok {
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ListSavedGems(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load saved gems.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}

func (c *SavedGemsController) Save(ctx *gin.Context) {
	email, ok := callerEmail(ctx)
	if !ok {
		return
	}
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	var body models.SavedGemIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	// Whatever the body claimed, the row belongs to the caller.
	body.UserEmail = email
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.UpsertSavedGem(rctx, db.DB(), body, id); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save gem.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "advertiser_id": id})
}

func (c *SavedGemsController) Remove(ctx *gin.Context) {
	email, ok := callerEmail(ctx)
	if !ok {
		return
	}
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	var body models.SavedGemRemoveIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.DeleteSavedGem(rctx, db.DB(), email, id); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not remove gem.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "advertiser_id": id})
}
