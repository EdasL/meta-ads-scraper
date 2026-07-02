package integration

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"golang.org/x/crypto/bcrypt"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

func TestKeyAuthResolvesRealKey(t *testing.T) {
	ctx := context.Background()
	hash, _ := bcrypt.GenerateFromPassword([]byte("x"), bcrypt.DefaultCost)
	if err := repositories.UpsertUser(ctx, db.DB(), "mw@test.local", string(hash), "", "admin"); err != nil {
		t.Fatal(err)
	}
	plain, err := repositories.CreateAPIKey(ctx, db.DB(), "mw@test.local", "t")
	if err != nil {
		t.Fatal(err)
	}

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/whoami", middlewares.KeyAuth(false), func(c *gin.Context) {
		u, _ := middlewares.UserFrom(c.Request.Context())
		c.JSON(200, u)
	})

	do := func(token string) int {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/whoami", nil)
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		r.ServeHTTP(w, req)
		return w.Code
	}
	if c := do(plain); c != 200 {
		t.Fatalf("valid key: want 200 got %d", c)
	}
	if c := do("fd_bogus"); c != 401 {
		t.Fatalf("bogus key: want 401 got %d", c)
	}
	keys, _ := repositories.ListAPIKeys(ctx, db.DB(), "mw@test.local")
	_ = repositories.RevokeAPIKey(ctx, db.DB(), "mw@test.local", keys[0].KeyHash)
	if c := do(plain); c != 401 {
		t.Fatalf("revoked key: want 401 got %d", c)
	}
}
