package controllers

import (
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// enrichTimeout bounds how long a 'running' row is trusted before the status
// endpoint reports it as failed (matches the Python --timeout default).
const enrichTimeout = 180 * time.Second

// spawnEnrich launches the detached one-shot enrichment subprocess. Overridden
// in tests. Default execs scripts/enrich_one.sh (AGF_ENRICH_SCRIPT to override).
var spawnEnrich = func(advertiserID, jobID int) error {
	script := os.Getenv("AGF_ENRICH_SCRIPT")
	if script == "" {
		// Relative path: the Go API's working directory MUST be the repo root
		// for this to resolve. Set AGF_ENRICH_SCRIPT to an absolute path if the
		// process is launched from elsewhere (e.g. launchd, a different cwd).
		script = "scripts/enrich_one.sh"
	}
	cmd := exec.Command(script,
		"--advertiser-id", strconv.Itoa(advertiserID),
		"--job-id", strconv.Itoa(jobID))
	if err := cmd.Start(); err != nil {
		return err
	}
	go func() { _ = cmd.Wait() }() // reap the child without blocking the request
	return nil
}

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
	// Stale-guard: a 'running' row past the timeout means the subprocess died
	// without recording a terminal status. Report it as error for the UI.
	if out.Status == "running" && time.Since(time.Time(row.StartedAt)) > enrichTimeout {
		out.Status = "error"
		msg := "enrichment timed out"
		out.ErrorText = &msg
	}
	return out
}

// Trigger handles POST /api/enrich/:advertiser_id.
func (c *EnrichController) Trigger(ctx *gin.Context) {
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()

	// Dedupe: a fresh 'running' job → return it, don't spawn a second.
	latest, err := repositories.LatestEnrichJob(rctx, db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not read enrich status.")
		return
	}
	if latest != nil && latest.Status == "running" &&
		time.Since(time.Time(latest.StartedAt)) <= enrichTimeout {
		ctx.JSON(http.StatusAccepted, toEnrichOut(latest))
		return
	}

	jobID, err := repositories.InsertEnrichJob(rctx, db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not start enrichment.")
		return
	}
	if err := spawnEnrich(id, jobID); err != nil {
		// The row was inserted as 'running'; without this it would hang there
		// until the stale-guard timeout. Flip it to 'error' so the UI sees it.
		_ = repositories.MarkEnrichJobError(rctx, db.DB(), jobID, "could not launch enrichment")
		helpers.Detail(ctx, http.StatusInternalServerError, "could not launch enrichment.")
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

// Test hooks — let integration tests stub the subprocess spawn.
func SetSpawnEnrichForTest(fn func(advertiserID, jobID int) error) {
	spawnEnrich = fn
}

var defaultSpawnEnrich = spawnEnrich

func ResetSpawnEnrichForTest() { spawnEnrich = defaultSpawnEnrich }
