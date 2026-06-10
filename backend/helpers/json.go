// Package helpers holds small response/query utilities.
//
// DEVIATION from the sibling Go repos: we do NOT use a {success,data}/{success,error}
// envelope. This API returns bare JSON shapes and FastAPI-style error bodies
// ({"detail": "..."}), because the web client (web/src/lib/adapters.ts) consumes
// them directly. See backend/DEVIATIONS.md.
package helpers

import (
	"regexp"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
)

// Detail writes a FastAPI-style error body: {"detail": "..."}.
func Detail(ctx *gin.Context, status int, msg string) {
	ctx.JSON(status, gin.H{"detail": msg})
}

// QueryFloat returns the float query param, def when absent/empty, and an error
// when present-but-unparseable (so the caller can mirror FastAPI's 422).
func QueryFloat(ctx *gin.Context, key string, def float64) (float64, error) {
	v, ok := ctx.GetQuery(key)
	if !ok || v == "" {
		return def, nil
	}
	return strconv.ParseFloat(v, 64)
}

// QueryInt returns the int query param, def when absent/empty, and an error when
// present-but-unparseable.
func QueryInt(ctx *gin.Context, key string, def int) (int, error) {
	v, ok := ctx.GetQuery(key)
	if !ok || v == "" {
		return def, nil
	}
	return strconv.Atoi(v)
}

// QueryBool returns the bool query param (strconv.ParseBool forms: true/false,
// 1/0, t/f…), def when absent/empty, and an error when present-but-unparseable.
func QueryBool(ctx *gin.Context, key string, def bool) (bool, error) {
	v, ok := ctx.GetQuery(key)
	if !ok || v == "" {
		return def, nil
	}
	return strconv.ParseBool(v)
}

// mediaKey is the media-proxy allowlist (mirrors src/agf/api/routers/media.py):
// a key must start with a known prefix, no leading slash, and the caller also
// rejects any ".." — together this is the whole authorization model.
var mediaKey = regexp.MustCompile(`^(creatives|funnels)/[A-Za-z0-9][A-Za-z0-9._/-]*$`)

// ValidMediaKey reports whether key is a safe, servable S3 object key.
func ValidMediaKey(key string) bool {
	return !strings.Contains(key, "..") && mediaKey.MatchString(key)
}
