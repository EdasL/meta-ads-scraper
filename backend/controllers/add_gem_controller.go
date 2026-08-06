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

// AddGemController serves the on-demand gem add: the founder pastes a Meta Ad
// Library link, we resolve it to a page id and queue one add_page job. The
// scrape, triage and enrich all run on the worker VM (this API container has no
// Python pipeline), which is why the response is a job to poll rather than a gem.
type AddGemController struct{}

func NewAddGemController() *AddGemController { return &AddGemController{} }

type addGemIn struct {
	URL string `json:"url"`
}

func toAddGemOut(row *models.AddPageJobRow, alreadyKnown bool) models.AddGemOut {
	return models.AddGemOut{
		JobID:        row.ID,
		AdvertiserID: row.AdvertiserID,
		Status:       row.Status,
		Phase:        row.Phase,
		PageName:     row.PageName,
		IconURL:      row.IconURL,
		AdCount:      row.AdCount,
		TriageNote:   row.TriageNote,
		ErrorText:    row.ErrorText,
		AlreadyKnown: alreadyKnown,
	}
}

// Add handles POST /api/add-gem.
func (c *AddGemController) Add(ctx *gin.Context) {
	var in addGemIn
	if err := ctx.ShouldBindJSON(&in); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "Send a JSON body with a url field.")
		return
	}
	pageID, adID, err := helpers.ParseAdLibraryPageID(in.URL)
	if err != nil {
		// The parser's message is written for the person who pasted the link.
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()

	// A single-ad link only resolves against ads we already have: Meta's
	// ad-details response carries no page id.
	if pageID == "" && adID != "" {
		pageID, err = repositories.PageIDForAdID(rctx, db.DB(), adID)
		if err != nil {
			helpers.Detail(ctx, http.StatusInternalServerError, "Could not look up that ad.")
			return
		}
		if pageID == "" {
			helpers.Detail(ctx, http.StatusUnprocessableEntity,
				"That link points at one ad Field Desk has not seen. "+
					"Open the advertiser in Ad Library and paste that URL instead.")
			return
		}
	}

	advertiserID, alreadyKnown, err := repositories.UpsertAdvertiserByPageID(rctx, db.DB(), pageID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "Could not add that advertiser.")
		return
	}

	// One active job per advertiser. An in-flight job is returned as-is rather
	// than queueing a second scrape of the same page.
	if active, aerr := repositories.ActiveAddPageJob(rctx, db.DB(), advertiserID); aerr == nil && active != nil {
		ctx.JSON(http.StatusAccepted, toAddGemOut(active, true))
		return
	}
	if err := repositories.InsertAddPageJob(rctx, db.DB(), advertiserID); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "Could not start the scrape.")
		return
	}
	row, err := repositories.ActiveAddPageJob(rctx, db.DB(), advertiserID)
	if err != nil || row == nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "Could not start the scrape.")
		return
	}
	ctx.JSON(http.StatusAccepted, toAddGemOut(row, alreadyKnown))
}

// Status handles GET /api/add-gem/:job_id.
func (c *AddGemController) Status(ctx *gin.Context) {
	jobID, err := strconv.ParseInt(ctx.Param("job_id"), 10, 64)
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "job_id must be an integer.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	row, err := repositories.AddPageJobByID(rctx, db.DB(), jobID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "Could not read that job.")
		return
	}
	if row == nil {
		helpers.Detail(ctx, http.StatusNotFound, "No such job.")
		return
	}
	ctx.JSON(http.StatusOK, toAddGemOut(row, false))
}
