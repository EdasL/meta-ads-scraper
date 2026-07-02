package integration

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"

	"golang.org/x/crypto/bcrypt"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// TestAPIGroupRequiresAuth is the lockdown gate: every route under the /api
// group now demands either a personal fd_ key or the internal token with a
// resolvable X-User-Email. It also pins the belt-and-suspenders on writes — an
// fd_ key clears the group's KeyAuth but cannot pass the per-route RequireToken
// once AGF_API_TOKEN is set, so REST writes stay internal-token-only.
//
// AGF_API_TOKEN is read by KeyAuth/RequireToken at construction, so t.Setenv
// must precede routers.Init (via router()).
func TestAPIGroupRequiresAuth(t *testing.T) {
	ctx := context.Background()
	const token = "lockdown-internal-token"
	t.Setenv(middlewares.APITokenEnv, token)

	// A seeded user for the internal-token identity path.
	hash, _ := bcrypt.GenerateFromPassword([]byte("x"), bcrypt.DefaultCost)
	if err := repositories.UpsertUser(ctx, db.DB(), authedEmail, string(hash), "IT", "admin"); err != nil {
		t.Fatal(err)
	}

	r := router()

	do := func(req *http.Request) int {
		w := httptest.NewRecorder()
		r.ServeHTTP(w, req)
		return w.Code
	}

	t.Run("keyless GET is 401", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/api/shortlist", nil)
		if c := do(req); c != http.StatusUnauthorized {
			t.Fatalf("keyless: want 401 got %d", c)
		}
	})

	t.Run("valid fd_ key GET is 200", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/api/shortlist", nil)
		req.Header.Set("Authorization", "Bearer "+authedKey)
		if c := do(req); c != http.StatusOK {
			t.Fatalf("fd_ key: want 200 got %d", c)
		}
	})

	t.Run("internal token + X-User-Email is 200", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/api/shortlist", nil)
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("X-User-Email", authedEmail)
		if c := do(req); c != http.StatusOK {
			t.Fatalf("internal+email: want 200 got %d", c)
		}
	})

	t.Run("internal token without X-User-Email is 401", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/api/shortlist", nil)
		req.Header.Set("Authorization", "Bearer "+token)
		if c := do(req); c != http.StatusUnauthorized {
			t.Fatalf("internal no-email: want 401 got %d", c)
		}
	})

	// Writes: an fd_ key clears KeyAuth but RequireToken (token set) rejects any
	// bearer that is not the internal token, so a personal key cannot write.
	t.Run("fd_ key on write route is 401", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodPost, "/api/ratings/"+strconv.Itoa(advID), nil)
		req.Header.Set("Authorization", "Bearer "+authedKey)
		if c := do(req); c != http.StatusUnauthorized {
			t.Fatalf("fd_ key write: want 401 got %d", c)
		}
	})
}
