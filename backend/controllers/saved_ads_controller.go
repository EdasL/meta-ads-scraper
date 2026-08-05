package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// SavedAdsController serves a user's saved ads (list + save + remove) — the
// ad-level sibling of SavedGemsController. Writes are token-guarded the same way.
type SavedAdsController struct{}

func NewSavedAdsController() *SavedAdsController { return &SavedAdsController{} }

// externalAdID validates the :external_ad_id path param — a Meta ad archive id
// (digits in practice; bounded to keep junk out of the PK).
func externalAdID(ctx *gin.Context) (string, bool) {
	id := ctx.Param("external_ad_id")
	if id == "" || len(id) > 64 {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "external_ad_id is required.")
		return "", false
	}
	return id, true
}

func (c *SavedAdsController) List(ctx *gin.Context) {
	email, ok := ctx.GetQuery("user_email")
	if !ok || email == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "user_email is required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ListSavedAds(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load saved ads.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}

func (c *SavedAdsController) Save(ctx *gin.Context) {
	id, ok := externalAdID(ctx)
	if !ok {
		return
	}
	var body models.SavedAdIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.UpsertSavedAd(rctx, db.DB(), body, id); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save ad.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "external_ad_id": id})
}

func (c *SavedAdsController) Remove(ctx *gin.Context) {
	id, ok := externalAdID(ctx)
	if !ok {
		return
	}
	var body models.SavedAdRemoveIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.DeleteSavedAd(rctx, db.DB(), body.UserEmail, id); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not remove saved ad.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok"})
}
