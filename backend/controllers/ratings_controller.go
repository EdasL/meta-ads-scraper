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

// RatingsController serves per-user app ratings (like/neutral/dislike + comment).
type RatingsController struct{}

func NewRatingsController() *RatingsController { return &RatingsController{} }

// List handles GET /api/ratings — the caller's own ratings.
// Returns a map of advertiser_id → {rating, comment, features}.
//
// Identity comes from KeyAuth, never from ?user_email: the param is
// caller-supplied and would let any valid key read anybody's ratings. The web
// client still sends it and it is deliberately ignored.
func (c *RatingsController) List(ctx *gin.Context) {
	email, ok := callerEmail(ctx)
	if !ok {
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ListRatings(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load ratings.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}

// Save handles POST /api/ratings/:advertiser_id
// rating=="none" removes the row; any other valid value upserts it.
//
// The row is written for the CALLER, resolved from KeyAuth. body.user_email is
// parsed for wire compatibility and then discarded: the route's RequireToken
// only admits the internal token, but the web proxy holds that token and
// forwards the browser's body verbatim, so a body-supplied email would let a
// signed-in user rate on somebody else's account.
func (c *RatingsController) Save(ctx *gin.Context) {
	email, ok := callerEmail(ctx)
	if !ok {
		return
	}
	id, err := strconv.ParseInt(ctx.Param("advertiser_id"), 10, 64)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	var body models.RatingIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if body.Rating == "none" {
		if err := repositories.DeleteRating(rctx, db.DB(), email, id); err != nil {
			helpers.Detail(ctx, http.StatusInternalServerError, "could not remove rating.")
			return
		}
	} else {
		if err := repositories.UpsertRating(rctx, db.DB(), email, id, body.Rating, body.Comment); err != nil {
			helpers.Detail(ctx, http.StatusInternalServerError, "could not save rating.")
			return
		}
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "advertiser_id": id})
}
