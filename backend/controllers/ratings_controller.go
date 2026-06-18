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

// List handles GET /api/ratings?user_email=...
// Returns a map of advertiser_id → {rating, comment, features}.
func (c *RatingsController) List(ctx *gin.Context) {
	email, ok := ctx.GetQuery("user_email")
	if !ok || email == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "user_email is required.")
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
func (c *RatingsController) Save(ctx *gin.Context) {
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
		if err := repositories.DeleteRating(rctx, db.DB(), body.UserEmail, id); err != nil {
			helpers.Detail(ctx, http.StatusInternalServerError, "could not remove rating.")
			return
		}
	} else {
		if err := repositories.UpsertRating(rctx, db.DB(), body.UserEmail, id, body.Rating, body.Comment); err != nil {
			helpers.Detail(ctx, http.StatusInternalServerError, "could not save rating.")
			return
		}
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "advertiser_id": id})
}
