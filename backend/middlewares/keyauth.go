package middlewares

import (
	"context"
	"crypto/subtle"
	"net/http"
	"os"
	"strings"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

type AuthedUser struct {
	Email string
	Role  string
}

type userCtxKey struct{}

func WithUser(ctx context.Context, u AuthedUser) context.Context {
	return context.WithValue(ctx, userCtxKey{}, u)
}

func UserFrom(ctx context.Context) (AuthedUser, bool) {
	u, ok := ctx.Value(userCtxKey{}).(AuthedUser)
	return u, ok
}

// KeyAuth authenticates requests with a personal API key (Authorization:
// Bearer fd_...). With acceptInternal, the static internal token
// (AGF_API_TOKEN) is also accepted; identity then comes from X-User-Email,
// which is trusted ONLY on internal-token requests (the web server sets it
// from the NextAuth session).
func KeyAuth(acceptInternal bool) gin.HandlerFunc {
	internal := os.Getenv(APITokenEnv)
	return func(c *gin.Context) {
		bearer := strings.TrimPrefix(c.GetHeader("Authorization"), "Bearer ")
		if bearer == "" || bearer == c.GetHeader("Authorization") {
			unauthorized(c)
			return
		}
		if acceptInternal && internal != "" &&
			subtle.ConstantTimeCompare([]byte(bearer), []byte(internal)) == 1 {
			email := c.GetHeader("X-User-Email")
			if email == "" {
				unauthorized(c)
				return
			}
			role, err := lookupRole(c, email)
			if err != nil || role == "" {
				unauthorized(c)
				return
			}
			attach(c, AuthedUser{Email: email, Role: role})
			return
		}
		if !strings.HasPrefix(bearer, "fd_") {
			unauthorized(c)
			return
		}
		ku, err := repositories.LookupAPIKey(c.Request.Context(), db.DB(), repositories.HashAPIKey(bearer))
		if err != nil {
			helpers.Detail(c, http.StatusInternalServerError, "something went wrong.")
			c.Abort()
			return
		}
		if ku == nil {
			unauthorized(c)
			return
		}
		repositories.TouchAPIKey(db.DB(), repositories.HashAPIKey(bearer))
		attach(c, AuthedUser{Email: ku.Email, Role: ku.Role})
	}
}

func attach(c *gin.Context, u AuthedUser) {
	c.Request = c.Request.WithContext(WithUser(c.Request.Context(), u))
	c.Next()
}

func lookupRole(c *gin.Context, email string) (string, error) {
	var rows []struct{ Role string }
	err := db.WithReadTx(c.Request.Context(), db.DB(), func(tx *gorm.DB) error {
		return tx.Raw(`SELECT role FROM frontend.users WHERE lower(email) = lower(?)`, email).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return "", err
	}
	return rows[0].Role, nil
}

func unauthorized(c *gin.Context) {
	helpers.Detail(c, http.StatusUnauthorized, "invalid or missing API key.")
	c.Abort()
}
