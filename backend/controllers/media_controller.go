package controllers

import (
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/storage"
)

// MediaController streams creative/funnel bytes from the object store (GCS in
// prod, MinIO in dev). The endpoint is the browser's only way to reach the
// store (mirrors src/agf/api/routers/media.py), so the key allowlist is the
// whole authz model.
type MediaController struct{}

func NewMediaController() *MediaController {
	return &MediaController{}
}

func (c *MediaController) Get(ctx *gin.Context) {
	// gin's catch-all param includes the leading slash.
	key := strings.TrimPrefix(ctx.Param("key"), "/")
	if !helpers.ValidMediaKey(key) {
		helpers.Detail(ctx, http.StatusForbidden, "forbidden.")
		return
	}
	obj, err := storage.Get(ctx, key)
	if err != nil {
		helpers.Detail(ctx, http.StatusNotFound, "not found.")
		return
	}
	defer obj.Body.Close()

	ctype := "application/octet-stream"
	if obj.ContentType != "" {
		ctype = obj.ContentType
	}
	ctx.DataFromReader(http.StatusOK, obj.Size, ctype, obj.Body, nil)
}
