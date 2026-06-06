package controllers

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// GemsController serves the gem dossier and its scaling series.
type GemsController struct{}

func NewGemsController() *GemsController { return &GemsController{} }

func (c *GemsController) Get(ctx *gin.Context) {
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	d, err := repositories.Dossier(db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "grug board broke.")
		return
	}
	if d == nil {
		helpers.Detail(ctx, http.StatusNotFound, "gem not found. loosen filter.")
		return
	}
	ctx.JSON(http.StatusOK, d)
}

func (c *GemsController) Scaling(ctx *gin.Context) {
	id, err := strconv.Atoi(ctx.Param("advertiser_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "advertiser_id must be an integer.")
		return
	}
	pts, err := repositories.Scaling(db.DB(), id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "grug board broke.")
		return
	}
	ctx.JSON(http.StatusOK, pts)
}
