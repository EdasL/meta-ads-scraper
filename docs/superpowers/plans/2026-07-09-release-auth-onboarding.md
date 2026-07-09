# Release: OAuth Registration + Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Public-release the Field Desk dashboard: Google/GitHub OAuth registration + login (OAuth-only UI), a first-run onboarding flow (concept explainer + 15-card preference calibration deck), and release polish (front-door login page, /terms, /privacy, error pages, settings account section).

**Architecture:** Extend the existing NextAuth v5 (JWT) setup with Google + GitHub providers; first OAuth sign-in upserts the user into `frontend.users` via a new internal Go endpoint (fail-closed). Onboarding state lives in a new `users.onboarded_at` column, checked server-side by `ProductShell` via a new `GET /me`. The review/preference stack is reused untouched for the calibration deck.

**Tech Stack:** Next.js 16 App Router, next-auth 5.0.0-beta.31, Go/Gin/GORM-raw, Postgres (migrations in `db/schema/`), Playwright for e2e.

**Spec:** `docs/superpowers/specs/2026-07-09-release-auth-onboarding-design.md`

## Global Constraints

- Product copy: plain, clear, professional. No marketing fluff, no exclamation spam. Product nouns intact: **Field Desk**, **GemScore**, **cohort** (project CLAUDE.md).
- Migrations: NEVER use a bare `%` anywhere in a `db/schema/*.sql` file (psycopg3 placeholder scan breaks apply on fresh DBs; a lint test enforces this). Use `mod()` if needed.
- Go integration tests: `package repositories_test`, skip when `AGF_IT_DB_URL` unset. Run with `AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./...` from `backend/`.
- Python suite gate: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest -q` from repo root — must be **0 failed** (skips are not passes).
- Web gates from `web/`: `npx tsc --noEmit`, `npx eslint src`, `npx next build`.
- Next.js 16 has breaking changes vs training data — read `web/node_modules/next/dist/docs/` for any API you're unsure of; same for `web/node_modules/next-auth` (v5 beta: client `signIn(provider, { redirectTo })`, env `AUTH_GOOGLE_ID`/`AUTH_GOOGLE_SECRET`/`AUTH_GITHUB_ID`/`AUTH_GITHUB_SECRET` are auto-read).
- Commit after each task with explicit paths (`git add <paths>`), never `git add -A`. All work lands on `main`.
- Verification commands run bare, as their own Bash call — never piped through tail/grep in the block that decides success.

---

### Task 1: migration 149 + user-repo OAuth/profile functions

**Files:**
- Create: `db/schema/149_oauth_users.sql`
- Modify: `backend/repositories/users_repo.go` (append)
- Test: `backend/repositories/users_repo_test.go` (new)

**Interfaces:**
- Produces (Go, used by Task 2):
  - `type UserProfile struct { Email, Name, Role, AuthProvider string; AvatarURL *string; OnboardedAt *time.Time }` (JSON tags: `email, name, role, auth_provider, avatar_url, onboarded_at`)
  - `func OAuthUpsertUser(ctx context.Context, g *gorm.DB, email, name, provider, avatarURL string) (*UserProfile, error)`
  - `func GetUserProfile(ctx context.Context, g *gorm.DB, email string) (*UserProfile, error)` — nil, nil when not found
  - `func MarkOnboarded(ctx context.Context, g *gorm.DB, email string) error` — idempotent

- [ ] **Step 1: Write the migration**

`db/schema/149_oauth_users.sql`:

```sql
-- OAuth-capable accounts: password becomes optional (Google/GitHub users have
-- none), the identity provider is recorded, and onboarding is tracked.
-- Existing users are backfilled as already onboarded so they are never
-- funneled into the new-user flow.
ALTER TABLE frontend.users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE frontend.users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'password';
ALTER TABLE frontend.users ADD CONSTRAINT users_auth_provider_check
    CHECK (auth_provider IN ('password', 'google', 'github'));
ALTER TABLE frontend.users ADD COLUMN avatar_url TEXT;
ALTER TABLE frontend.users ADD COLUMN onboarded_at TIMESTAMPTZ;
UPDATE frontend.users SET onboarded_at = now();
```

- [ ] **Step 2: Apply it to the integration test DB**

```bash
docker exec -i $(docker ps -qf name=agf_test_pg) psql -U agf -d agf_it < db/schema/149_oauth_users.sql
```

(If the container name differs, find it with `docker ps` — the test Postgres listens on host port 55433.) Also verify the placeholder lint passes: `pytest tests/db/test_migrations_placeholder_safe.py -q`.

- [ ] **Step 3: Write the failing tests**

`backend/repositories/users_repo_test.go` — same house style as `ratings_repo_test.go` (`package repositories_test`, TestMain skips without `AGF_IT_DB_URL`, testify `require`):

```go
// Package repositories_test exercises users_repo.go OAuth/profile functions
// against a real Postgres database. Skipped unless AGF_IT_DB_URL is set.
package repositories_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// usersDB reuses the connection opened by the package TestMain (ratings_repo_test.go).
// If that file's TestMain does NOT export a shared *gorm.DB, add the same
// TestMain-guarded var pattern here under a different name and guard against
// double-TestMain (only one TestMain per package — extend the existing one
// with a seedUsers(g) call instead of adding a second).

func seedUsers(t *testing.T) {
	t.Helper()
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	must(ratingsDB.Exec(`DELETE FROM frontend.api_keys WHERE user_email LIKE 'oauth-test-%@t.local'`).Error)
	must(ratingsDB.Exec(`DELETE FROM frontend.users WHERE email LIKE 'oauth-test-%@t.local'`).Error)
}

func TestOAuthUpsertUser_InsertsNewMember(t *testing.T) {
	seedUsers(t)
	ctx := context.Background()

	p, err := repositories.OAuthUpsertUser(ctx, ratingsDB, "OAuth-Test-New@t.local", "New User", "google", "https://a/img.png")
	require.NoError(t, err)
	require.Equal(t, "oauth-test-new@t.local", p.Email) // normalized lowercase
	require.Equal(t, "member", p.Role)
	require.Equal(t, "google", p.AuthProvider)
	require.NotNil(t, p.AvatarURL)
	require.Nil(t, p.OnboardedAt) // new users must go through onboarding
}

