package controllers

import (
	"archive/zip"
	"fmt"
	"io"
	"log"
	"net/http"
	"path"
	"regexp"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/edaslakavicius/emerging-app-finder/backend/storage"
)

// A creative key is content-addressed (`creatives/<sha256>.jpg`), so the raw
// object name is a 64-char hash. Zip entries are named after the ad instead,
// matching adDownloadName in web/src/lib/adMedia.ts — keep the two in step so a
// single download and a zipped one produce the same filename.
var nonSlug = regexp.MustCompile(`[^a-z0-9]+`)
var shortExt = regexp.MustCompile(`^[a-z0-9]{2,4}$`)

func savedAdFileName(ad models.SavedAdMedia) string {
	ext := strings.ToLower(strings.TrimPrefix(path.Ext(ad.MediaS3Key), "."))
	if !shortExt.MatchString(ext) {
		ext = "jpg"
		if ad.MediaType != nil && *ad.MediaType == "video" {
			ext = "mp4"
		}
	}
	name := ""
	if ad.PageName != nil {
		name = *ad.PageName
	} else if ad.AppName != nil {
		name = *ad.AppName
	}
	slug := strings.Trim(nonSlug.ReplaceAllString(strings.ToLower(name), "-"), "-")
	if len(slug) > 40 {
		slug = slug[:40]
	}
	if slug == "" {
		slug = "ad"
	}
	return fmt.Sprintf("%s-%s.%s", slug, ad.ExternalAdID, ext)
}

// writeSavedAdsZip streams every fetchable creative into w and returns how many
// entries it wrote. `fetch` is injected so the assembly is testable without an
// object store. An object that can't be read is skipped: one missing creative
// must not cost the caller the whole archive.
func writeSavedAdsZip(w io.Writer, rows []models.SavedAdMedia, fetch func(string) (io.ReadCloser, error)) (int, error) {
	zw := zip.NewWriter(w)
	used := map[string]int{}
	written := 0
	for _, ad := range rows {
		body, err := fetch(ad.MediaS3Key)
		if err != nil {
			log.Printf("saved-ads zip: skipping %s (%s): %v", ad.ExternalAdID, ad.MediaS3Key, err)
			continue
		}
		name := savedAdFileName(ad)
		// Two saves of the same ad id would otherwise collide inside the zip.
		if n := used[name]; n > 0 {
			ext := path.Ext(name)
			name = fmt.Sprintf("%s-%d%s", strings.TrimSuffix(name, ext), n+1, ext)
		}
		used[savedAdFileName(ad)]++

		entry, err := zw.Create(name)
		if err != nil {
			body.Close()
			return written, err
		}
		_, err = io.Copy(entry, body)
		body.Close()
		if err != nil {
			return written, err
		}
		written++
	}
	return written, zw.Close()
}

// SavedAdsController serves a user's saved ads (list + save + remove) — the
// ad-level sibling of SavedGemsController. Writes are token-guarded the same way.
type SavedAdsController struct{}

func NewSavedAdsController() *SavedAdsController { return &SavedAdsController{} }

// externalAdID validates the :external_ad_id path param — a Meta ad archive id
// (digits in practice; bounded to keep junk out of the PK).
func externalAdID(ctx *gin.Context) (string, bool) {
	id := ctx.Param("external_ad_id")
	if id == "" || len(id) > 64 {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "external_ad_id is required.")
		return "", false
	}
	return id, true
}

func (c *SavedAdsController) List(ctx *gin.Context) {
	email, ok := ctx.GetQuery("user_email")
	if !ok || email == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "user_email is required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ListSavedAds(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load saved ads.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}

// Download streams every saved creative for a user as one zip. Entries are
// named after the ad, and unreadable objects are skipped so a single missing
// creative can't fail the whole archive. The body is streamed, so a failure
// partway through can only truncate it — hence the pre-flight empty check.
func (c *SavedAdsController) Download(ctx *gin.Context) {
	email, ok := ctx.GetQuery("user_email")
	if !ok || email == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "user_email is required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ListSavedAdMedia(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load saved ads.")
		return
	}
	if len(rows) == 0 {
		helpers.Detail(ctx, http.StatusNotFound, "no saved ad creatives to download.")
		return
	}

	ctx.Header("Content-Type", "application/zip")
	ctx.Header("Content-Disposition", `attachment; filename="saved-ads.zip"`)
	ctx.Status(http.StatusOK)

	n, err := writeSavedAdsZip(ctx.Writer, rows, func(key string) (io.ReadCloser, error) {
		if !helpers.ValidMediaKey(key) {
			return nil, fmt.Errorf("rejected key %q", key)
		}
		obj, err := storage.Get(rctx, key)
		if err != nil {
			return nil, err
		}
		return obj.Body, nil
	})
	if err != nil {
		// Headers are already out; all that's left is to record the truncation.
		log.Printf("saved-ads zip: aborted after %d entries for %s: %v", n, email, err)
	}
}

func (c *SavedAdsController) Save(ctx *gin.Context) {
	id, ok := externalAdID(ctx)
	if !ok {
		return
	}
	var body models.SavedAdIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.UpsertSavedAd(rctx, db.DB(), body, id); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save ad.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok", "external_ad_id": id})
}

func (c *SavedAdsController) Remove(ctx *gin.Context) {
	id, ok := externalAdID(ctx)
	if !ok {
		return
	}
	var body models.SavedAdRemoveIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.DeleteSavedAd(rctx, db.DB(), body.UserEmail, id); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not remove saved ad.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok"})
}
