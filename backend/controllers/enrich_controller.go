package controllers

import (
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// enrichTimeout bounds how long a non-terminal row is trusted before the
// status endpoint reports it as failed. Budget: queue pickup by the worker-VM
// daemon (QUEUED_MAX_AGE_SEC=120 in agf/enrich_full/queue_daemon.py) + the
// enrich_one run itself (--timeout 180) + margin.
const enrichTimeout = 360 * time.Second

type EnrichController struct{}

func NewEnrichController() *EnrichController { return &EnrichController{} }

func toEnrichOut(row *models.EnrichJobRow) models.EnrichJobOut {
	out := models.EnrichJobOut{
		JobID:      row.ID,
		Status:     row.Status,
		ErrorText:  row.ErrorText,
		StartedAt:  row.StartedAt,
		FinishedAt: row.FinishedAt,
	}
	// 'queued' is a queue-internal state (waiting for the worker-VM daemon);
	// the UI only knows running/done/error.
	if out.Status == "queued" {
		out.Status = "running"
	}
	// Stale-guard: a non-terminal row past the timeout means the daemon never
	// picked it up or the subprocess died without recording a terminal status.
	if out.Status == "running" && time.Since(time.Time(row.StartedAt)) > enrichTimeout {
		out.Status = "error"
		msg := "enrichment timed out"
		out.ErrorText = &msg
	}
	return out
}

// Trigger handles POST /api/enrich/:advertiser_id. It only enqueues: the row
// is claimed and run by the enrich-queue daemon on the worker VM (this API
// runs in a container with no Python pipeline).
func (c *EnrichController) Trigger(ctx *gin.Context) {
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()

	// Dedupe: a fresh in-flight job → return it, don't enqueue a second.
	latest, err := repositories.LatestEnrichJob(rctx, db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not read enrich status.")
		return
	}
	if latest != nil && (latest.Status == "queued" || latest.Status == "running") &&
		time.Since(time.Time(latest.StartedAt)) <= enrichTimeout {
		ctx.JSON(http.StatusAccepted, toEnrichOut(latest))
		return
	}

	jobID, err := repositories.InsertEnrichJob(rctx, db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not start enrichment.")
		return
	}
	// Return a consistent EnrichJobOut shape (re-fetch the row we just inserted).
	row, err := repositories.LatestEnrichJob(rctx, db.DB(), id)
	if err != nil || row == nil {
		// Fall back to a minimal but typed shape.
		ctx.JSON(http.StatusAccepted, models.EnrichJobOut{JobID: jobID, Status: "running", StartedAt: models.APITime(time.Now())})
		return
	}
	ctx.JSON(http.StatusAccepted, toEnrichOut(row))
}

// Status handles GET /api/enrich/:advertiser_id.
func (c *EnrichController) Status(ctx *gin.Context) {
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	latest, err := repositories.LatestEnrichJob(rctx, db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not read enrich status.")
		return
	}
	if latest == nil {
		ctx.JSON(http.StatusOK, gin.H{"job_id": 0, "status": "none"})
		return
	}
	ctx.JSON(http.StatusOK, toEnrichOut(latest))
}