func TestOAuthUpsertUser_ConflictKeepsProviderAndRole(t *testing.T) {
	seedUsers(t)
	ctx := context.Background()
	// Existing password-provisioned admin.
	must(ratingsDB.Exec(`INSERT INTO frontend.users (email, password_hash, name, role, onboarded_at)
		VALUES ('oauth-test-admin@t.local', 'x', 'Old Name', 'admin', now())`).Error)

	p, err := repositories.OAuthUpsertUser(ctx, ratingsDB, "oauth-test-admin@t.local", "Fresh Name", "github", "")
	require.NoError(t, err)
	require.Equal(t, "admin", p.Role)               // role never clobbered
	require.Equal(t, "password", p.AuthProvider)     // provider set once at insert
	require.Equal(t, "Fresh Name", p.Name)           // name refreshed
	require.NotNil(t, p.OnboardedAt)                 // existing user stays onboarded
}

func TestMarkOnboarded_IdempotentAndProfileRoundTrip(t *testing.T) {
	seedUsers(t)
	ctx := context.Background()
	_, err := repositories.OAuthUpsertUser(ctx, ratingsDB, "oauth-test-ob@t.local", "Ob", "github", "")
	require.NoError(t, err)

	require.NoError(t, repositories.MarkOnboarded(ctx, ratingsDB, "oauth-test-ob@t.local"))
	p1, err := repositories.GetUserProfile(ctx, ratingsDB, "oauth-test-ob@t.local")
	require.NoError(t, err)
	require.NotNil(t, p1.OnboardedAt)

	// Second call must not error or move the timestamp.
	require.NoError(t, repositories.MarkOnboarded(ctx, ratingsDB, "oauth-test-ob@t.local"))
	p2, err := repositories.GetUserProfile(ctx, ratingsDB, "oauth-test-ob@t.local")
	require.NoError(t, err)
	require.Equal(t, p1.OnboardedAt.UTC(), p2.OnboardedAt.UTC())
}

func TestGetUserProfile_NotFound(t *testing.T) {
	seedUsers(t)
	p, err := repositories.GetUserProfile(context.Background(), ratingsDB, "oauth-test-missing@t.local")
	require.NoError(t, err)
	require.Nil(t, p)
}
```

NOTE: `ratingsDB` and `must` live in `ratings_repo_test.go` in the same package — reuse them; do NOT add a second TestMain.

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./repositories/ -run 'OAuthUpsert|MarkOnboarded|GetUserProfile' -v
```

Expected: compile error (functions undefined).

- [ ] **Step 5: Implement the repo functions**

Append to `backend/repositories/users_repo.go`:

```go
type UserProfile struct {
	Email        string     `json:"email"`
	Name         string     `json:"name"`
	Role         string     `json:"role"`
	AuthProvider string     `json:"auth_provider"`
	AvatarURL    *string    `json:"avatar_url"`
	OnboardedAt  *time.Time `json:"onboarded_at"`
}

// OAuthUpsertUser registers or refreshes an OAuth identity. On conflict it
// updates name/avatar only — auth_provider and role are set once at insert, so
// a later OAuth sign-in can never clobber an existing account's provider or
// privileges. Email is normalized to lowercase.
func OAuthUpsertUser(ctx context.Context, g *gorm.DB, email, name, provider, avatarURL string) (*UserProfile, error) {
	var rows []UserProfile
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`INSERT INTO frontend.users (email, password_hash, name, role, auth_provider, avatar_url)
		               VALUES (lower(?), NULL, NULLIF(?, ''), 'member', ?, NULLIF(?, ''))
		               ON CONFLICT (email) DO UPDATE SET
		                 name       = COALESCE(NULLIF(EXCLUDED.name, ''), frontend.users.name),
		                 avatar_url = COALESCE(EXCLUDED.avatar_url, frontend.users.avatar_url)
		               RETURNING email, COALESCE(name, '') AS name, role, auth_provider, avatar_url, onboarded_at`,
			email, name, provider, avatarURL).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

func GetUserProfile(ctx context.Context, g *gorm.DB, email string) (*UserProfile, error) {
	var rows []UserProfile
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT email, COALESCE(name, '') AS name, role, auth_provider, avatar_url, onboarded_at
		               FROM frontend.users WHERE lower(email) = lower(?)`, email).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

