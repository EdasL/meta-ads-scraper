package integration

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
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

// TestKeyAuthInternalPath covers the acceptInternal branch: identity comes from
// X-User-Email, and the role lookup must distinguish a DB failure (500) from an
// unknown user (401) — the same contract the fd_ key path already honours.
func TestKeyAuthInternalPath(t *testing.T) {
	ctx := context.Background()
	hash, _ := bcrypt.GenerateFromPassword([]byte("x"), bcrypt.DefaultCost)
	if err := repositories.UpsertUser(ctx, db.DB(), "internal@test.local", string(hash), "", "admin"); err != nil {
		t.Fatal(err)
	}

	const token = "internal-secret-token"
	t.Setenv(middlewares.APITokenEnv, token)

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/whoami", middlewares.KeyAuth(true), func(c *gin.Context) {
		u, _ := middlewares.UserFrom(c.Request.Context())
		c.JSON(200, u)
	})

	do := func(req *http.Request) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		r.ServeHTTP(w, req)
		return w
	}
	newReq := func(email string) *http.Request {
		req := httptest.NewRequest(http.MethodGet, "/whoami", nil)
		req.Header.Set("Authorization", "Bearer "+token)
		if email != "" {
			req.Header.Set("X-User-Email", email)
		}
		return req
	}

	// Known user → 200 with the resolved role.
	w := do(newReq("internal@test.local"))
	if w.Code != 200 {
		t.Fatalf("known user: want 200 got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), `"admin"`) {
		t.Fatalf("known user: role not attached: %s", w.Body.String())
	}

	// Missing X-User-Email → 401 (no identity to trust).
	if c := do(newReq("")).Code; c != 401 {
		t.Fatalf("no email: want 401 got %d", c)
	}

	// Valid internal token but no such user → 401, NOT 500.
	if c := do(newReq("ghost@test.local")).Code; c != 401 {
		t.Fatalf("unknown user: want 401 got %d", c)
	}

	// DB failure during the role lookup → 500, NOT 401. A cancelled request
	// context makes the tx begin fail without disturbing the shared pool.
	cctx, cancel := context.WithCancel(ctx)
	cancel()
	if c := do(newReq("internal@test.local").WithContext(cctx)).Code; c != http.StatusInternalServerError {
		t.Fatalf("db error: want 500 got %d", c)
	}
}
