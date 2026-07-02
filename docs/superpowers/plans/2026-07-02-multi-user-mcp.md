# Field Desk Multi-User MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hosted, per-user-authenticated MCP server at `api.peon.works/mcp` where every Field Desk account queries its own preference data (ratings, saved gems, taste profile, personalized recommendations) plus the shared gem directory; admin accounts also get scraping health. Closes the open `/api` GETs on the way.

**Architecture:** MCP Go SDK `StreamableHTTPHandler` mounted inside the existing Gin API (GKE pod, Cloud SQL sidecar — no local credentials). Personal API keys (`fd_…`, SHA-256-hashed at rest) resolve to `frontend.users` rows; every tool reads the user from request context. Web login moves from the `AUTH_USERS` env var to `frontend.users` via a Go internal endpoint; the web's own API calls move to a session-checked Next.js proxy.

**Tech Stack:** Go 1.25 / Gin / GORM raw SQL, `github.com/modelcontextprotocol/go-sdk` v1.6.1, `golang.org/x/crypto/bcrypt`, `golang.org/x/time/rate`; Next.js (custom fork — see `web/AGENTS.md`), NextAuth v5 Credentials; migration SQL applied by `agf.db.migrate.apply_migrations`.

**Spec:** `docs/superpowers/specs/2026-07-02-multi-user-mcp-design.md` — read it first.

## Global Constraints

- Module path: `github.com/edaslakavicius/emerging-app-finder/backend`. DB handle is the global `db.DB() *gorm.DB`; reads wrap `db.WithReadTx`, writes `db.WithWriteTx`; raw SQL with `?` placeholders (GORM rewrites to `$n`). NO GORM AutoMigrate — schema is Python-owned.
- JSON errors on REST endpoints: `helpers.Detail(ctx, code, "msg")` → `{"detail":"msg"}`. Plain, professional copy everywhere (CLAUDE.md product voice). Product nouns: **Field Desk**, **GemScore**, **cohort**.
- Go integration tests: package `backend/internal/integration` pattern — `TestMain` skips unless `AGF_IT_DB_URL` is set. Test DB (schema pre-applied): `postgresql://agf:agf@localhost:55433/agf_it`. Python suite env: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`.
- Verification standard: ZERO failing tests in the FULL Go suite (`go test ./...` in backend/ with AGF_IT_DB_URL set, run BARE — never piped) and `go vet ./...`. Web: `npm run lint && npx tsc --noEmit` in web/.
- Commit after each task with explicit paths (`git add <paths>` — NEVER `git add -A`). All work on `main`.
- Migration number: **145** (144 is taken by `creatives_ad_id_index`).
- MCP SDK signatures in this plan were written from v1.6.1 docs; they are illustrative until compiled. First Go task using the SDK MUST run `go doc github.com/modelcontextprotocol/go-sdk/mcp.NewServer`, `.AddTool`, `.NewStreamableHTTPHandler` and reconcile.
- Key format: `fd_` + 43 base62 chars (32 random bytes). At rest: SHA-256 hex. Plaintext shown exactly once.

---

### Task 1: Migration 145 — `frontend.users` + `frontend.api_keys`

**Files:**
- Create: `db/schema/145_users_api_keys.sql`

**Interfaces:**
- Produces: tables `frontend.users(email, password_hash, name, role, created_at)` and `frontend.api_keys(key_hash, user_email, label, created_at, last_used_at, revoked_at)` — all later Go SQL depends on these exact names.

- [ ] **Step 1: Write the migration**

```sql
-- Accounts + personal API keys for the hosted Field Desk MCP.
-- Web login reads frontend.users (replaces the AUTH_USERS env seed).
-- api_keys.key_hash = SHA-256 hex of the full plaintext key ("fd_..."), which is
-- shown exactly once at mint time and never stored.
CREATE TABLE IF NOT EXISTS frontend.users (
    email         TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,               -- bcrypt
    name          TEXT,
    role          TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS frontend.api_keys (
    key_hash     TEXT PRIMARY KEY,
    user_email   TEXT NOT NULL REFERENCES frontend.users(email) ON DELETE CASCADE,
    label        TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON frontend.api_keys (user_email);
```

- [ ] **Step 2: Apply to the test DB and verify**

```bash
cd /Users/edaslakavicius/Projects/emerging-app-finder
AGF_DATABASE_URL="postgresql://agf:agf@localhost:55433/agf_it" PYTHONPATH=src \
  .venv/bin/python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; print('applied:', apply_migrations(get_engine()))"
AGF_DATABASE_URL="postgresql://agf:agf@localhost:55433/agf_it" PYTHONPATH=src \
  .venv/bin/python -c "from agf.db.engine import get_engine; from sqlalchemy import text; e=get_engine();
import sys
with e.connect() as c: print(c.execute(text(\"select table_name from information_schema.tables where table_schema='frontend' and table_name in ('users','api_keys') order by 1\")).all())"
```
Expected: `applied:` includes 145; second command prints both table names. If the test DB DSN is wrong, find the right one via `docker ps` (container `agf_test_pg`) before proceeding — do NOT guess.

- [ ] **Step 3: Run the full Python suite (migration must not break it)**

Run bare: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it .venv/bin/python -m pytest -q`
Expected: 0 failed (skips are not passes — check the summary line).

- [ ] **Step 4: Commit**

```bash
git add db/schema/145_users_api_keys.sql
git commit -m "feat(db): migration 145 - frontend.users + frontend.api_keys"
```

---

### Task 2: Go users/keys repository

**Files:**
- Create: `backend/repositories/users_repo.go`
- Test: `backend/internal/integration/users_repo_test.go`

**Interfaces:**
- Consumes: Task 1 tables; `db.WithReadTx` / `db.WithWriteTx` from `backend/db`.
- Produces (later tasks call these exact signatures):
  - `type KeyUser struct { Email string; Role string }`
  - `func HashAPIKey(plaintext string) string` — SHA-256 hex
  - `func NewAPIKeyPlaintext() (string, error)` — `fd_` + base62(32 bytes)
  - `func LookupAPIKey(ctx context.Context, g *gorm.DB, keyHash string) (*KeyUser, error)` — nil,nil when unknown/revoked
  - `func TouchAPIKey(g *gorm.DB, keyHash string)` — best-effort async update of last_used_at
  - `func CreateAPIKey(ctx context.Context, g *gorm.DB, email, label string) (plaintext string, err error)`
  - `type APIKeyInfo struct { KeyHash, Label string; CreatedAt time.Time; LastUsedAt, RevokedAt *time.Time }`
  - `func ListAPIKeys(ctx context.Context, g *gorm.DB, email string) ([]APIKeyInfo, error)`
  - `func RevokeAPIKey(ctx context.Context, g *gorm.DB, email, keyHash string) error` — only the owner's key
  - `func VerifyLogin(ctx context.Context, g *gorm.DB, email, password string) (*KeyUser, string, error)` — returns user, display name; nil when bad credentials (bcrypt compare; also bcrypt-compare against a dummy hash when the user is missing, to keep timing flat)
  - `func UpsertUser(ctx context.Context, g *gorm.DB, email, passwordHash, name, role string) error` (used by seeding/tests)

- [ ] **Step 1: Write failing integration tests**

`backend/internal/integration/users_repo_test.go` (same package/TestMain as the existing integration test):

```go
package integration

import (
	"context"
	"testing"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"golang.org/x/crypto/bcrypt"
)

func TestAPIKeyLifecycle(t *testing.T) {
	ctx := context.Background()
	g := db.DB()
	hash, _ := bcrypt.GenerateFromPassword([]byte("pw-test"), bcrypt.DefaultCost)
	if err := repositories.UpsertUser(ctx, g, "it-user@test.local", string(hash), "IT User", "member"); err != nil {
		t.Fatal(err)
	}

	plain, err := repositories.CreateAPIKey(ctx, g, "it-user@test.local", "laptop")
	if err != nil || len(plain) < 20 || plain[:3] != "fd_" {
		t.Fatalf("bad key: %q err=%v", plain, err)
	}

	ku, err := repositories.LookupAPIKey(ctx, g, repositories.HashAPIKey(plain))
	if err != nil || ku == nil || ku.Email != "it-user@test.local" || ku.Role != "member" {
		t.Fatalf("lookup: %+v err=%v", ku, err)
	}
	if ku, _ := repositories.LookupAPIKey(ctx, g, repositories.HashAPIKey("fd_wrong")); ku != nil {
		t.Fatal("unknown key must resolve to nil")
	}

	keys, err := repositories.ListAPIKeys(ctx, g, "it-user@test.local")
	if err != nil || len(keys) != 1 || keys[0].Label != "laptop" {
		t.Fatalf("list: %+v err=%v", keys, err)
	}

	if err := repositories.RevokeAPIKey(ctx, g, "it-user@test.local", keys[0].KeyHash); err != nil {
		t.Fatal(err)
	}
	if ku, _ := repositories.LookupAPIKey(ctx, g, repositories.HashAPIKey(plain)); ku != nil {
		t.Fatal("revoked key must resolve to nil")
	}
}

func TestVerifyLogin(t *testing.T) {
	ctx := context.Background()
	g := db.DB()
	hash, _ := bcrypt.GenerateFromPassword([]byte("right-pw"), bcrypt.DefaultCost)
	if err := repositories.UpsertUser(ctx, g, "login@test.local", string(hash), "Login", "admin"); err != nil {
		t.Fatal(err)
	}
	ku, name, err := repositories.VerifyLogin(ctx, g, "login@test.local", "right-pw")
	if err != nil || ku == nil || ku.Role != "admin" || name != "Login" {
		t.Fatalf("good login failed: %+v %q %v", ku, name, err)
	}
	if ku, _, _ := repositories.VerifyLogin(ctx, g, "login@test.local", "wrong"); ku != nil {
		t.Fatal("wrong password must fail")
	}
	if ku, _, _ := repositories.VerifyLogin(ctx, g, "ghost@test.local", "x"); ku != nil {
		t.Fatal("unknown user must fail")
	}
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./internal/integration/ -run 'TestAPIKeyLifecycle|TestVerifyLogin' -v`
Expected: FAIL — undefined `repositories.UpsertUser` etc.

- [ ] **Step 3: Implement `backend/repositories/users_repo.go`**

```go
// Package repositories: accounts + personal API keys (frontend.users/api_keys).
package repositories

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"math/big"
	"time"

	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
)

type KeyUser struct {
	Email string
	Role  string
}

type APIKeyInfo struct {
	KeyHash    string     `json:"key_hash"`
	Label      string     `json:"label"`
	CreatedAt  time.Time  `json:"created_at"`
	LastUsedAt *time.Time `json:"last_used_at"`
	RevokedAt  *time.Time `json:"revoked_at"`
}

const base62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

func HashAPIKey(plaintext string) string {
	sum := sha256.Sum256([]byte(plaintext))
	return hex.EncodeToString(sum[:])
}

func NewAPIKeyPlaintext() (string, error) {
	out := make([]byte, 43)
	for i := range out {
		n, err := rand.Int(rand.Reader, big.NewInt(62))
		if err != nil {
			return "", err
		}
		out[i] = base62[n.Int64()]
	}
	return "fd_" + string(out), nil
}

func LookupAPIKey(ctx context.Context, g *gorm.DB, keyHash string) (*KeyUser, error) {
	var rows []KeyUser
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT u.email, u.role
		               FROM frontend.api_keys k
		               JOIN frontend.users u ON u.email = k.user_email
		               WHERE k.key_hash = ? AND k.revoked_at IS NULL`, keyHash).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

// TouchAPIKey updates last_used_at in the background; failures are ignored on
// purpose (liveness metadata must never fail a request).
func TouchAPIKey(g *gorm.DB, keyHash string) {
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
			return tx.Exec(`UPDATE frontend.api_keys SET last_used_at = now() WHERE key_hash = ?`, keyHash).Error
		})
	}()
}

func CreateAPIKey(ctx context.Context, g *gorm.DB, email, label string) (string, error) {
	plain, err := NewAPIKeyPlaintext()
	if err != nil {
		return "", err
	}
	err = db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(`INSERT INTO frontend.api_keys (key_hash, user_email, label) VALUES (?, ?, ?)`,
			HashAPIKey(plain), email, label).Error
	})
	if err != nil {
		return "", err
	}
	return plain, nil
}