// MarkOnboarded is idempotent: only the first call sets the timestamp.
func MarkOnboarded(ctx context.Context, g *gorm.DB, email string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(`UPDATE frontend.users SET onboarded_at = now()
		                WHERE lower(email) = lower(?) AND onboarded_at IS NULL`, email).Error
	})
}
```

- [ ] **Step 6: Run tests to verify they pass**

Same command as Step 4. Expected: PASS (all 4). Then `go vet ./...` and `go build ./...` from `backend/`.

- [ ] **Step 7: Commit**

```bash
git add db/schema/149_oauth_users.sql backend/repositories/users_repo.go backend/repositories/users_repo_test.go
git commit -m "feat(db,api): OAuth-capable users — nullable password, provider, onboarded_at (migration 149)"
```

---

### Task 2: Go endpoints — /internal/oauth-login, GET /me, POST /me/onboarded

**Files:**
- Modify: `backend/controllers/internal_auth_controller.go` (append `OAuthLogin`)
- Create: `backend/controllers/users_controller.go`
- Modify: `backend/routers/routers.go`
- Test: `backend/repositories/users_repo_test.go` already covers the data layer; this task adds a thin router-level test `backend/controllers/users_controller_test.go` ONLY if an httptest precedent exists in `backend/controllers/` — there is none today, so controller logic stays thin enough to skip dedicated tests (matching every other controller in the package).

**Interfaces:**
- Consumes (Task 1): `repositories.OAuthUpsertUser`, `GetUserProfile`, `MarkOnboarded`, `middlewares.UserFrom(ctx)` (`backend/middlewares/keyauth.go:29`).
- Produces (HTTP, used by Tasks 3-5):
  - `POST /api/internal/oauth-login` body `{"email","name","provider","avatar_url"}` → 200 `{"email","name","role","onboarded":bool}`; 400 on missing email / provider not in {google, github}.
  - `GET /api/me` → 200 `UserProfile` JSON (`email,name,role,auth_provider,avatar_url,onboarded_at`); 404 if no row.
  - `POST /api/me/onboarded` (RequireToken, like all writes) → 204.

- [ ] **Step 1: Append OAuthLogin to the internal controller**

In `backend/controllers/internal_auth_controller.go` (style-match `Login` at line 17):

```go
func (ia *InternalAuthController) OAuthLogin(ctx *gin.Context) {
	var body struct {
		Email     string `json:"email"`
		Name      string `json:"name"`
		Provider  string `json:"provider"`
		AvatarURL string `json:"avatar_url"`
	}
	if err := ctx.ShouldBindJSON(&body); err != nil || body.Email == "" ||
		(body.Provider != "google" && body.Provider != "github") {
		helpers.Detail(ctx, http.StatusBadRequest, "email and a known provider are required.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	p, err := repositories.OAuthUpsertUser(rctx, db.DB(), body.Email, body.Name, body.Provider, body.AvatarURL)
	if err != nil || p == nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{
		"email": p.Email, "name": p.Name, "role": p.Role, "onboarded": p.OnboardedAt != nil,
	})
}
```

- [ ] **Step 2: Create the users controller**

`backend/controllers/users_controller.go`:

```go
package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// UsersController serves the authenticated caller's own profile. Identity
// comes from KeyAuth (fd_ key or internal token + X-User-Email) — never from
// a query param, so a caller can only ever read or update themselves.
type UsersController struct{}

func NewUsersController() *UsersController { return &UsersController{} }

func (uc *UsersController) Me(ctx *gin.Context) {
	u, ok := middlewares.UserFrom(ctx.Request.Context())
	if !ok {
		helpers.Detail(ctx, http.StatusUnauthorized, "not signed in.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	p, err := repositories.GetUserProfile(rctx, db.DB(), u.Email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if p == nil {
		helpers.Detail(ctx, http.StatusNotFound, "no such user.")
		return
	}
	ctx.JSON(http.StatusOK, p)
}

func (uc *UsersController) MarkOnboarded(ctx *gin.Context) {
	u, ok := middlewares.UserFrom(ctx.Request.Context())
	if !ok {
		helpers.Detail(ctx, http.StatusUnauthorized, "not signed in.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.MarkOnboarded(rctx, db.DB(), u.Email); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	ctx.Status(http.StatusNoContent)
}
```

- [ ] **Step 3: Wire routes**

In `backend/routers/routers.go`: add `users := controllers.NewUsersController()` next to the other controllers (line ~29), then inside the `/api` group (after the ratings routes, line ~64):

```go
		// The caller's own account (identity from KeyAuth, never a query param).
		api.GET("/me", users.Me)
		api.POST("/me/onboarded", middlewares.RequireToken(), users.MarkOnboarded)
```

and in the internal group (after `internal.POST("/login", ia.Login)`, line 76):

```go
	internal.POST("/oauth-login", ia.OAuthLogin)
```

- [ ] **Step 4: Build + vet + full Go tests**

```bash
cd backend && go build ./... && go vet ./...
AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./...
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/controllers/internal_auth_controller.go backend/controllers/users_controller.go backend/routers/routers.go
git commit -m "feat(api): oauth-login upsert + /me profile + /me/onboarded"
```

---

### Task 3: NextAuth Google + GitHub, OAuth-only login page

**Files:**
- Modify: `web/src/lib/users.ts` (append `oauthLogin`)
- Modify: `web/src/lib/auth.ts`
- Create: `web/src/components/auth/OAuthButtons.tsx`
- Modify: `web/src/app/login/page.tsx`
- Delete: `web/src/components/auth/LoginForm.tsx`
- Modify: `web/.env.example` (document the four new AUTH_ vars)

**Interfaces:**
- Consumes: `POST /api/internal/oauth-login` (Task 2).
- Produces: session shape UNCHANGED (`user.id = email`, `user.email`, `user.name`, `role` on token/session) — everything downstream (proxy `X-User-Email`, useSession readers) keeps working. Credentials provider stays REGISTERED (e2e/emergency) but has no UI.

- [ ] **Step 1: Add oauthLogin to users.ts**

Append to `web/src/lib/users.ts`:

```ts
export interface OAuthUser {
  email: string;
  name: string;
  role: "admin" | "member";
  onboarded: boolean;
}

/** Upsert an OAuth identity into frontend.users. First sign-in IS
 * registration. Returns null on any failure — the sign-in is then denied
 * (fail closed). */
export async function oauthLogin(
  email: string,
  name: string,
  provider: "google" | "github",
  avatarUrl: string,
): Promise<OAuthUser | null> {
  try {
    const res = await fetch(`${API}/internal/oauth-login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${TOKEN}`,
      },
      body: JSON.stringify({ email, name, provider, avatar_url: avatarUrl }),
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as OAuthUser;
  } catch {
    return null;
  }
}
```

- [ ] **Step 2: Add providers + signIn callback to auth.ts**

Replace `web/src/lib/auth.ts` with:

```ts
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import Google from "next-auth/providers/google";
import { authConfig } from "./auth.config";
import { oauthLogin, verifyLogin } from "./users";

export const { handlers, auth, signIn, signOut } = NextAuth({
  ...authConfig,
  session: { strategy: "jwt" },
  providers: [
    Google,
    GitHub,
    // No UI form renders this provider; it remains for e2e tests and
    // emergency access via a direct POST to /api/auth/callback/credentials.
    Credentials({
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const email = String(credentials?.email ?? "");
        const password = String(credentials?.password ?? "");
        if (!email || !password) return null;
        const u = await verifyLogin(email, password);
        if (!u) return null;
        return { id: u.email, email: u.email, name: u.name, role: u.role };
      },
    }),
  ],
  callbacks: {
    ...authConfig.callbacks,
    async signIn({ user, account, profile }) {
      if (!account || account.provider === "credentials") return true;
      const email = user.email;
      if (!email) return false;
      // Google marks verification explicitly; GitHub's provider only returns
      // verified primary emails, so presence is sufficient there.
      if (account.provider === "google") {
        const verified = (profile as { email_verified?: boolean } | null)?.email_verified;
        if (verified !== true) return false;
      }
      const u = await oauthLogin(
        email,
        user.name ?? "",
        account.provider as "google" | "github",
        user.image ?? "",
      );
      if (!u) return false; // fail closed: no account row, no session
      (user as { role?: string }).role = u.role;
      return true;
    },
    jwt({ token, user }) {
      if (user) token.role = (user as { role?: string }).role ?? "member";
      return token;
    },
    session({ session, token }) {
      if (session.user) {
        (session.user as { role?: string }).role = token.role as string;
      }
      return session;
    },
  },
});
```

Verify against `web/node_modules/next-auth` docs that bare `Google` / `GitHub` provider references are valid in this beta (they are in beta.31 — env inference); if not, call them: `Google({})`.

- [ ] **Step 3: Create OAuthButtons**

`web/src/components/auth/OAuthButtons.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import { IconBrandGithub, IconBrandGoogleFilled } from "@tabler/icons-react";

export function OAuthButtons() {
  const params = useSearchParams();
  const callbackUrl = params.get("callbackUrl") ?? "/discover";
  const failed = params.get("error") !== null;
  const [busy, setBusy] = useState<"google" | "github" | null>(null);

  function start(provider: "google" | "github") {
    setBusy(provider);
    // Redirects away; busy state only matters until the browser leaves.
    void signIn(provider, { redirectTo: callbackUrl });
  }

  const button: React.CSSProperties = {
    width: "100%",
    padding: "13px 16px",
    fontSize: "1rem",
    background: "var(--ink)",
    color: "var(--paper)",
    border: "none",
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <button className="display" style={button} disabled={busy !== null} onClick={() => start("google")}>
        <IconBrandGoogleFilled size={18} />
        {busy === "google" ? "Redirecting…" : "Continue with Google"}
      </button>
      <button className="display" style={button} disabled={busy !== null} onClick={() => start("github")}>
        <IconBrandGithub size={18} stroke={1.75} />
        {busy === "github" ? "Redirecting…" : "Continue with GitHub"}
      </button>
      {failed && (
        <p className="font-mono" style={{ color: "var(--ink)", fontSize: "0.78rem", margin: 0, fontWeight: 600 }}>
          Sign-in didn&apos;t work. Try again.
        </p>
      )}
    </div>
  );
}
```

Check the v5 beta client signature in `web/node_modules/next-auth`: if `redirectTo` is not accepted by the client `signIn`, use `{ callbackUrl }`.

- [ ] **Step 4: Rewrite the login page as the front door**

Replace `web/src/app/login/page.tsx`:

```tsx
import { Suspense } from "react";
import Link from "next/link";
import { OAuthButtons } from "@/components/auth/OAuthButtons";

export default function LoginPage() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        position: "relative",
        zIndex: 1,
      }}
    >
      <div style={{ width: "min(440px, 100%)" }}>
        <div style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", boxShadow: "6px 6px 0 var(--ink)" }}>
          <div style={{ padding: "28px 28px 32px" }}>
            <h1 className="display-x" style={{ fontSize: "2.6rem", margin: 0 }}>Field Desk</h1>
            <p className="font-mono" style={{ fontSize: "0.8rem", color: "var(--muted)", marginTop: 8, lineHeight: 1.5 }}>
              Finds mobile apps that are quietly scaling paid ads — before the
              charts notice. Sign in to see them.
            </p>
            <div style={{ marginTop: 24 }}>
              <Suspense fallback={null}>
                <OAuthButtons />
              </Suspense>
            </div>
          </div>
        </div>
        <p className="font-mono" style={{ fontSize: "0.72rem", color: "var(--muted)", marginTop: 14, textAlign: "center" }}>
          By signing in you agree to the{" "}
          <Link href="/terms" style={{ color: "inherit" }}>terms</Link> and{" "}
          <Link href="/privacy" style={{ color: "inherit" }}>privacy policy</Link>.
        </p>
      </div>
    </main>
  );
}
```

Then delete `web/src/components/auth/LoginForm.tsx` (`git rm`). Grep for other importers first: `grep -rn "LoginForm" web/src` — must be zero after the page rewrite.

- [ ] **Step 5: Document env vars**

Append to `web/.env.example` (do NOT touch `.env.local` — permission-blocked; the founder wires real values):

```
# OAuth sign-in (NextAuth v5 reads these names automatically)
AUTH_GOOGLE_ID=
AUTH_GOOGLE_SECRET=
AUTH_GITHUB_ID=
AUTH_GITHUB_SECRET=
```

- [ ] **Step 6: Gates**

```bash
cd web && npx tsc --noEmit && npx eslint src && npx next build
```

Expected: clean. (OAuth env vars are absent locally — the build must still pass; providers only need env at sign-in time. If the build fails on missing env, wrap provider entries: include Google/GitHub only when their env vars are set, and note it in the commit.)

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/users.ts web/src/lib/auth.ts web/src/components/auth/OAuthButtons.tsx web/src/app/login/page.tsx web/.env.example
git rm web/src/components/auth/LoginForm.tsx
git commit -m "feat(web): Google/GitHub OAuth sign-in — first sign-in registers; OAuth-only login page"
```

