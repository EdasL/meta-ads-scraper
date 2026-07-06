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

// PreferenceController serves the per-user /review queue, ordered by the
// preference model's predicted-like score (explore/exploit blended).
type PreferenceController struct{}

func NewPreferenceController() *PreferenceController { return &PreferenceController{} }

// Queue handles GET /api/preference-queue?user_email=...&limit=...&exclude=1,2,3
func (c *PreferenceController) Queue(ctx *gin.Context) {
	email, ok := ctx.GetQuery("user_email")
	if !ok || email == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "user_email is required.")
		return
	}
	// The review deck fetches a small buffer and refills in the background as
	// the user rates; scoring happens at serve time so each refill reflects the
	// latest ratings. The high ceiling stays as a backstop for API consumers.
	limit, err := helpers.QueryInt(ctx, "limit", 50)
	if err != nil || limit <= 0 || limit > 5000 {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad limit.")
		return
	}
	var exclude []int64
	if raw := ctx.Query("exclude"); raw != "" {
		parts := strings.Split(raw, ",")
		if len(parts) > 500 {
			helpers.Detail(ctx, http.StatusUnprocessableEntity, "too many excluded ids.")
			return
		}
		for _, p := range parts {
			id, perr := strconv.ParseInt(strings.TrimSpace(p), 10, 64)
			if perr != nil {
				helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad exclude.")
				return
			}
			exclude = append(exclude, id)
		}
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.PreferenceQueue(rctx, db.DB(), email, limit, exclude)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load the review queue.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}
