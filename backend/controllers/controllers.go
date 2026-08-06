package controllers

import (
	"context"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
)

// queryTimeout bounds how long a single request may spend in the database, so a
// slow query can't hold a connection (or the client) open indefinitely. Tied to
// the request context, so a disconnected client also cancels the query.
const queryTimeout = 15 * time.Second

// reqCtx derives a timeout-bounded context from the incoming request.
func reqCtx(c *gin.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(c.Request.Context(), queryTimeout)
}

// callerEmail resolves the signed-in user from KeyAuth, writing 401 and
// returning ok=false when the request carries no resolvable identity.
//
// This is the ONLY sanctioned identity source for a per-user endpoint. A
// ?user_email query param or a user_email body field is caller-supplied and
// binds to no credential: honouring one lets any authenticated caller read or
// write somebody else's rows. KeyAuth resolves the same value from either
// credential path — a personal fd_ key resolves to the key's owner, and an
// internal-token request resolves to the X-User-Email the web proxy sets from
// the session — so the web client keeps working while its param goes inert.
func callerEmail(c *gin.Context) (string, bool) {
	u, authed := middlewares.UserFrom(c.Request.Context())
	if !authed || u.Email == "" {
		helpers.Detail(c, http.StatusUnauthorized, "not signed in.")
		return "", false
	}
	return u.Email, true
}