---

### Task 4: Onboarding gate — /me plumbing + ProductShell redirect

**Files:**
- Modify: `web/src/app/backend/[...path]/route.ts` (allowlist)
- Modify: `web/src/lib/users.ts` (append `getProfile`)
- Modify: `web/src/lib/api.ts` (append `completeOnboarding`)
- Modify: `web/src/components/chrome/ProductShell.tsx`
- Modify: `web/src/lib/auth.config.ts` (PROTECTED list)

**Interfaces:**
- Consumes: `GET /api/me`, `POST /api/me/onboarded` (Task 2).
- Produces (used by Task 5/6):
  - `getProfile(email): Promise<Profile | null>` (server-only, users.ts) where `Profile = { email: string; name: string; role: string; auth_provider: string; avatar_url: string | null; onboarded_at: string | null }`
  - `completeOnboarding(): Promise<void>` (browser, api.ts, via `/backend/me/onboarded`)
  - ProductShell redirects any signed-in, un-onboarded user to `/onboarding`. The onboarding page must NOT render ProductShell (that is what prevents a loop).

- [ ] **Step 1: Allowlist "me" in the proxy**

In `web/src/app/backend/[...path]/route.ts`, add to `ALLOWED_RESOURCES`:

```ts
  "me", // /me (profile), /me/onboarded
```

