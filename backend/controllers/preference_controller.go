package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// PreferenceController serves the per-user /review queue, ordered by the
// preference model's predicted-like score (explore/exploit blended).
type PreferenceController struct{}

func NewPreferenceController() *PreferenceController { return &PreferenceController{} }

// Queue handles GET /api/preference-queue?user_email=...&limit=...
func (c *PreferenceController) Queue(ctx *gin.Context) {
	email, ok := ctx.GetQuery("user_email")
	if !ok || email == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "user_email is required.")
		return
	}
	limit, err := helpers.QueryInt(ctx, "limit", 50)
	if err != nil || limit <= 0 || limit > 200 {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad limit.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.PreferenceQueue(rctx, db.DB(), email, limit)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load review queue.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}
