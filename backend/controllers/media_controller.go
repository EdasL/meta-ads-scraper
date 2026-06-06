package controllers

import (
	"net/http"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
)

// MediaController streams creative/funnel bytes from MinIO/S3. The endpoint is
// the browser's only way to reach the internal object store (mirrors
// src/agf/api/routers/media.py), so the key allowlist is the whole authz model.
type MediaController struct {
	s3     *s3.Client
	bucket string
}

func NewMediaController(client *s3.Client, bucket string) *MediaController {
	return &MediaController{s3: client, bucket: bucket}
}

func (c *MediaController) Get(ctx *gin.Context) {
	// gin's catch-all param includes the leading slash.
	key := strings.TrimPrefix(ctx.Param("key"), "/")
	if !helpers.ValidMediaKey(key) {
		helpers.Detail(ctx, http.StatusForbidden, "nope. not that.")
		return
	}
	out, err := c.s3.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		helpers.Detail(ctx, http.StatusNotFound, "grug not find that.")
		return
	}
	defer out.Body.Close()

	ctype := "application/octet-stream"
	if out.ContentType != nil && *out.ContentType != "" {
		ctype = *out.ContentType
	}
	var clen int64 = -1
	if out.ContentLength != nil {
		clen = *out.ContentLength
	}
	ctx.DataFromReader(http.StatusOK, clen, ctype, out.Body, nil)
}
