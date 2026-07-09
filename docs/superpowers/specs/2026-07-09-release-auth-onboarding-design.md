# Release readiness: OAuth registration + onboarding — design

**Date:** 2026-07-09
**Status:** approved by founder (open free signup, OAuth-only login, skippable 15-card onboarding deck)

## Goal

Prepare the Field Desk web dashboard (app.peon.works) for public release:

1. Self-serve registration and login with Google and GitHub (OAuth only — the
   email/password form is removed from the UI).
2. An onboarding flow for new users: explain the concept, then run an initial
   review round to bootstrap their preference model.
3. General release readiness: login page as the public front door, legal pages,
   error pages, account section in settings.

## Current state (verified 2026-07-09)

- NextAuth v5, JWT sessions. Single Credentials provider in
  `web/src/lib/auth.ts:9-24`; edge-safe `auth.config.ts` has `providers: []`
  and the `authorized` callback + `PROTECTED` list gates routes (no
  `middleware.ts` file). `verifyLogin` POSTs to the Go API
  `POST /internal/login` with `AGF_API_TOKEN` (`web/src/lib/users.ts:15-30`).
  There is no registration path.
- `frontend.users` (migration 145): `email` PK, `password_hash TEXT NOT NULL`
  (bcrypt), `name`, `role` (admin|member, default member), `created_at`.
  `frontend.api_keys` FKs to users.
- Go API: `/api` group behind `KeyAuth` — personal `fd_` key OR internal
  `AGF_API_TOKEN` + `X-User-Email` (`backend/middlewares/keyauth.go:39-84`).
  The web proxy (`web/src/app/backend/[...path]/route.ts`) injects
  `X-User-Email` from the session and 401s without one.
- Data layer is already multi-user: `saved_gems`, `app_ratings`,
  `preference_scores` are keyed by `(user_email, advertiser_id)`.
- Preference model: serve-time scoring (Go port of ContentPreferenceModel).
  Coldstart below `MinLikes = 8` liked apps
  (`backend/internal/reco/content_model.go:26`) → serving substitutes
  `gem_score/100` (`backend/repositories/preference_repo.go:95`). The review
  deck (`/review`) fetches `GET /preference-queue` and submits
  `POST /ratings/:id` — both already work for a zero-label user.
- Not release-ready: login form prefills `founder@fielddesk.local` and says
  "Internal tool"; no terms/privacy pages; no global `error.tsx` /
  `not-found.tsx`; settings shows only API keys.

## Decisions (locked with founder)

- **Open free signup** — anyone with a Google or GitHub account; no waitlist,
  no invite gate, no billing.
- **OAuth only** — remove the email/password form from the login page. The
  backend `POST /internal/login` endpoint STAYS (no UI): it is the vehicle for
  Playwright e2e tests and emergency access.
- **Onboarding review round: fixed deck of ~15 cards, skippable.** Users who
  like 8+ apps get a personalized feed immediately; others land on the
  generic GemScore ranking until they hit 8 likes organically in /review.
- **No marketing landing page** in this scope; `/` keeps redirecting into the
  app and the login page doubles as the front door.

## Design

### 1. DB migration 148 — OAuth-capable users

`db/schema/148_oauth_users.sql`:

- `ALTER TABLE frontend.users ALTER COLUMN password_hash DROP NOT NULL;`
- `ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'password'`
  with `CHECK (auth_provider IN ('password','google','github'))`.
- `ADD COLUMN avatar_url TEXT` (nullable).
- `ADD COLUMN onboarded_at TIMESTAMPTZ` (nullable). Existing rows are
  backfilled `onboarded_at = now()` (existing users must not be funneled into
  onboarding).

Reminder from lessons: no bare `%` anywhere in the migration (psycopg3
placeholder scan) — use `mod()` if needed.

### 2. Go API — two endpoints

- `POST /internal/oauth-login` (internal-token guarded, same group as
  `/internal/login`): body `{email, name, provider, avatar_url}`. Upserts into
  `frontend.users` (INSERT … ON CONFLICT (email) DO UPDATE name/avatar_url;
  `auth_provider` set on insert, not overwritten on conflict so a password
  account that later signs in with Google keeps working). Returns
  `{email, name, role, onboarded: bool}`.
- `GET /me` (normal `/api` group, KeyAuth): returns the caller's
  `{email, name, role, avatar_url, auth_provider, onboarded_at}`.
- `POST /me/onboarded` (normal `/api` group): sets `onboarded_at = now()` for
  the caller. Idempotent.

### 3. NextAuth — Google + GitHub providers

In `web/src/lib/auth.ts`:

- Add `Google` and `GitHub` providers (env: `AUTH_GOOGLE_ID`,
  `AUTH_GOOGLE_SECRET`, `AUTH_GITHUB_ID`, `AUTH_GITHUB_SECRET`; NextAuth v5
  reads these names automatically). `AUTH_URL=https://app.peon.works` in prod.
