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

// ReviewController serves the review queue and the single decision write.
type ReviewController struct{}

func NewReviewController() *ReviewController { return &ReviewController{} }

func (c *ReviewController) Queue(ctx *gin.Context) {
	minScore, err := helpers.QueryFloat(ctx, "min_score", 70.0)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad min_score.")
		return
	}
	limit, err := helpers.QueryInt(ctx, "limit", 50)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad limit.")
		return
	}
	offset, err := helpers.QueryInt(ctx, "offset", 0)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad offset.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ReviewQueue(rctx, db.DB(), minScore, limit, offset)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}

func (c *ReviewController) Decision(ctx *gin.Context) {
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	var body models.ReviewDecisionIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		// Mirrors Pydantic's 422 on a bad verdict / missing reviewer.
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.RecordDecision(rctx, db.DB(), id, body.Verdict, body.Reviewer, body.Note); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save decision.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "advertiser_id": id, "verdict": body.Verdict})
}
