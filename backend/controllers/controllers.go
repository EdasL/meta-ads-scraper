package controllers

import (
	"context"
	"time"

	"github.com/gin-gonic/gin"
)

// queryTimeout bounds how long a single request may spend in the database, so a
// slow query can't hold a connection (or the client) open indefinitely. Tied to
// the request context, so a disconnected client also cancels the query.
const queryTimeout = 15 * time.Second

// reqCtx derives a timeout-bounded context from the incoming request.
func reqCtx(c *gin.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(c.Request.Context(), queryTimeout)
}