func ListAPIKeys(ctx context.Context, g *gorm.DB, email string) ([]APIKeyInfo, error) {
	var rows []APIKeyInfo
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT key_hash, label, created_at, last_used_at, revoked_at
		               FROM frontend.api_keys WHERE user_email = ? ORDER BY created_at DESC`, email).Scan(&rows).Error
	})
	return rows, err
}

func RevokeAPIKey(ctx context.Context, g *gorm.DB, email, keyHash string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(`UPDATE frontend.api_keys SET revoked_at = now()
		                WHERE key_hash = ? AND user_email = ? AND revoked_at IS NULL`, keyHash, email).Error
	})
}

// dummyHash keeps VerifyLogin's timing flat when the user does not exist.
var dummyHash, _ = bcrypt.GenerateFromPassword([]byte("fielddesk-dummy"), bcrypt.DefaultCost)

func VerifyLogin(ctx context.Context, g *gorm.DB, email, password string) (*KeyUser, string, error) {
	var rows []struct {
		Email        string
		Role         string
		Name         *string
		PasswordHash string
	}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT email, role, name, password_hash FROM frontend.users
		               WHERE lower(email) = lower(?)`, email).Scan(&rows).Error
	})
	if err != nil {
		return nil, "", err
	}
	if len(rows) == 0 {
		_ = bcrypt.CompareHashAndPassword(dummyHash, []byte(password))
		return nil, "", nil
	}
	if bcrypt.CompareHashAndPassword([]byte(rows[0].PasswordHash), []byte(password)) != nil {
		return nil, "", nil
	}
	name := rows[0].Email
	if rows[0].Name != nil && *rows[0].Name != "" {
		name = *rows[0].Name
	}
	return &KeyUser{Email: rows[0].Email, Role: rows[0].Role}, name, nil
}

