package middlewares

import (
	"log"
	"time"

	"github.com/gin-gonic/gin"
)

// AccessLog writes one structured line per request, including the request id, so
// logs carry correlation. Replaces gin.Logger(). It sits OUTSIDE Recovery in the
// chain, so even a panic'd request is logged with its final 500 status.
func AccessLog() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		rid := c.GetString(ContextRequestID)
		log.Printf("method=%s path=%s status=%d dur=%s ip=%s rid=%s",
			c.Request.Method, c.Request.URL.Path, c.Writer.Status(),
			time.Since(start).Round(time.Millisecond), c.ClientIP(), rid)
	}
}
