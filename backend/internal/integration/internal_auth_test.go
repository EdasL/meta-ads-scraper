package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"golang.org/x/crypto/bcrypt"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/edaslakavicius/emerging-app-finder/backend/routers"
)

// TestInternalAuthEndpoints exercises the /api/internal group end to end: login
// (right and wrong password), minting a key, listing it, and revoking it. All
// requests carry the static internal token, which RequireToken reads at
// construction — so t.Setenv must run BEFORE routers.Init builds the router.
func TestInternalAuthEndpoints(t *testing.T) {
	ctx := context.Background()
	// A per-run email keeps the mint/list/revoke key counts isolated from keys
	// left behind by earlier runs against the shared integration DB.
	email := fmt.Sprintf("internal-auth-%d@test.local", time.Now().UnixNano())
	const (
		password = "s3cret-pass"
		token    = "it-internal"
	)
	hash, _ := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err := repositories.UpsertUser(ctx, db.DB(), email, string(hash), "Ada", "admin"); err != nil {
		t.Fatal(err)
	}

	t.Setenv(middlewares.APITokenEnv, token)

	gin.SetMode(gin.TestMode)
	r := gin.New()
	routers.Init(r)

	do := func(method, path, body string) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		var rdr *strings.Reader
		if body != "" {
			rdr = strings.NewReader(body)
		} else {
			rdr = strings.NewReader("")
		}
		req := httptest.NewRequest(method, path, rdr)
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		r.ServeHTTP(w, req)
		return w
	}

	t.Run("login ok", func(t *testing.T) {
		w := do(http.MethodPost, "/api/internal/login", `{"email":"`+email+`","password":"`+password+`"}`)
		if w.Code != http.StatusOK {
			t.Fatalf("want 200 got %d: %s", w.Code, w.Body.String())
		}
		var got struct{ Email, Name, Role string }
		if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
			t.Fatal(err)
		}
		if got.Email != email || got.Role != "admin" {
			t.Fatalf("unexpected body: %s", w.Body.String())
		}
	})

	t.Run("login wrong password", func(t *testing.T) {
		w := do(http.MethodPost, "/api/internal/login", `{"email":"`+email+`","password":"nope"}`)
		if w.Code != http.StatusUnauthorized {
			t.Fatalf("want 401 got %d: %s", w.Code, w.Body.String())
		}
	})

	t.Run("mint list revoke", func(t *testing.T) {
		// Mint returns a one-time plaintext key with the fd_ prefix.
		w := do(http.MethodPost, "/api/internal/keys", `{"email":"`+email+`","label":"laptop"}`)
		if w.Code != http.StatusOK {
			t.Fatalf("mint: want 200 got %d: %s", w.Code, w.Body.String())
		}
		var minted struct {
			Key string `json:"key"`
		}
		if err := json.Unmarshal(w.Body.Bytes(), &minted); err != nil {
			t.Fatal(err)
		}
		if !strings.HasPrefix(minted.Key, "fd_") {
			t.Fatalf("mint: key missing fd_ prefix: %q", minted.Key)
		}

		// List shows exactly the one key, not yet revoked.
		w = do(http.MethodGet, "/api/internal/keys?email="+email, "")
		if w.Code != http.StatusOK {
			t.Fatalf("list: want 200 got %d: %s", w.Code, w.Body.String())
		}
		var keys []repositories.APIKeyInfo
		if err := json.Unmarshal(w.Body.Bytes(), &keys); err != nil {
			t.Fatal(err)
		}
		if len(keys) != 1 {
			t.Fatalf("list: want 1 key got %d", len(keys))
		}
		if keys[0].RevokedAt != nil {
			t.Fatalf("list: key should not be revoked yet")
		}

		// Revoke returns 204 with no body.
		w = do(http.MethodPost, "/api/internal/keys/revoke", `{"email":"`+email+`","key_hash":"`+keys[0].KeyHash+`"}`)
		if w.Code != http.StatusNoContent {
			t.Fatalf("revoke: want 204 got %d: %s", w.Code, w.Body.String())
		}

		// List now shows the key as revoked.
		w = do(http.MethodGet, "/api/internal/keys?email="+email, "")
		if w.Code != http.StatusOK {
			t.Fatalf("list-after: want 200 got %d: %s", w.Code, w.Body.String())
		}
		if err := json.Unmarshal(w.Body.Bytes(), &keys); err != nil {
			t.Fatal(err)
		}
		if len(keys) != 1 || keys[0].RevokedAt == nil {
			t.Fatalf("list-after: want 1 revoked key, got %d rows revoked=%v", len(keys), keys[0].RevokedAt)
		}
	})
}
