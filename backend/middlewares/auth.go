package middlewares

import (
	"crypto/subtle"
	"net/http"
	"os"
	"strings"

	"github.com/gin-gonic/gin"
)

// APITokenEnv is the NAME of the env var that holds the optional write token —
// not a credential itself.
const APITokenEnv = "AGF_API_TOKEN" // #nosec G101 -- env var name, not a secret

// RequireToken guards mutating routes with a static bearer token. It is OPT-IN:
// when AGF_API_TOKEN is unset the guard is a no-op, preserving the original
// (auth-less) contract so the existing web client is unaffected. Set the env var
// in production to require `Authorization: Bearer <token>` on writes.
//
// The token is read once at construction (startup), and compared in constant
// time to avoid leaking length/prefix via timing.
func RequireToken() gin.HandlerFunc {
	want := os.Getenv(APITokenEnv)
	return func(c *gin.Context) {
		if want == "" {
			c.Next()
			return
		}
		got := strings.TrimPrefix(c.GetHeader("Authorization"), "Bearer ")
		if got == "" || subtle.ConstantTimeCompare([]byte(got), []byte(want)) != 1 {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"detail": "that not right. try again."})
			return
		}
		c.Next()
	}
}
