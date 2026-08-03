package controllers

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// maxSavedGroupNameLen bounds a column title so a stray paste cannot become an
// unreadable board header.
const maxSavedGroupNameLen = 60

// SavedGroupsController serves the /saved board columns. Identity always comes
// from KeyAuth via UserFrom — never a query or body param — and an id that is
// unknown or owned by somebody else answers 404 (never 403), so a caller cannot
// probe for other users' groups.
type SavedGroupsController struct{}

func NewSavedGroupsController() *SavedGroupsController { return &SavedGroupsController{} }

// caller resolves the signed-in user, writing 401 and returning ok=false when
// the request is unauthenticated.
func (c *SavedGroupsController) caller(ctx *gin.Context) (email string, ok bool) {
	u, authed := middlewares.UserFrom(ctx.Request.Context())
	if !authed {
		helpers.Detail(ctx, http.StatusUnauthorized, "not signed in.")
		return "", false
	}
	return u.Email, true
}

// groupID parses :id, writing 404 on a non-numeric value so a bad id is
// indistinguishable from a missing one.
func (c *SavedGroupsController) groupID(ctx *gin.Context) (int64, bool) {
	id, err := strconv.ParseInt(ctx.Param("id"), 10, 64)
	if err != nil {
		helpers.Detail(ctx, http.StatusNotFound, "no such group.")
		return 0, false
	}
	return id, true
}

// List handles GET /api/saved-groups. Folders are optional, so a user who has
// never made one gets an empty list.
func (c *SavedGroupsController) List(ctx *gin.Context) {
	email, ok := c.caller(ctx)
	if !ok {
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ListSavedGroups(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load groups.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}

type savedGroupCreateIn struct {
	Name string `json:"name"`
}

// Create handles POST /api/saved-groups — appends a column at the right edge.
func (c *SavedGroupsController) Create(ctx *gin.Context) {
	email, ok := c.caller(ctx)
	if !ok {
		return
	}
	var body savedGroupCreateIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "name is required.")
		return
	}
	name, ok := validSavedGroupName(ctx, body.Name)
	if !ok {
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	row, err := repositories.CreateSavedGroup(rctx, db.DB(), email, name)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not create group.")
		return
	}
	ctx.JSON(http.StatusOK, row)
}

type savedGroupUpdateIn struct {
	Name     *string `json:"name"`
	Position *int    `json:"position"`
}

// Update handles POST /api/saved-groups/:id/update — rename and/or reorder.
// Omitted fields are left alone.
func (c *SavedGroupsController) Update(ctx *gin.Context) {
	email, ok := c.caller(ctx)
	if !ok {
		return
	}
	id, ok := c.groupID(ctx)
	if !ok {
		return
	}
	var body savedGroupUpdateIn
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "name or position is required.")
		return
	}
	if body.Name != nil {
		name, valid := validSavedGroupName(ctx, *body.Name)
		if !valid {
			return
		}
		body.Name = &name
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	updated, err := repositories.UpdateSavedGroup(rctx, db.DB(), email, id, body.Name, body.Position)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not update group.")
		return
	}
	if !updated {
		helpers.Detail(ctx, http.StatusNotFound, "no such group.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok"})
}

// Remove handles POST /api/saved-groups/:id/remove. Gems in the folder are
// unfiled, not deleted; a user may remove every folder they have.
func (c *SavedGroupsController) Remove(ctx *gin.Context) {
	email, ok := c.caller(ctx)
	if !ok {
		return
	}
	id, ok := c.groupID(ctx)
	if !ok {
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	deleted, err := repositories.DeleteSavedGroup(rctx, db.DB(), email, id)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not remove group.")
		return
	}
	if !deleted {
		helpers.Detail(ctx, http.StatusNotFound, "no such group.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"status": "ok"})
}

// validSavedGroupName trims the name and writes a 422 when it is empty or too
// long, returning ok=false in that case.
func validSavedGroupName(ctx *gin.Context, raw string) (string, bool) {
	name := strings.TrimSpace(raw)
	if name == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "name is required.")
		return "", false
	}
	if len([]rune(name)) > maxSavedGroupNameLen {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "name is too long.")
		return "", false
	}
	return name, true
}