- `signIn` callback: for OAuth sign-ins, require a verified email from the
  provider (GitHub: primary verified email; Google: `email_verified`). Call
  `POST /internal/oauth-login`; deny sign-in if the upsert fails (fail
  closed). Merge returned `role` into the JWT (same shape as today:
  `id = email`).
- Credentials provider stays registered (e2e/emergency) but the login page no
  longer renders a form for it.
- The `PROTECTED` list in `auth.config.ts` gains `/onboarding`.

**Founder account migration:** a one-off script
(`scripts/reassign_user_email.py <old> <new>`) re-points `frontend.users`,
`api_keys`, `saved_gems`, `app_ratings`, `preference_scores` rows from
`founder@fielddesk.local` to the founder's Google email after his first OAuth
sign-in. Run manually once; not part of the migration.

**OAuth app registration (founder action, needed before live verify):**

- Google Cloud console → OAuth consent screen (external, production) +
  OAuth client (web), redirect URI
  `https://app.peon.works/api/auth/callback/google`. Consent screen requires
  the /terms and /privacy URLs.
- GitHub → Settings → Developer settings → OAuth App, callback
  `https://app.peon.works/api/auth/callback/github`.
- Secrets land in the `agf-secrets` k8s secret (web pod env) and in
  `web/.env.local` for local dev (localhost callback variants registered too).

### 4. Onboarding flow — `/onboarding`

Routing rule: `ProductShell` (the authenticated chrome) fetches `GET /me`
once per mount; if `onboarded_at` is null and the current path is not
`/onboarding`, redirect there. (Reads the DB, not the JWT — no stale-token
problem after completion.)

Page structure, two phases in one client component:

- **Phase A — concept explainer.** Three short panels (Next/Back), plain
  professional copy per product voice rules, grounded in real product nouns:
  1. What Field Desk does: finds mobile apps that are quietly scaling paid
     Meta ads before they show up on the charts.
  2. GemScore: how the ranking condenses ad longevity, growth, and reach into
     one number; cohort context.
  3. Review: rating apps teaches Field Desk your taste; your Discover feed
     reranks around what you like.
- **Phase B — "Calibrate your feed".** A fixed deck of 15 cards reusing the
  existing review components (`ReviewDeck`/`ReviewCard` — extracted/reused,
  not forked) against the existing `GET /preference-queue?limit=15` +
  `POST /ratings/:id`. Progress "x of 15". A quiet "Skip for now" link is
  always visible. A one-line note states that ~8 likes unlock a personalized
  feed.
- Finishing the deck or skipping calls `POST /me/onboarded` then routes to
  `/discover`.

New users have zero `preference_scores` rows; the serve-time coldstart
(`gem_score/100`) already handles the deck fetch — no backend change needed
for scoring.

### 5. Release readiness

- **Login page** (`/login`) becomes the front door: Field Desk wordmark, one
  plain sentence about the product, "Continue with Google" / "Continue with
  GitHub" buttons, footer links to /terms and /privacy. Remove the
  "Internal tool" copy, the prefilled founder email, and the password form.
- **Legal pages:** static `/terms` and `/privacy` (public — added to the
  auth-exempt list). Plain, honest, short; founder contact email; no
  boilerplate fluff. These URLs are prerequisites for the Google consent
  screen.
- **Error pages:** global `web/src/app/error.tsx` and `not-found.tsx` in the
  product voice.
- **Settings:** account section above API keys — avatar, name, email,
  provider badge, sign-out. No profile editing in this scope.

### 6. Error handling

- OAuth callback failure / denied consent → NextAuth error query param →
  login page renders a short inline error line.
- `/internal/oauth-login` unreachable during sign-in → sign-in denied (fail
  closed), login page error line.
- Onboarding deck fetch failure → explainer still works; deck phase shows the
  existing feed error state with retry; skip always available.
- `GET /me` failure in ProductShell → do not redirect-loop; render the app
  (fail open for navigation, since onboarding is a nicety, not a security
  boundary).

### 7. Testing

- Go: unit tests for `oauth-login` upsert semantics (insert vs conflict,
  provider not clobbered), `/me`, `/me/onboarded` idempotency.
- Web: typecheck, eslint, `next build`; component test for the explainer/deck
  phase transitions if a test harness exists (node --test per repo precedent).
- Migration: applies to a fresh DB (placeholder-safe lint already exists).
- E2E (live-adjacent): Playwright against local dev — password test user
  signed in programmatically (fetch the NextAuth CSRF token, POST
  `/api/auth/callback/credentials`; no UI form exists), walk /onboarding
  explainer → 15-card deck →
  ratings land in `frontend.app_ratings` → `onboarded_at` set → redirect to
  /discover and no re-redirect on next visit.
- Live verify after GKE deploy: real Google + GitHub sign-in on
  app.peon.works, new user row created, onboarding shown once, feed loads;
  cookieless probes of /terms, /privacy (200) and /discover (redirect to
  /login).

## Out of scope

- Billing/paywall, email+password self-signup, email verification flows,
  marketing landing page, profile editing, account deletion UI (manual via
  backend for now), Discover "foryou" stale-preference_scores fix (separate
  known issue).