- [ ] **Step 2: Server-side getProfile**

Append to `web/src/lib/users.ts`:

```ts
export interface Profile {
  email: string;
  name: string;
  role: string;
  auth_provider: string;
  avatar_url: string | null;
  onboarded_at: string | null;
}

/** Server-only profile read (internal token + X-User-Email, same trust path
 * as the /backend proxy). Returns null on any failure — callers fail open. */
export async function getProfile(email: string): Promise<Profile | null> {
  try {
    const res = await fetch(`${API}/me`, {
      headers: { Authorization: `Bearer ${TOKEN}`, "X-User-Email": email },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as Profile;
  } catch {
    return null;
  }
}
```

- [ ] **Step 3: Browser-side completeOnboarding**

Append to `web/src/lib/api.ts` (next to `setRating`, same `live()` helper):

```ts
/** Marks the signed-in user as onboarded (first-run flow finished or skipped). */
export async function completeOnboarding(): Promise<void> {
  await live(`/me/onboarded`, { method: "POST", body: "{}" });
}
```

- [ ] **Step 4: ProductShell onboarding gate**

Replace `web/src/components/chrome/ProductShell.tsx`:

```tsx
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getProfile } from "@/lib/users";
import { AppHeader } from "@/components/AppHeader";
import { Backdrop } from "./Backdrop";

/**
 * Shared chrome for the logged-in product (/discover, /saved, /gems/*):
 * light "paper" theme, pale fingerprint watermark, sticky app header.
 * Also the onboarding gate: a signed-in user who has not finished (or
 * skipped) first-run onboarding is redirected to /onboarding — which renders
 * WITHOUT this shell, so there is no redirect loop. The profile read fails
 * open: if /me is unreachable the app renders normally.
 */
export async function ProductShell({ children }: { children: React.ReactNode }) {
  const session = await auth();
  const email = session?.user?.email;
  if (email) {
    const me = await getProfile(email);
    if (me && me.onboarded_at === null) redirect("/onboarding");
  }
  return (
    <div className="theme-light" style={{ minHeight: "100dvh", color: "var(--ink)" }}>
      <Backdrop variant="light" />
      <AppHeader />
      {children}
    </div>
  );
}
```

(All current consumers are server components — `grep -rn "ProductShell" web/src` to confirm none is a client component; a client importer would break on an async server component and needs restructuring, not a cast.)

- [ ] **Step 5: Protect /onboarding**

In `web/src/lib/auth.config.ts` line 3:

```ts
const PROTECTED = ["/discover", "/saved", "/gems", "/review", "/settings", "/onboarding"];
```

- [ ] **Step 6: Gates + commit**

```bash
cd web && npx tsc --noEmit && npx eslint src && npx next build
git add "web/src/app/backend/[...path]/route.ts" web/src/lib/users.ts web/src/lib/api.ts web/src/components/chrome/ProductShell.tsx web/src/lib/auth.config.ts
git commit -m "feat(web): onboarding gate — /me plumbing + ProductShell redirect"
```

---

### Task 5: /onboarding — explainer + 15-card calibration deck

**Files:**
- Create: `web/src/app/onboarding/page.tsx`
- Create: `web/src/components/onboarding/OnboardingFlow.tsx`

**Interfaces:**
- Consumes: `getPreferenceQueue(email, 15)` and `setRating` (`web/src/lib/api.ts`), `completeOnboarding` (Task 4), `ReviewCard` (`web/src/components/review/ReviewCard.tsx` — props `{ card: PreferenceCard; onRate: (rating: Rating, comment?: string) => void }`; verify the exact prop names in the file before using), `getProfile` (Task 4).
- Produces: a signed-in user's first-run flow; finishing or skipping calls `completeOnboarding()` then navigates to `/discover`.

- [ ] **Step 1: Server page (skip-if-already-onboarded)**

`web/src/app/onboarding/page.tsx`:

```tsx
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { getProfile } from "@/lib/users";
import { Backdrop } from "@/components/chrome/Backdrop";
import { OnboardingFlow } from "@/components/onboarding/OnboardingFlow";

export default async function OnboardingPage() {
  const session = await auth();
  const email = session?.user?.email;
  if (!email) redirect("/login");
  const me = await getProfile(email);
  if (me && me.onboarded_at !== null) redirect("/discover");

  return (
    <div className="theme-light" style={{ minHeight: "100dvh", color: "var(--ink)" }}>
      <Backdrop variant="light" />
      <OnboardingFlow />
    </div>
  );
}
```

- [ ] **Step 2: The flow component**

`web/src/components/onboarding/OnboardingFlow.tsx`. Structure (complete file — style constants inline, theme vars only, plain copy):

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";

import { completeOnboarding, getPreferenceQueue, setRating } from "@/lib/api";
import type { Rating } from "@/lib/api";
import type { PreferenceCard } from "@/types/contracts";
import { ReviewCard } from "@/components/review/ReviewCard";

const DECK_SIZE = 15;

const PANELS = [
  {
    title: "Find apps before they're obvious",
    body: "Field Desk watches Meta's ad library for mobile apps that are quietly scaling paid campaigns — small pages with real, growing ad spend. You see them while they're still emerging, not after they top the charts.",
  },
  {
    title: "GemScore ranks the signal",
    body: "Every app gets a GemScore: how long its winning ads have been running, how fast its campaign is growing, and how much reach it's buying — measured against its cohort. Higher means a stronger scaling signal.",
  },
  {
    title: "Teach it your taste",
    body: "Rate apps in Review — like, skip, or pass. Once you've liked about 8, your Discover feed reranks around what you like. First, a short calibration round.",
  },
] as const;

