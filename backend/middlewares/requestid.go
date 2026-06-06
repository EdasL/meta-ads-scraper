package middlewares

import (
	"crypto/rand"
	"encoding/hex"

	"github.com/gin-gonic/gin"
)

// RequestIDHeader is the inbound/outbound correlation header.
const RequestIDHeader = "X-Request-ID"

// ContextRequestID is the gin-context key holding the request id.
const ContextRequestID = "request_id"

// RequestID gives every request an X-Request-ID (honoring an inbound one),
// stores it on the context, and echoes it on the response — so logs, traces, and
// the client can correlate a single request.
func RequestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader(RequestIDHeader)
		if id == "" {
			id = newID()
		}
		c.Set(ContextRequestID, id)
		c.Writer.Header().Set(RequestIDHeader, id)
		c.Next()
	}
}

func newID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "unknown"
	}
	return hex.EncodeToString(b)
}