func UpsertUser(ctx context.Context, g *gorm.DB, email, passwordHash, name, role string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(`INSERT INTO frontend.users (email, password_hash, name, role)
		                VALUES (?, ?, ?, ?)
		                ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash,
		                  name = EXCLUDED.name, role = EXCLUDED.role`,
			email, passwordHash, name, role).Error
	})
}
```

Add deps: `cd backend && go get golang.org/x/crypto@latest && go mod tidy`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./internal/integration/ -run 'TestAPIKeyLifecycle|TestVerifyLogin' -v`
Expected: PASS. Then `go vet ./...`.

- [ ] **Step 5: Commit**

```bash
git add backend/repositories/users_repo.go backend/internal/integration/users_repo_test.go backend/go.mod backend/go.sum
git commit -m "feat(api): users/api-keys repository (hash, mint, lookup, revoke, login verify)"
```

---

### Task 3: Bearer-key middleware + per-key rate limit

**Files:**
- Create: `backend/middlewares/keyauth.go`, `backend/middlewares/ratelimit.go`
- Test: `backend/middlewares/keyauth_test.go` (pure parts), `backend/internal/integration/keyauth_test.go` (DB-backed)

**Interfaces:**
- Consumes: `repositories.LookupAPIKey`, `HashAPIKey`, `TouchAPIKey`; existing `middlewares.APITokenEnv` const (`AGF_API_TOKEN`).
- Produces:
  - `type AuthedUser struct { Email string; Role string }`
  - `func UserFrom(ctx context.Context) (AuthedUser, bool)` — reads the value set by the middleware from a plain context (works inside MCP tool handlers)
  - `func KeyAuth(acceptInternal bool) gin.HandlerFunc` — resolves `Authorization: Bearer fd_…` via api_keys; when `acceptInternal` and the bearer equals `AGF_API_TOKEN`, identity comes from `X-User-Email` (role looked up from users; missing header → 401). Sets the user into BOTH `gin.Context` and `ctx.Request`'s context. 401 `{"detail":"invalid or missing API key."}` otherwise.
  - `func PerKeyRateLimit(perMin int) gin.HandlerFunc` — token bucket per bearer token string; 429 `{"detail":"rate limit exceeded. retry in a minute."}`.

- [ ] **Step 1: Write failing tests**

`backend/middlewares/keyauth_test.go`:

```go
package middlewares

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestUserFromRoundTrip(t *testing.T) {
	ctx := WithUser(context.Background(), AuthedUser{Email: "a@b.c", Role: "member"})
	u, ok := UserFrom(ctx)
	if !ok || u.Email != "a@b.c" || u.Role != "member" {
		t.Fatalf("got %+v ok=%v", u, ok)
	}
	if _, ok := UserFrom(context.Background()); ok {
		t.Fatal("empty context must not carry a user")
	}
}

func TestKeyAuthRejectsMissingHeader(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/x", KeyAuth(false), func(c *gin.Context) { c.Status(200) })
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/x", nil)
	r.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("want 401, got %d", w.Code)
	}
}

func TestPerKeyRateLimit(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/x", PerKeyRateLimit(2), func(c *gin.Context) { c.Status(200) })
	codes := []int{}
	for i := 0; i < 4; i++ {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/x", nil)
		req.Header.Set("Authorization", "Bearer fd_same")
		r.ServeHTTP(w, req)
		codes = append(codes, w.Code)
	}
	if codes[0] != 200 || codes[1] != 200 || codes[3] != 429 {
		t.Fatalf("codes=%v", codes)
	}
}
```

`backend/internal/integration/keyauth_test.go`:

```go
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
```

- [ ] **Step 2: Run to verify failure** — `cd backend && go test ./middlewares/ -run 'TestUserFrom|TestKeyAuthRejects|TestPerKey' -v` → FAIL (undefined symbols).

- [ ] **Step 3: Implement**

`backend/middlewares/keyauth.go`:

```go
package middlewares

import (
	"context"
	"crypto/subtle"
	"net/http"
	"os"
	"strings"

	"github.com/gin-gonic/gin"

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
```

(Add `"gorm.io/gorm"` to imports — the implementer must make it compile; run `go build ./...`.)

`backend/middlewares/ratelimit.go`:

```go
package middlewares

import (
	"net/http"
	"sync"

	"github.com/gin-gonic/gin"
	"golang.org/x/time/rate"

	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
)

// PerKeyRateLimit is a per-bearer-token token bucket (single-pod deployment,
// in-memory is sufficient; see spec "Out of scope" for multi-pod).
func PerKeyRateLimit(perMin int) gin.HandlerFunc {
	var mu sync.Mutex
	buckets := map[string]*rate.Limiter{}
	return func(c *gin.Context) {
		key := c.GetHeader("Authorization")
		mu.Lock()
		lim, ok := buckets[key]
		if !ok {
			lim = rate.NewLimiter(rate.Limit(float64(perMin)/60.0), perMin/2)
			buckets[key] = lim
		}
		mu.Unlock()
		if !lim.Allow() {
			helpers.Detail(c, http.StatusTooManyRequests, "rate limit exceeded. retry in a minute.")
			c.Abort()
			return
		}
		c.Next()
	}
}
```

Add dep: `cd backend && go get golang.org/x/time@latest && go mod tidy`.

- [ ] **Step 4: Run all Task 3 tests + vet**

`cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./middlewares/ ./internal/integration/ -run 'TestUserFrom|TestKeyAuth|TestPerKey' -v && go vet ./...`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/middlewares/keyauth.go backend/middlewares/ratelimit.go backend/middlewares/keyauth_test.go backend/internal/integration/keyauth_test.go backend/go.mod backend/go.sum
git commit -m "feat(api): bearer API-key middleware + per-key rate limit"
```

---

### Task 4: Internal auth + key-management endpoints (for the web app)

**Files:**
- Create: `backend/controllers/internal_auth_controller.go`
- Modify: `backend/routers/routers.go` (add `/api/internal` group)
- Test: `backend/internal/integration/internal_auth_test.go`

**Interfaces:**
- Consumes: Task 2 repo functions; existing `middlewares.RequireToken()` (static internal token — these routes are ONLY for the Next.js server).
- Produces routes (all under `/api/internal`, guarded by `RequireToken()`):
  - `POST /api/internal/login` body `{"email","password"}` → 200 `{"email","name","role"}` | 401
  - `GET  /api/internal/keys?email=…` → 200 `[APIKeyInfo…]`
  - `POST /api/internal/keys` body `{"email","label"}` → 200 `{"key":"fd_…"}` (plaintext, once)
  - `POST /api/internal/keys/revoke` body `{"email","key_hash"}` → 204

- [ ] **Step 1: Write failing integration test** — `backend/internal/integration/internal_auth_test.go`: build a router with the new controller mounted (mirror the existing integration router setup), `t.Setenv(middlewares.APITokenEnv, "it-internal")`, then: login with seeded user (200 + role), wrong password (401), mint key (200, `fd_` prefix), list (1 row), revoke (204), list shows revoked_at set. Follow the httptest pattern from Task 3's integration test — full request/assert code, no sharing of state between subtests except the seeded user.

- [ ] **Step 2: Run to verify failure** — undefined `controllers.NewInternalAuthController`.

- [ ] **Step 3: Implement controller + mount**

`backend/controllers/internal_auth_controller.go`:

```go
package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

