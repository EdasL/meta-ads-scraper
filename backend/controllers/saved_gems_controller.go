package controllers

import (
	"errors"
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
type SavedGemsController struct{}

func NewSavedGemsController() *SavedGemsController { return &SavedGemsController{} }

func (c *SavedGemsController) List(ctx *gin.Context) {
	email, ok := ctx.GetQuery("user_email")
	if !ok || email == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "user_email is required.")
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
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.UpsertSavedGem(rctx, db.DB(), body, id); err != nil {
		// A group_id that is unknown or owned by somebody else is the caller's
		// mistake, not a server fault.
		if errors.Is(err, repositories.ErrGroupNotOwned) {
			helpers.Detail(ctx, http.StatusBadRequest, "group not found.")
			return
		}
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save gem.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "advertiser_id": id})
}

func (c *SavedGemsController) Remove(ctx *gin.Context) {
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
	if err := repositories.DeleteSavedGem(rctx, db.DB(), body.UserEmail, id); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not remove gem.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "advertiser_id": id})
}
