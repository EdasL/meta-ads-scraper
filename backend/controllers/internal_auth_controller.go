package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

type InternalAuthController struct{}

func NewInternalAuthController() *InternalAuthController { return &InternalAuthController{} }

func (ia *InternalAuthController) Login(ctx *gin.Context) {
	var body struct{ Email, Password string }
	if err := ctx.ShouldBindJSON(&body); err != nil || body.Email == "" || body.Password == "" {
		helpers.Detail(ctx, http.StatusBadRequest, "email and password are required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	ku, name, err := repositories.VerifyLogin(rctx, db.DB(), body.Email, body.Password)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if ku == nil {
		helpers.Detail(ctx, http.StatusUnauthorized, "wrong email or password.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"email": ku.Email, "name": name, "role": ku.Role})
}

func (ia *InternalAuthController) OAuthLogin(ctx *gin.Context) {
	var body struct {
		Email     string `json:"email"`
		Name      string `json:"name"`
		Provider  string `json:"provider"`
		AvatarURL string `json:"avatar_url"`
	}
	if err := ctx.ShouldBindJSON(&body); err != nil || body.Email == "" ||
		(body.Provider != "google" && body.Provider != "github") {
		helpers.Detail(ctx, http.StatusBadRequest, "email and a known provider are required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	p, err := repositories.OAuthUpsertUser(rctx, db.DB(), body.Email, body.Name, body.Provider, body.AvatarURL)
	if err != nil || p == nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{
		"email": p.Email, "name": p.Name, "role": p.Role, "onboarded": p.OnboardedAt != nil,
	})
}

func (ia *InternalAuthController) ListKeys(ctx *gin.Context) {
	email := ctx.Query("email")
	if email == "" {
		helpers.Detail(ctx, http.StatusBadRequest, "email is required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	keys, err := repositories.ListAPIKeys(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.JSON(http.StatusOK, keys)
}

func (ia *InternalAuthController) MintKey(ctx *gin.Context) {
	var body struct{ Email, Label string }
	if err := ctx.ShouldBindJSON(&body); err != nil || body.Email == "" {
		helpers.Detail(ctx, http.StatusBadRequest, "email is required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	plain, err := repositories.CreateAPIKey(rctx, db.DB(), body.Email, body.Label)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"key": plain})
}

func (ia *InternalAuthController) RevokeKey(ctx *gin.Context) {
	var body struct {
		Email   string `json:"email"`
		KeyHash string `json:"key_hash"`
	}
	if err := ctx.ShouldBindJSON(&body); err != nil || body.Email == "" || body.KeyHash == "" {
		helpers.Detail(ctx, http.StatusBadRequest, "email and key_hash are required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.RevokeAPIKey(rctx, db.DB(), body.Email, body.KeyHash); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.Status(http.StatusNoContent)
}