type InternalAuthController struct{}

func NewInternalAuthController() *InternalAuthController { return &InternalAuthController{} }

func (ia *InternalAuthController) Login(ctx *gin.Context) {
	var body struct{ Email, Password string }
	if err := ctx.ShouldBindJSON(&body); err != nil || body.Email == "" || body.Password == "" {
		helpers.Detail(ctx, http.StatusBadRequest, "email and password are required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	ku, name, err := repositories.VerifyLogin(rctx, db.DB(), body.Email, body.Password)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if ku == nil {
		helpers.Detail(ctx, http.StatusUnauthorized, "wrong email or password.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"email": ku.Email, "name": name, "role": ku.Role})
}

func (ia *InternalAuthController) ListKeys(ctx *gin.Context) {
	email := ctx.Query("email")
	if email == "" {
		helpers.Detail(ctx, http.StatusBadRequest, "email is required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	keys, err := repositories.ListAPIKeys(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.JSON(http.StatusOK, keys)
}

func (ia *InternalAuthController) MintKey(ctx *gin.Context) {
	var body struct{ Email, Label string }
	if err := ctx.ShouldBindJSON(&body); err != nil || body.Email == "" {
		helpers.Detail(ctx, http.StatusBadRequest, "email is required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	plain, err := repositories.CreateAPIKey(rctx, db.DB(), body.Email, body.Label)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"key": plain})
}

func (ia *InternalAuthController) RevokeKey(ctx *gin.Context) {
	var body struct {
		Email   string `json:"email"`
		KeyHash string `json:"key_hash"`
	}
	if err := ctx.ShouldBindJSON(&body); err != nil || body.Email == "" || body.KeyHash == "" {
		helpers.Detail(ctx, http.StatusBadRequest, "email and key_hash are required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.RevokeAPIKey(rctx, db.DB(), body.Email, body.KeyHash); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.Status(http.StatusNoContent)
}
```

In `backend/routers/routers.go` inside `Init`, after the existing `api := r.Group("/api")` block:

```go
ia := controllers.NewInternalAuthController()
internal := r.Group("/api/internal", middlewares.RequireToken())
internal.POST("/login", ia.Login)
internal.GET("/keys", ia.ListKeys)
internal.POST("/keys", ia.MintKey)
internal.POST("/keys/revoke", ia.RevokeKey)
```

- [ ] **Step 4: Run tests + vet** — Task 4 tests PASS; `go build ./... && go vet ./...` clean.

- [ ] **Step 5: Commit**

```bash
git add backend/controllers/internal_auth_controller.go backend/routers/routers.go backend/internal/integration/internal_auth_test.go
git commit -m "feat(api): internal login + API-key management endpoints for the web app"
```

---

### Task 5: Member data repo — my_ratings, my_saved_gems, my_taste_profile

**Files:**
- Create: `backend/repositories/member_repo.go`
- Test: `backend/internal/integration/member_repo_test.go`

**Interfaces:**
- Consumes: `frontend.app_ratings(user_email, advertiser_id, rating, comment, features jsonb, rated_at)`, `frontend.saved_gems(user_email, advertiser_id, page_name, app_name, cohort, gem_score, tags, note, stage, verdict, confidence, saved_at)`, matview `frontend.emerging_scaling_apps` (cohort per advertiser).
- Produces:
  - `type MyRating struct { AdvertiserID int64; AppName, Cohort, Rating, Comment string; RatedAt time.Time }` (JSON tags snake_case)
  - `func MyRatings(ctx, g, email string, rating string, limit, offset int) ([]MyRating, int64, error)` — `rating` empty = all; joins the matview for app_name/cohort (LEFT JOIN — rated apps may have left the feed; fall back to `''`)
  - `type MySavedGem struct { AdvertiserID int64; PageName, AppName, Cohort string; GemScore float64; Stage string; Verdict *string; Tags, Note string; SavedAt time.Time }`
  - `func MySavedGems(ctx, g, email string) ([]MySavedGem, error)`
  - `type TasteProfile struct { Counts map[string]int64; TopLikedCohorts []CohortCount; LikeSignals []SignalAvg }`
  - `type CohortCount struct { Cohort string; Likes int64 }`
  - `type SignalAvg struct { Signal string; LikeAvg, DislikeAvg float64 }`
  - `func MyTasteProfile(ctx, g, email string) (*TasteProfile, error)`

- [ ] **Step 1: Failing integration test** — seed one user with 3 ratings (2 like w/ features `{"demand":0.9,"has_funnel":1}` and cohorts via existing seeded matview advertisers — reuse the ids the existing integration `seed()` inserts; read that seed first) + 1 saved gem; assert `MyRatings` filters by rating + respects limit, `MySavedGems` returns the row, `MyTasteProfile` counts `{"like":2,"dislike":1}` and `LikeSignals` contains `demand` with LikeAvg 0.9. Assert another user's email returns empty (scoping).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Core SQL (follow `shortlist_repo.go` structure — `db.WithReadTx`, `tx.Raw(...).Scan`):

```sql
-- MyRatings
SELECT r.advertiser_id,
       COALESCE(mv.app_name, '')  AS app_name,
       COALESCE(mv.cohort, '')    AS cohort,
       r.rating, r.comment, r.rated_at
FROM frontend.app_ratings r
LEFT JOIN frontend.emerging_scaling_apps mv ON mv.advertiser_id = r.advertiser_id
WHERE r.user_email = ? AND (? = '' OR r.rating = ?)
ORDER BY r.rated_at DESC LIMIT ? OFFSET ?

-- MyTasteProfile: counts
SELECT rating, count(*) AS n FROM frontend.app_ratings WHERE user_email = ? GROUP BY 1

-- top liked cohorts
SELECT COALESCE(mv.cohort, 'unknown') AS cohort, count(*) AS likes
FROM frontend.app_ratings r
LEFT JOIN frontend.emerging_scaling_apps mv ON mv.advertiser_id = r.advertiser_id
WHERE r.user_email = ? AND r.rating = 'like'
GROUP BY 1 ORDER BY 2 DESC LIMIT 8

-- like vs dislike signal averages from the features snapshot (numeric keys only)
SELECT f.key AS signal,
       avg(f.value::float) FILTER (WHERE r.rating = 'like')    AS like_avg,
       avg(f.value::float) FILTER (WHERE r.rating = 'dislike') AS dislike_avg
FROM frontend.app_ratings r, jsonb_each_text(r.features) f
WHERE r.user_email = ? AND f.value ~ '^-?[0-9.]+$'
GROUP BY 1 HAVING count(*) FILTER (WHERE r.rating = 'like') > 0
ORDER BY 2 DESC NULLS LAST LIMIT 12
```

- [ ] **Step 4: Tests PASS + `go vet ./...`.**

- [ ] **Step 5: Commit** — `git add backend/repositories/member_repo.go backend/internal/integration/member_repo_test.go && git commit -m "feat(api): member repo - my ratings, saved gems, taste profile"`

---

### Task 6: Recommendations — embeddings + centroid ranking

**Files:**
- Create: `backend/internal/reco/reco.go` (pure math), `backend/repositories/embeddings_repo.go`
- Test: `backend/internal/reco/reco_test.go`, `backend/internal/integration/reco_test.go`

**Interfaces:**
- Consumes: `frontend.app_embeddings(advertiser_id BIGINT PK, embedding REAL[], dim INT)` (valid rows: `dim=768`), `frontend.emerging_scaling_products` view, `frontend.app_ratings`.
- Produces:
  - `func ParsePgFloatArray(s string) ([]float32, error)` — parses `{0.1,-0.2,…}` (embeddings are selected as `embedding::text`; GORM cannot scan `REAL[]` directly)
  - `func LikedCentroid(vecs [][]float32) []float32` — mean then L2-normalize; nil for empty input
  - `func RankByCosine(centroid []float32, items []RecoItem) []RecoItem` — embedded items sorted by cosine desc, then unembedded (`Unscored=true`) in GemScore order
  - `type RecoItem struct { AdvertiserID int64; AppName, Cohort string; GemScore float64; Similarity *float64; Unscored bool; vec []float32 }`
  - `func RecommendedGems(ctx, g, email string, limit int) ([]RecoItem, bool /*personalized*/, error)` — false when the user has no likes (GemScore order, payload says so)

- [ ] **Step 1: Failing pure tests** (`reco_test.go`): ParsePgFloatArray round-trip + malformed input error; LikedCentroid of `{1,0}`,`{0,1}` = normalized `{.707,.707}`; RankByCosine puts the aligned vector first and unembedded items last with `Unscored=true`.

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement.** Embeddings SQL:

```sql
-- caller's liked embeddings
SELECT e.embedding::text AS emb
FROM frontend.app_ratings r
JOIN frontend.app_embeddings e ON e.advertiser_id = r.advertiser_id AND e.dim = 768
WHERE r.user_email = ? AND r.rating = 'like'

-- feed with optional embedding
SELECT p.advertiser_id, COALESCE(p.app_name, p.page_name) AS app_name,
       p.cohort, p.gem_score, e.embedding::text AS emb
FROM frontend.emerging_scaling_products p
LEFT JOIN frontend.app_embeddings e ON e.advertiser_id = p.advertiser_id AND e.dim = 768
ORDER BY p.gem_score DESC
```

Pure math is straight loops (dot product on unit vectors = cosine; centroid = element-wise mean, L2-normalize). No numeric libs.

- [ ] **Step 4: Integration test** — seed 2 embeddings (one liked by the test user, one on a feed advertiser) + verify: personalized=true, similar app first, an embedding-less feed row is flagged unscored; a like-less user gets personalized=false. PASS + vet.

- [ ] **Step 5: Commit** — `git add backend/internal/reco/ backend/repositories/embeddings_repo.go backend/internal/integration/reco_test.go && git commit -m "feat(api): recommended gems - liked-centroid cosine ranking"`

---

### Task 7: Scraping-health port (admin tool data)

**Files:**
- Create: `backend/repositories/scrapehealth_repo.go` (SQL), `backend/internal/diversity/diversity.go` (pure math)
- Test: `backend/internal/diversity/diversity_test.go`, `backend/internal/integration/scrapehealth_test.go`

**Interfaces:**
- Consumes: `discovery.candidates(first_seen_at, status, store, page_name, source_keyword, kept_advertiser_id)`, `discovery.candidate_embeddings(candidate_id, embedding float4[], dim)` (valid = `dim=768`), `discovery.{sweep_runs,flywheel_runs,funnel_sweep_runs}(started_at)`, `frontend.emerging_scaling_products(advertiser_id, gem_score)`. Reference implementation: `src/agf_mcp/metrics.py` (port the SQL and shape verbatim; JSON field names identical so existing familiarity carries over).
- Produces:
  - `func EnginePulse(ctx, g) (map[string]any, error)` — status healthy/wedged (no candidate in 6h OR no sweep in 24h), newest candidate + age, triage backlog, per-run-table latest/24h/7d
  - `func DiscoveryRate(ctx, g, days int) (map[string]any, error)` — days clamped 1..120, default 14
  - `func IdeaDiversity(ctx, g) (map[string]any, error)` — coverage, effective concepts, concentration (corpus+gems, GemScore gate 60), concept groups (top 12, most-central member as representative), novelty (newest 25% NN-distance vs older baseline; verdict broadening/steady/converging at ±10%), platform split
  - diversity package: `func KMeans(vecs [][]float32, k int, seed int64) []int` (k-means++ init with `math/rand.New(rand.NewSource(seed))`, Lloyd ≤50 iters), `func EffectiveCount(sizes []int) float64` (exp of Shannon entropy), `func Concentration(sizes []int) (top1, top5 float64)`, `func Novelty(vecs [][]float32, ageDays []float64) map[string]any`

- [ ] **Step 1: Failing pure tests** — KMeans on two obvious 2-D blobs separates them deterministically (same labels across two runs with seed 0); EffectiveCount([10,10]) ≈ 2.0, EffectiveCount([20]) = 1.0; Novelty with recent vectors orthogonal to old ones verdicts "broadening".

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement.** Port each query from `metrics.py:35-109` and `:125-147` 1:1 (same aliases). Embeddings selected as `embedding::text`, parsed with `reco.ParsePgFloatArray`. k defaults to `min(30, max(2, n/20))`. Return `map[string]any` mirroring the Python dict shape exactly (keys: `status`, `newest_candidate`, `triage_backlog_new`, `runs`, `per_day`, `kept_rate`, `effective_concepts`, `concentration`, `concept_groups`, `novelty`, `platform_split`, `embedding_coverage`).

- [ ] **Step 4: Integration smoke** — seed a couple of candidates + one run row; assert EnginePulse returns a status and the seeded backlog count; DiscoveryRate window math; IdeaDiversity returns the "not enough valid embeddings" note with <2 embeddings (mirror `metrics.py:156-158`). PASS + vet.

- [ ] **Step 5: Commit** — `git add backend/repositories/scrapehealth_repo.go backend/internal/diversity/ backend/internal/integration/scrapehealth_test.go && git commit -m "feat(api): scraping-health metrics ported from python mcp"`

---

### Task 8: MCP server assembly + `/mcp` mount

**Files:**
- Create: `backend/mcpserver/mcpserver.go`
- Modify: `backend/routers/routers.go` (mount), `backend/go.mod` (SDK dep)
- Test: `backend/internal/integration/mcp_test.go`

**Interfaces:**
- Consumes: everything from Tasks 3–7; `middlewares.UserFrom(ctx)`.
- Produces: `func mcpserver.Handler() http.Handler` — a streamable-HTTP MCP server named `fielddesk` exposing tools `my_taste_profile`, `my_ratings`, `my_saved_gems`, `recommended_gems`, `search_gems`, `gem_detail`, `scraping_health`. Mounted in `routers.Init`:

```go
mcpHandler := mcpserver.Handler()
r.Any("/mcp", middlewares.KeyAuth(false), middlewares.PerKeyRateLimit(60), gin.WrapH(mcpHandler))
r.Any("/mcp/*path", middlewares.KeyAuth(false), middlewares.PerKeyRateLimit(60), gin.WrapH(mcpHandler))
```

- [ ] **Step 1: Reconcile SDK API (required before writing code)**

```bash
cd backend && go get github.com/modelcontextprotocol/go-sdk@v1.6.1 && go mod tidy
go doc github.com/modelcontextprotocol/go-sdk/mcp.NewServer
go doc github.com/modelcontextprotocol/go-sdk/mcp.AddTool
go doc github.com/modelcontextprotocol/go-sdk/mcp.NewStreamableHTTPHandler
```
Adjust the code below to the real signatures — it is illustrative until it compiles.

- [ ] **Step 2: Failing integration test** — `mcp_test.go`: build the full router (`routers.Init`), seed a member user+key and an admin user+key, then use the SDK's client over an `httptest.Server`:

```go
// sketch — reconcile with go doc:
cli := mcp.NewClient(&mcp.Implementation{Name: "it", Version: "0"}, nil)
tr := &mcp.StreamableClientTransport{Endpoint: ts.URL + "/mcp", HTTPClient: httpClientWithBearer(memberKey)}
sess, err := cli.Connect(ctx, tr, nil)
```
Assertions: (1) `tools/list` as member does NOT include `scraping_health`, admin does (register admin tools on a per-request server — see Step 3); (2) `my_ratings` as member returns only that member's seeded rating; (3) calling `scraping_health` with the member session errors with "requires an admin account"; (4) no/bogus bearer → HTTP 401 before MCP handshake.

- [ ] **Step 3: Implement `backend/mcpserver/mcpserver.go`**

Shape (reconcile signatures):

```go
// Package mcpserver: the hosted Field Desk MCP. Tools read the authenticated
// user from the request context (set by middlewares.KeyAuth) — no tool accepts
// a user parameter.
package mcpserver

func Handler() http.Handler {
	return mcp.NewStreamableHTTPHandler(func(req *http.Request) *mcp.Server {
		u, ok := middlewares.UserFrom(req.Context())
		if !ok {
			return nil // KeyAuth already rejected; defensive
		}
		return buildServer(u)
	}, nil)
}

func buildServer(u middlewares.AuthedUser) *mcp.Server {
	s := mcp.NewServer(&mcp.Implementation{Name: "fielddesk", Version: "1.0.0"}, nil)
	mcp.AddTool(s, &mcp.Tool{Name: "my_taste_profile",
		Description: "Your rating counts, top liked cohorts, and the signals that drive your likes."},
		tasteProfileTool)
	mcp.AddTool(s, &mcp.Tool{Name: "my_ratings",
		Description: "Your app ratings. Filter by rating (like/neutral/dislike)."}, myRatingsTool)
	mcp.AddTool(s, &mcp.Tool{Name: "my_saved_gems",
		Description: "Your saved watchlist with stage, verdict, tags, and notes."}, mySavedGemsTool)
	mcp.AddTool(s, &mcp.Tool{Name: "recommended_gems",
		Description: "The gem feed ranked by similarity to the apps you liked."}, recommendedTool)
	mcp.AddTool(s, &mcp.Tool{Name: "search_gems",
		Description: "Search the gem directory by name, cohort, GemScore, and store."}, searchTool)
	mcp.AddTool(s, &mcp.Tool{Name: "gem_detail",
		Description: "One gem: scores, axes, ads summary, funnel, store links."}, detailTool)
	if u.Role == "admin" {
		mcp.AddTool(s, &mcp.Tool{Name: "scraping_health",
			Description: "Discovery engine health: pulse, discovery rate, idea diversity."}, scrapingHealthTool)
	}
	return s
}
```

Tool handler pattern (typed in/out per SDK; user always from ctx):

```go
type myRatingsIn struct {
	Rating string `json:"rating,omitempty" jsonschema:"like, neutral, or dislike; empty for all"`
	Limit  int    `json:"limit,omitempty"`
	Offset int    `json:"offset,omitempty"`
}

func myRatingsTool(ctx context.Context, req *mcp.CallToolRequest, in myRatingsIn) (*mcp.CallToolResult, any, error) {
	u, ok := middlewares.UserFrom(ctx)
	if !ok {
		return nil, nil, errors.New("not authenticated.")
	}
	if in.Limit <= 0 || in.Limit > 200 {
		in.Limit = 50
	}
	rows, total, err := repositories.MyRatings(ctx, db.DB(), u.Email, in.Rating, in.Limit, in.Offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading your ratings.")
	}
	return nil, map[string]any{"total": total, "ratings": rows}, nil
}
```

Behavior rules baked into handlers: `my_taste_profile` with zero ratings returns `{"note":"No ratings yet — rate apps in the review queue first."}`; `recommended_gems` includes `"personalized": false, "note": "No likes yet — showing GemScore order."` on fallback; `scraping_health` (also defense-in-depth inside the handler) returns error "This tool requires an admin account." for non-admins. `search_gems` calls the existing `repositories.Shortlist`; `gem_detail` calls the existing dossier repo function — find it via `grep -rn "func Gem" backend/repositories/` and reuse; it MUST stay on the matview read (resolves non-representative advertiser ids — regression noted 2026-06-27).

- [ ] **Step 4: Tests PASS** — Task 8 integration tests + FULL suite: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./... && go vet ./...` (bare, read the summary).

- [ ] **Step 5: Commit**

```bash
git add backend/mcpserver/ backend/routers/routers.go backend/internal/integration/mcp_test.go backend/go.mod backend/go.sum
git commit -m "feat(mcp): hosted multi-user MCP at /mcp (7 tools, key auth, admin gating)"
```

---

### Task 9: Close the open `/api` GETs

**Files:**
- Modify: `backend/routers/routers.go`
- Test: extend `backend/internal/integration/internal_auth_test.go` (or new `api_lockdown_test.go`)

**Interfaces:**
- Consumes: `middlewares.KeyAuth(true)` (accepts `fd_` keys AND internal token + `X-User-Email`).
- Produces: every route in the `api := r.Group("/api")` group requires auth. `/healthz`, `/readyz`, `/metrics`, `/api/internal/*` (RequireToken) unchanged.

- [ ] **Step 1: Failing test** — `api_lockdown_test.go`: cookieless/keyless GET `/api/shortlist` → 401; with a valid `fd_` key → 200; with internal token + `X-User-Email` of a seeded user → 200; internal token WITHOUT the header → 401.

- [ ] **Step 2: Verify failure** (currently 200 keyless).

- [ ] **Step 3: Implement** — in `routers.Init`, change the group to `api := r.Group("/api", middlewares.KeyAuth(true))`. Remove now-redundant per-route `RequireToken()` from write routes ONLY if the internal-token path still guards them via KeyAuth(true) — writes must keep requiring the internal token OR a valid key; verify each of the 5 write routes' tests still pass. Existing integration tests that hit `/api/*` without auth will now 401 — update them to send the internal token + `X-User-Email` (add a helper `authedReq(method, path)` in the integration package).

- [ ] **Step 4: FULL Go suite bare + vet — 0 failures.**

- [ ] **Step 5: Commit** — `git add backend/routers/routers.go backend/internal/integration/ && git commit -m "fix(api): require API key or internal token on all /api routes (closes open GETs)"`

---

### Task 10: Web — DB-backed login + session-checked API proxy

**Files:**
- Modify: `web/src/lib/users.ts` (replace env-seed with Go internal login), `web/src/lib/auth.ts` (role in JWT/session), `web/src/lib/api.ts` (base URL → proxy), `web/src/lib/auth.config.ts` (add `/settings` to PROTECTED)
- Create: `web/src/app/backend/[...path]/route.ts` (proxy), `web/src/types/next-auth.d.ts` (session role typing)
- Test: `npm run lint && npx tsc --noEmit`; behavior verified in Task 12's live gate (web has no unit-test rig for auth — the repo's standard is the live e2e)

**Interfaces:**
- Consumes: Task 4 endpoints; server envs `AGF_API_INTERNAL_URL` (e.g. `http://agf-api:8000/api` in-cluster; `http://localhost:8000/api` in dev) + `AGF_API_TOKEN`.
- Produces: `session.user.role: "admin" | "member"`; all browser fetches go to same-origin `/backend/*`.

- [ ] **Step 1: Replace `users.ts`**

```ts
/** Login verification via the Go API (frontend.users). The web server holds
 *  the internal token; the browser never sees it. */
const API = process.env.AGF_API_INTERNAL_URL ?? "http://localhost:8000/api";
const TOKEN = process.env.AGF_API_TOKEN ?? "";

export interface VerifiedUser {
  email: string;
  name: string;
  role: "admin" | "member";
}

export async function verifyLogin(email: string, password: string): Promise<VerifiedUser | null> {
  const res = await fetch(`${API}/internal/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify({ email, password }),
    cache: "no-store",
  });
  if (!res.ok) return null;
  return (await res.json()) as VerifiedUser;
}
```

- [ ] **Step 2: Update `auth.ts`** — `authorize` calls `verifyLogin`; return `{ id: u.email, email: u.email, name: u.name, role: u.role }`. Add callbacks:

```ts
callbacks: {
  ...authConfig.callbacks,
  jwt({ token, user }) {
    if (user) token.role = (user as { role?: string }).role ?? "member";
    return token;
  },
  session({ session, token }) {
    if (session.user) (session.user as { role?: string }).role = token.role as string;
    return session;
  },
},
```

`web/src/types/next-auth.d.ts`:

```ts
import "next-auth";
declare module "next-auth" {
  interface User { role?: "admin" | "member" }
  interface Session { user?: { email?: string | null; name?: string | null; role?: "admin" | "member" } }
}
declare module "next-auth/jwt" {
  interface JWT { role?: "admin" | "member" }
}
```

- [ ] **Step 3: Proxy route `web/src/app/backend/[...path]/route.ts`**

```ts
import { auth } from "@/lib/auth";
import { NextRequest, NextResponse } from "next/server";

const API = process.env.AGF_API_INTERNAL_URL ?? "http://localhost:8000/api";
const TOKEN = process.env.AGF_API_TOKEN ?? "";

async function proxy(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const session = await auth();
  const email = session?.user?.email;
  if (!email) return NextResponse.json({ detail: "not signed in." }, { status: 401 });
  const { path } = await params;
  const url = `${API}/${path.join("/")}${req.nextUrl.search}`;
  const res = await fetch(url, {
    method: req.method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
      "X-User-Email": email,
    },
    body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.text(),
    cache: "no-store",
  });
  const body = await res.text();
  return new NextResponse(body, {
    status: res.status,
    headers: {
      "Content-Type": res.headers.get("Content-Type") ?? "application/json",
      ...(res.headers.get("X-Total-Count") ? { "X-Total-Count": res.headers.get("X-Total-Count")! } : {}),
    },
  });
}

export { proxy as GET, proxy as POST, proxy as DELETE };
```

(Check the fork's route-handler `params` convention against an existing dynamic route, e.g. `gems/[advertiserId]/page.tsx`, before assuming the Promise form.)

- [ ] **Step 4: Point `api.ts` at the proxy** — replace the base/token block:

```ts
// Browser calls go through the session-checked same-origin proxy; the server
// itself (SSR) calls the API directly with the internal token.
const API_BASE =
  typeof window === "undefined"
    ? (process.env.AGF_API_INTERNAL_URL ?? "http://localhost:8000/api")
    : "/backend";
const SERVER_HEADERS: Record<string, string> =
  typeof window === "undefined" && process.env.AGF_API_TOKEN
    ? { Authorization: `Bearer ${process.env.AGF_API_TOKEN}` }
    : {};
```
and merge `SERVER_HEADERS` into `live()`'s headers. Grep for any other `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_API_TOKEN` reads (`grep -rn NEXT_PUBLIC_API web/src/`) and migrate them. NOTE: server-side `live()` calls now send the internal token but no `X-User-Email` — check whether any SSR call hits a user-scoped route; if so pass the session email through explicitly at the call site. Add `/settings` to `PROTECTED` in `auth.config.ts`.

- [ ] **Step 5: Gates** — `cd web && npm run lint && npx tsc --noEmit` clean. Local smoke: run backend (`AGF_DATABASE_URL=… go run .`) + `npm run dev`, seed a local user (Task 2's UpsertUser via a Go one-liner or SQL insert with a bcryptjs hash), log in, confirm the Discover feed loads through `/backend/*` (Network tab) and a signed-out `curl localhost:3000/backend/shortlist` 401s.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/users.ts web/src/lib/auth.ts web/src/lib/auth.config.ts web/src/lib/api.ts web/src/app/backend/ web/src/types/next-auth.d.ts
git commit -m "feat(web): DB-backed login + session-checked backend proxy (no public API token)"
```

---

### Task 11: Web — Settings → API keys page

**Files:**
- Create: `web/src/app/settings/page.tsx`, `web/src/app/settings/actions.ts`, `web/src/components/settings/ApiKeys.tsx`
- Modify: `web/src/components/AppHeader.tsx` (add Settings link)

**Interfaces:**
- Consumes: Task 4 internal endpoints (server actions call them with `AGF_API_TOKEN` + session email — never through the browser); session from `auth()`.
- Produces: `/settings` page listing keys (label, created, last used, revoked), a mint form (label input → shows the plaintext key ONCE with a copy hint), revoke buttons.

- [ ] **Step 1: Server actions `web/src/app/settings/actions.ts`**

```ts
"use server";

import { auth } from "@/lib/auth";

const API = process.env.AGF_API_INTERNAL_URL ?? "http://localhost:8000/api";
const TOKEN = process.env.AGF_API_TOKEN ?? "";

async function requireEmail(): Promise<string> {
  const session = await auth();
  const email = session?.user?.email;
  if (!email) throw new Error("not signed in.");
  return email;
}

export interface ApiKeyRow {
  key_hash: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export async function listKeys(): Promise<ApiKeyRow[]> {
  const email = await requireEmail();
  const res = await fetch(`${API}/internal/keys?email=${encodeURIComponent(email)}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    cache: "no-store",
  });
  if (!res.ok) throw new Error("could not load API keys.");
  return res.json();
}

export async function mintKey(label: string): Promise<string> {
  const email = await requireEmail();
  const res = await fetch(`${API}/internal/keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify({ email, label }),
  });
  if (!res.ok) throw new Error("could not create the key.");
  return (await res.json()).key as string;
}

export async function revokeKey(keyHash: string): Promise<void> {
  const email = await requireEmail();
  const res = await fetch(`${API}/internal/keys/revoke`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify({ email, key_hash: keyHash }),
  });
  if (!res.ok && res.status !== 204) throw new Error("could not revoke the key.");
}
```

- [ ] **Step 2: Page + component.** `page.tsx` is a server page: `const keys = await listKeys()` → renders `<ApiKeys initialKeys={keys} />`. `ApiKeys.tsx` is `"use client"`: table of keys (label, created date, last used or "never", "Revoked" state), a form (`label` input + "Create key" button → calls `mintKey`, shows the returned key in a monospace block with the line "Copy it now — it won't be shown again.", refreshes the list), revoke button per active key with one confirm step. Copy: plain and professional; connection hint block showing where to paste the key:

```
{ "mcpServers": { "fielddesk": { "type": "http", "url": "https://api.peon.works/mcp",
  "headers": { "Authorization": "Bearer <your key>" } } } }
```

Match existing component idioms — read `web/src/components/AppHeader.tsx` and one existing page for styling patterns (light paper theme, no all-caps) before writing JSX. Add `{ href: "/settings", label: "Settings" }` to the `links` array in `AppHeader.tsx`. Escape apostrophes in JSX text (`&apos;` / `{"won't"}`) — react/no-unescaped-entities.

- [ ] **Step 3: Gates + local smoke** — lint + tsc clean; in the dev stack: mint a key on the page, `curl -H "Authorization: Bearer fd_…" localhost:8000/api/shortlist?limit=1` → 200; revoke → same curl 401.

- [ ] **Step 4: Commit**

```bash
git add web/src/app/settings/ web/src/components/settings/ web/src/components/AppHeader.tsx
git commit -m "feat(web): settings page - personal API keys (mint once, list, revoke)"
```

---

### Task 12: Deploy, seed, live e2e gate, docs

**Files:**
- Modify: `DEPLOYMENT.md` (envs + MCP section), `README.md` if it lists services
- Delete (after parity verified): announce `src/agf_mcp/` retirement in the commit message but DO NOT delete yet — the founder decides after using the hosted version.

- [ ] **Step 1: Apply migration 145 to prod** (Mac, through local proxy — requires the founder's ADC to be fixed first: `gcloud auth application-default login`):

```bash
DB_PW="$(gcloud secrets versions access latest --secret=agf-db-password --project=peon-489013)"
AGF_DATABASE_URL="postgresql://agf:${DB_PW}@localhost:5433/agf" PYTHONPATH=src \
  .venv/bin/python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; print('applied:', apply_migrations(get_engine()))"
```

- [ ] **Step 2: Seed the founder account** (bcrypt hash via the web app's own bcryptjs; ASK THE FOUNDER for the password to use — do not invent one):

```bash
HASH=$(cd web && node -e "console.log(require('bcryptjs').hashSync(process.argv[1], 10))" "$FOUNDER_PASSWORD")
HASH="$HASH" AGF_DATABASE_URL="postgresql://agf:${DB_PW}@localhost:5433/agf" PYTHONPATH=src \
  .venv/bin/python -c "from agf.db.engine import get_engine; from sqlalchemy import text; import os
e = get_engine()
with e.begin() as c: c.execute(text(\"INSERT INTO frontend.users (email, password_hash, name, role) VALUES ('founder@fielddesk.local', :h, 'Founder', 'admin') ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = 'admin'\"), {'h': os.environ['HASH']})"
```
(Email stays `founder@fielddesk.local` — all existing ratings/saved gems are keyed to it.)

- [ ] **Step 3: Set envs/secrets** — ensure `AGF_API_TOKEN` exists as a k8s secret shared by BOTH deployments (generate: `openssl rand -base64 32`); add to `agf-api` env and to `agf-web` env along with `AGF_API_INTERNAL_URL` (confirm the service DNS first: `kubectl get svc -n agf` → e.g. `http://agf-api.agf.svc.cluster.local:8000/api`). Remove `NEXT_PUBLIC_API_URL`/`NEXT_PUBLIC_API_TOKEN` from the web build args if present.

- [ ] **Step 4: Build + roll both images** per `DEPLOYMENT.md:117-139` (api) and the web section (remember: `--platform linux/amd64`; golang base must match go.mod; post-`set image` the LB may 502 for a few minutes during NEG re-registration — wait it out).

- [ ] **Step 5: Live e2e gate (all must pass, observed, not assumed):**
  1. `https://app.peon.works/login` — log in as founder (DB-backed now). Feed loads; Network tab shows `/backend/*` calls.
  2. Settings page: mint a key (plaintext shown once).
  3. Claude Code: `claude mcp add --transport http fielddesk https://api.peon.works/mcp --header "Authorization: Bearer fd_…"`, then live-call `my_taste_profile` (real counts), `recommended_gems` (personalized=true), `scraping_health` (admin data).
  4. Create a member test user (Step 2 pattern, role `member`), mint their key, call `scraping_health` → "requires an admin account"; `my_ratings` → empty (scoping proven).
  5. Cookieless probe: `curl -s -o /dev/null -w '%{http_code}' https://api.peon.works/api/shortlist?limit=1` → **401**. `https://app.peon.works/settings` unauthenticated → redirect to `/login`.
  6. Revoke the member key → its MCP call now fails 401.

- [ ] **Step 6: Docs + memory** — DEPLOYMENT.md: new envs, MCP connect snippet, seed-user recipe. Update `docs/superpowers/specs/2026-07-02-multi-user-mcp-design.md` status → shipped. Write the memory file per the memory instructions.

- [ ] **Step 7: Commit**

```bash
git add DEPLOYMENT.md docs/superpowers/specs/2026-07-02-multi-user-mcp-design.md
git commit -m "docs(deploy): field desk mcp envs, connect guide, user seeding"
```
