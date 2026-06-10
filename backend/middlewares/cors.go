// Package middlewares holds gin middleware.
package middlewares

import (
	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
)

// CORS mirrors src/agf/api/app.py: an origin allowlist, GET/POST/OPTIONS, the
// common request headers, and credentials enabled — so the Next.js dossier UI
// can call the API from the browser.
func CORS(origins []string) gin.HandlerFunc {
	return cors.New(cors.Config{
		AllowOrigins:     origins,
		AllowMethods:     []string{"GET", "POST", "OPTIONS"},
		AllowHeaders:     []string{"Origin", "Content-Type", "Accept", "Authorization", "X-Requested-With"},
		// X-Total-Count carries the shortlist's all-pages row count (pagination).
		ExposeHeaders:    []string{"Content-Length", "Content-Type", "X-Total-Count"},
		AllowCredentials: true,
		MaxAge:           12 * 3600,
	})
}