export function OnboardingFlow() {
  const router = useRouter();
  const { data: session } = useSession();
  const userEmail = session?.user?.email ?? null;

  const [panel, setPanel] = useState(0); // 0..2 explainer, 3 = deck
  const [cards, setCards] = useState<PreferenceCard[]>([]);
  const [idx, setIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const [deckError, setDeckError] = useState(false);
  const [finishing, setFinishing] = useState(false);

  const inDeck = panel === PANELS.length;

  useEffect(() => {
    if (!inDeck || !userEmail) return;
    let alive = true;
    setLoading(true);
    setDeckError(false);
    getPreferenceQueue(userEmail, DECK_SIZE)
      .then((c) => {
        if (alive) setCards(c);
      })
      .catch(() => {
        if (alive) setDeckError(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [inDeck, userEmail]);

  async function finish() {
    if (finishing) return;
    setFinishing(true);
    try {
      await completeOnboarding();
    } catch {
      // Best-effort: the gate re-shows onboarding next visit if this failed.
    }
    router.replace("/discover");
  }

  function rate(rating: Rating, comment = "") {
    const current = cards[idx];
    if (!current || !userEmail) return;
    setIdx((i) => i + 1);
    setRating(current.advertiser_id, userEmail, rating, comment).catch(() => {
      /* best-effort, same policy as the review deck */
    });
    if (idx + 1 >= cards.length) void finish();
  }

  const shellStyle: React.CSSProperties = {
    maxWidth: 720,
    margin: "0 auto",
    padding: "48px 22px 64px",
    position: "relative",
    zIndex: 1,
  };

  if (!inDeck) {
    const p = PANELS[panel];
    return (
      <main style={shellStyle}>
        <p className="t-sm w-ui nums" style={{ color: "var(--muted)", margin: 0 }}>
          {panel + 1} of {PANELS.length}
        </p>
        <h1 className="display-x" style={{ fontSize: "2.2rem", marginTop: 10 }}>{p.title}</h1>
        <p style={{ marginTop: 16, lineHeight: 1.6, maxWidth: "56ch" }}>{p.body}</p>
        <div style={{ display: "flex", gap: 12, marginTop: 32 }}>
          {panel > 0 && (
            <button className="t-sm w-ui" onClick={() => setPanel((v) => v - 1)}
              style={{ background: "transparent", border: "1.5px solid var(--ink)", padding: "12px 22px", cursor: "pointer", color: "var(--ink)" }}>
              Back
            </button>
          )}
          <button className="t-sm w-strong" onClick={() => setPanel((v) => v + 1)}
            style={{ background: "var(--ink)", color: "var(--paper)", border: "none", padding: "12px 22px", cursor: "pointer" }}>
            {panel === PANELS.length - 1 ? "Start calibrating" : "Next"}
          </button>
        </div>
      </main>
    );
  }

  const current = cards[idx];

  return (
    <main style={shellStyle}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <div>
          <p className="t-sm w-strong nums" style={{ margin: 0 }}>
            Calibrate your feed{cards.length > 0 ? ` — ${Math.min(idx + 1, cards.length)} of ${cards.length}` : ""}
          </p>
          <p className="t-sm w-ui" style={{ color: "var(--muted)", marginTop: 4 }}>
            Like 8 or more apps and Discover starts ranking around your taste.
          </p>
        </div>
        <button className="t-sm w-ui" onClick={() => void finish()} disabled={finishing}
          style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", textDecoration: "underline" }}>
          Skip for now
        </button>
      </div>
      <div style={{ marginTop: 24 }}>
        {loading && <p>Loading apps to rate…</p>}
        {deckError && !loading && (
          <p>
            Couldn&apos;t load the deck.{" "}
            <button className="t-sm w-ui" onClick={() => { setPanel(PANELS.length - 1); setTimeout(() => setPanel(PANELS.length), 0); }}
              style={{ background: "transparent", border: "none", cursor: "pointer", textDecoration: "underline", color: "var(--ink)" }}>
              Retry
            </button>{" "}
            or skip — you can rate any time in Review.
          </p>
        )}
        {!loading && !deckError && !current && !finishing && cards.length === 0 && (
          <p>Nothing to rate right now — skip ahead; new apps appear as discovery runs.</p>
        )}
        {!loading && current && <ReviewCard card={current} onRate={rate} />}
      </div>
    </main>
  );
}
```

NOTE for the implementer: verify `ReviewCard`'s exact prop contract in `web/src/components/review/ReviewCard.tsx` before wiring (`card` + `onRate(rating, comment?)` per ReviewDeck.tsx:139); if it differs, adapt the call, not the card. The retry hack (panel flip) may be replaced by a `refreshKey` state if eslint objects — any mechanism that re-runs the fetch effect is fine.

- [ ] **Step 3: Gates**

```bash
cd web && npx tsc --noEmit && npx eslint src && npx next build
```

- [ ] **Step 4: Manual render check**

Run `npx next dev -p 3005`, sign in is not possible without OAuth env locally — verify the page compiles and the explainer renders by temporarily visiting with a dev session if available; the full flow is exercised in Task 8's e2e. Do not block this task on it.

- [ ] **Step 5: Commit**

```bash
git add web/src/app/onboarding/page.tsx web/src/components/onboarding/OnboardingFlow.tsx
git commit -m "feat(web): onboarding — concept explainer + 15-card calibration deck"
```

---

### Task 6: Release polish — legal pages, error pages, settings account section

**Files:**
- Create: `web/src/app/terms/page.tsx`
- Create: `web/src/app/privacy/page.tsx`
- Create: `web/src/app/error.tsx`
- Create: `web/src/app/not-found.tsx`
- Create: `web/src/components/settings/AccountCard.tsx`
- Modify: `web/src/app/settings/page.tsx`

**Interfaces:**
- Consumes: `getProfile` (Task 4), `auth` from `@/lib/auth`.
- Produces: public `/terms` and `/privacy` (not in PROTECTED — verify they render cookieless), global error/404 pages, account section above API keys in settings.

- [ ] **Step 1: Legal pages**

Both are static server components sharing a simple layout. `web/src/app/terms/page.tsx`:

```tsx
const S = {
  main: { maxWidth: 720, margin: "0 auto", padding: "48px 22px 64px", position: "relative" as const, zIndex: 1 },
  h2: { marginTop: 28, fontSize: "1.05rem" },
  p: { marginTop: 10, lineHeight: 1.6, color: "var(--ink-soft)" },
};

export const metadata = { title: "Terms — Field Desk" };

export default function TermsPage() {
  return (
    <main style={S.main}>
      <h1 className="display-x" style={{ fontSize: "2rem" }}>Terms of service</h1>
      <p style={S.p}>Last updated: July 9, 2026.</p>

      <h2 className="w-strong" style={S.h2}>The service</h2>
      <p style={S.p}>
        Field Desk surfaces mobile apps that appear to be scaling paid
        advertising, based on public ad-library and app-store data. The data is
        collected and processed automatically and comes with no guarantee of
        accuracy, completeness, or fitness for any decision you make with it.
      </p>

      <h2 className="w-strong" style={S.h2}>Your account</h2>
      <p style={S.p}>
        You sign in with a Google or GitHub account. You are responsible for
        activity under your account. We may suspend accounts that abuse the
        service — automated scraping of the dashboard, resale of the data, or
        attempts to disrupt the service.
      </p>

      <h2 className="w-strong" style={S.h2}>No warranty</h2>
      <p style={S.p}>
        The service is provided as-is, without warranties of any kind. We are
        not liable for losses arising from its use. We may change or
        discontinue features at any time.
      </p>

      <h2 className="w-strong" style={S.h2}>Contact</h2>
      <p style={S.p}>Questions: elakavicius@gmail.com</p>
    </main>
  );
}
```

`web/src/app/privacy/page.tsx` (same S constants inline):

```tsx
export const metadata = { title: "Privacy — Field Desk" };

export default function PrivacyPage() {
  return (
    <main style={S.main}>
      <h1 className="display-x" style={{ fontSize: "2rem" }}>Privacy policy</h1>
      <p style={S.p}>Last updated: July 9, 2026.</p>

      <h2 className="w-strong" style={S.h2}>What we store</h2>
      <p style={S.p}>
        When you sign in with Google or GitHub we store your email address,
        name, and avatar. As you use the product we store your app ratings,
        saved lists, and API keys you create. That is the full list.
      </p>

      <h2 className="w-strong" style={S.h2}>What we don&apos;t do</h2>
      <p style={S.p}>
        We don&apos;t sell your data, we don&apos;t share it with third
        parties, and we don&apos;t use third-party analytics or advertising
        trackers. Cookies are used only to keep you signed in.
      </p>

      <h2 className="w-strong" style={S.h2}>Deletion</h2>
      <p style={S.p}>
        Email elakavicius@gmail.com from your account address and we&apos;ll
        delete your account and its data.
      </p>
    </main>
  );
}
```

(Copy both files' shared `S` constant — they are independent routes; duplication of six style lines beats a shared module for two static pages.)

- [ ] **Step 2: Error + 404 pages**

`web/src/app/error.tsx`:

```tsx
"use client";

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <main style={{ minHeight: "60vh", display: "grid", placeItems: "center", padding: 24, position: "relative", zIndex: 1 }}>
      <div style={{ textAlign: "center" }}>
        <h1 className="display-x" style={{ fontSize: "1.8rem" }}>Something went wrong</h1>
        <p style={{ color: "var(--muted)", marginTop: 8 }}>The error is on our side, not yours.</p>
        <button className="t-sm w-strong" onClick={reset}
          style={{ marginTop: 20, background: "var(--ink)", color: "var(--paper)", border: "none", padding: "12px 22px", cursor: "pointer" }}>
          Try again
        </button>
      </div>
    </main>
  );
}
```

`web/src/app/not-found.tsx`:

```tsx
import Link from "next/link";

export default function NotFound() {
  return (
    <main style={{ minHeight: "60vh", display: "grid", placeItems: "center", padding: 24, position: "relative", zIndex: 1 }}>
      <div style={{ textAlign: "center" }}>
        <h1 className="display-x" style={{ fontSize: "1.8rem" }}>Page not found</h1>
        <p style={{ color: "var(--muted)", marginTop: 8 }}>Nothing lives at this address.</p>
        <Link href="/discover" className="t-sm w-strong"
          style={{ display: "inline-block", marginTop: 20, background: "var(--ink)", color: "var(--paper)", padding: "12px 22px", textDecoration: "none" }}>
          Go to Discover
        </Link>
      </div>
    </main>
  );
}
```

- [ ] **Step 3: Settings account section**

`web/src/components/settings/AccountCard.tsx`:

```tsx
import type { Profile } from "@/lib/users";

export function AccountCard({ profile }: { profile: Profile }) {
  return (
    <section style={{ border: "1.5px solid var(--ink)", background: "var(--paper)", padding: "22px 24px", marginBottom: 28 }}>
      <h2 className="t-sm w-strong" style={{ margin: 0 }}>Account</h2>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 14 }}>
        {profile.avatar_url && (
          // eslint-disable-next-line @next/next/no-img-element -- external avatar host, no next/image domain config for one thumbnail
          <img src={profile.avatar_url} alt="" width={44} height={44} style={{ borderRadius: "50%", border: "1.5px solid var(--ink)" }} />
        )}
        <div>
          <p className="w-strong" style={{ margin: 0 }}>{profile.name || profile.email}</p>
          <p className="t-sm w-ui" style={{ color: "var(--muted)", margin: "2px 0 0" }}>
            {profile.email} · signs in with {profile.auth_provider === "password" ? "email" : profile.auth_provider}
          </p>
        </div>
      </div>
    </section>
  );
}
```

Modify `web/src/app/settings/page.tsx`:

```tsx
import { ProductShell } from "@/components/chrome";
import { auth } from "@/lib/auth";
import { getProfile } from "@/lib/users";
import { AccountCard } from "@/components/settings/AccountCard";
import { ApiKeys } from "@/components/settings/ApiKeys";
import { listKeys } from "./actions";

export default async function SettingsPage() {
  const [keys, session] = await Promise.all([listKeys(), auth()]);
  const email = session?.user?.email;
  const profile = email ? await getProfile(email) : null;
  return (
    <ProductShell>
      <main style={{ maxWidth: 860, margin: "0 auto", padding: "32px 22px 64px" }}>
        {profile && <AccountCard profile={profile} />}
        <ApiKeys initialKeys={keys} />
      </main>
    </ProductShell>
  );
}
```

- [ ] **Step 4: Gates + cookieless probe + commit**

```bash
cd web && npx tsc --noEmit && npx eslint src && npx next build
```

Then with `npx next dev -p 3005` running: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3005/terms` → 200, same for `/privacy`; `/nonexistent` → 404.

```bash
git add web/src/app/terms/page.tsx web/src/app/privacy/page.tsx web/src/app/error.tsx web/src/app/not-found.tsx web/src/components/settings/AccountCard.tsx web/src/app/settings/page.tsx
git commit -m "feat(web): release polish — terms, privacy, error/404 pages, settings account card"
```

---

### Task 7: Founder-account reassignment script

**Files:**
- Create: `scripts/reassign_user_email.py`
- Test: `tests/scripts/test_reassign_user_email.py`

**Interfaces:**
- Consumes: `AGF_DATABASE_URL` (same env the other scripts use — check a sibling in `scripts/` for the exact var name and psycopg import style and MIRROR it).
- Produces: `python -m scripts.reassign_user_email <old_email> <new_email> [--execute]` — dry-run by default, prints per-table row counts; with `--execute` moves rows in ONE transaction.

Behavior (one transaction):
1. Both users must exist (`frontend.users`); abort otherwise (the new row is created by the founder's first OAuth sign-in).
2. `UPDATE frontend.users SET role = (old row's role) WHERE email = new` — privileges carry over.
3. For `frontend.api_keys`: `UPDATE ... SET user_email = new WHERE user_email = old`.
4. For `frontend.saved_gems`, `frontend.app_ratings`, `frontend.preference_scores`: move rows, but where the new user already has a row for the same advertiser (PK conflict), DELETE the old user's row instead — the new identity's action wins:

```sql
DELETE FROM frontend.app_ratings o WHERE o.user_email = %s
  AND EXISTS (SELECT 1 FROM frontend.app_ratings n
              WHERE n.user_email = %s AND n.advertiser_id = o.advertiser_id);
UPDATE frontend.app_ratings SET user_email = %s WHERE user_email = %s;
```

(same shape for saved_gems and preference_scores; psycopg `%s` placeholders are fine in Python scripts — the bare-`%` rule applies to `db/schema/*.sql` files only)
5. `DELETE FROM frontend.users WHERE email = old`.
6. Print counts per table; dry-run wraps everything and rolls back.

Test (against `AGF_TEST_DB_URL`, style-match an existing `tests/scripts/` test): seed old user with 2 ratings + 1 saved gem + admin role, new user with 1 conflicting rating; run execute; assert new user has 2 ratings (conflict kept new's), 1 saved gem, role admin, old user gone.

- [ ] **Step 1: Write the failing test** (concrete seed/asserts per the behavior above; mirror an existing tests/scripts DB test's connection fixture)
- [ ] **Step 2: Run it — expect import error**
- [ ] **Step 3: Implement the script** (argparse: `old`, `new`, `--execute`; psycopg connection from the env var the sibling scripts use; single transaction; rollback unless `--execute`)
- [ ] **Step 4: Test passes; full python suite still 0 failed**
- [ ] **Step 5: Commit**

```bash
git add scripts/reassign_user_email.py tests/scripts/test_reassign_user_email.py
git commit -m "feat(scripts): one-off account-email reassignment (founder -> OAuth identity)"
```

---

### Task 8: E2E — programmatic sign-in, onboarding walk, full gates

Controller-run (not a subagent): local `next dev` + local Go API against the integration DB.

**Files:** none committed — the repo has no Playwright test harness in web/ and this task does not introduce one. The steps below are a runbook the controller executes with the Playwright MCP tools + curl against local dev.

- [ ] **Step 1: Stack up** — Go API on :8000 with `AGF_DATABASE_URL` pointed at the integration DB (agf_it @55433), `AGF_API_TOKEN` set to a scratch value; `next dev -p 3005` with matching `AGF_API_INTERNAL_URL=http://localhost:8000/api`, same token, `AUTH_SECRET` set.
- [ ] **Step 2: Seed a test user** — insert into `frontend.users` a bcrypt password user (`e2e@fielddesk.local`, hash of a known password, `onboarded_at NULL`).
- [ ] **Step 3: Programmatic credentials sign-in** — GET `/api/auth/csrf` for the token+cookie, POST `/api/auth/callback/credentials` (form-encoded email/password/csrfToken) keeping the cookie jar, transplant cookies into the Playwright browser context.
- [ ] **Step 4: Walk the flow** — visit `/discover` → expect redirect to `/onboarding`; click through 3 explainer panels; rate a few cards (like/skip mix); click "Skip for now" midway OR finish the deck; expect landing on `/discover` with feed rendered; verify `frontend.app_ratings` rows for `e2e@fielddesk.local` and `onboarded_at IS NOT NULL`; revisit `/onboarding` → redirected to `/discover`.
- [ ] **Step 5: Cookieless probes** — `/terms` 200, `/privacy` 200, `/discover` 307→/login, `/backend/me` 401.
- [ ] **Step 6: Full gates, bare, each its own call** — backend `go test ./...` (with `AGF_IT_DB_URL`), repo-root `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it pytest -q` (0 failed), web `tsc`/`eslint`/`next build`.
- [ ] **Step 7: Clean up test user rows; commit any fixes found.**

---

### Post-plan (controller + founder, not subagent tasks)

1. **Founder action gate:** create the Google OAuth client (consent screen: external/production, app name "Field Desk", scopes email+profile, privacy/terms URLs `https://app.peon.works/privacy|terms`; redirect URIs `https://app.peon.works/api/auth/callback/google` + `http://localhost:3000/api/auth/callback/google`) and the GitHub OAuth app (callback `https://app.peon.works/api/auth/callback/github`). Hand the four values over.
2. **Deploy order:** migration 149 to prod DB → `agf-api` image (`backend/`, `kubectl set image deployment/agf-api api=...`) → add the four AUTH_ vars to the web pod's secret env → `agf-web` image. Both images built from a clean `git worktree add ... HEAD`.
3. **Live verify:** real Google + GitHub sign-in on app.peon.works (new `frontend.users` rows, onboarding shown once, ratings land, feed renders); cookieless probes of /terms /privacy /discover; founder signs in with Google, then run `scripts/reassign_user_email.py founder@fielddesk.local <founder-google-email> --execute` against prod.
