# Field Desk MCP — multi-user hosted MCP server (design)

**Date:** 2026-07-02
**Status:** approved (design review with founder)
**Supersedes:** the local read-only `src/agf_mcp/` scraping-health MCP (stdio, Mac-local,
cloud-sql-proxy + ADC), which is retired once this reaches tool parity.

## Problem

The current MCP is a single-user dev tool: it runs on the founder's Mac over stdio and
reaches prod through a local cloud-sql-proxy authenticated by personal ADC — which
expires, taking the MCP down while the hosted product keeps working. The product
direction is that **each Field Desk user has their own account**, and a user's MCP
client (claude.ai connectors, Claude Code, Cursor) should connect to a hosted server
and query **their own** preference data (ratings, saved gems, taste profile) plus the
shared gem directory. The local design cannot serve that: no auth, no user scoping, no
public endpoint, and a credential that rots.

## Decision summary

| Decision | Choice |
|---|---|
| Audience | Future Field Desk customers; every user has their own account |
| MCP auth | Personal API keys now; OAuth 2.1 later (out of scope) |
| Accounts | Move from `AUTH_USERS` env var to a `frontend.users` DB table |
| Placement | Inside the existing Go API (streamable HTTP at `api.peon.works/mcp`) |
| SDK | `github.com/modelcontextprotocol/go-sdk` v1.6.1 (`StreamableHTTPHandler` — verified live 2026-07-02) |
| Old Python MCP | Retire after admin-tool parity |

## Architecture

One new surface on the existing Go API pod (GKE ns `agf`, Cloud SQL sidecar +
Workload Identity — no new credential machinery):

```
MCP client ── Authorization: Bearer fd_<key> ──▶ api.peon.works/mcp
                                                    │  key middleware: hash → frontend.api_keys
                                                    │  → frontend.users → {email, role} in ctx
                                                    ▼
                                             MCP Go SDK StreamableHTTPHandler
                                                    │  tools read user from ctx only
                                                    ▼
                                             existing GORM/raw SQL → Cloud SQL (frontend.*, public.*, discovery.*)
```

- Mounted at a path on the existing host: no new DNS, ManagedCertificate, or
  deployment. (`gin.WrapH` on the SDK's `http.Handler`.)
- No tool accepts a user parameter. Scoping comes exclusively from the resolved key.

## Accounts & keys (migration 145 — 144 is taken by `creatives_ad_id_index`)

```sql
frontend.users (
  email         TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,          -- bcrypt
  name          TEXT,
  role          TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)

frontend.api_keys (
  key_hash     TEXT PRIMARY KEY,        -- SHA-256 hex of the full plaintext key
  user_email   TEXT NOT NULL REFERENCES frontend.users(email),
  label        TEXT NOT NULL DEFAULT '',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ,
  revoked_at   TIMESTAMPTZ
)
```

- Key format: `fd_` + 32 random bytes, base62. Plaintext shown exactly once at mint.
- Web login (NextAuth Credentials `authorize`) switches from the `AUTH_USERS` env var
  to `frontend.users`. A one-off seed script migrates the current env accounts
  (founder = `admin`). No public signup — admin creates accounts.
- Web **Settings → API keys** page: list (label, created, last used), mint, revoke.
  Copy is plain and grounded per product-voice rules.

## Auth flow

Middleware on `/mcp` (and reused for `/api`):

1. Read `Authorization: Bearer fd_…`; missing/malformed → HTTP 401 (before any MCP
   handling).
2. SHA-256 the token → select from `api_keys` where `key_hash` matches and
   `revoked_at IS NULL`, join `users` → `{email, role}` into request context.
3. Touch `last_used_at` (best-effort, async).
4. Unknown/revoked key → 401.

**Rides along:** the currently-open `/api` GETs get closed (the flagged residual from
the GKE deploy). `/api` accepts either a personal `fd_` key or the existing static
internal token (`AGF_API_TOKEN`). The web app's own browser calls move to a
session-checked Next.js proxy route (`/backend/*` → cluster-internal API URL +
internal token + `X-User-Email`), because a `NEXT_PUBLIC_*` token would be baked into
the public JS bundle and Go cannot practically decrypt NextAuth's JWE session cookie.
`X-User-Email` is trusted **only** on internal-token requests.

## Tools

| Tool | Access | Behavior |
|---|---|---|
| `my_taste_profile` | member | rating counts (like/neutral/dislike), top liked cohorts/niches, top signals driving likes (aggregated from `app_ratings.features` snapshots) |
| `my_ratings` | member | caller's `frontend.app_ratings` rows; filters: rating, cohort; paginated |
| `my_saved_gems` | member | caller's `frontend.saved_gems` watchlist with stage, verdict, tags, notes |
| `recommended_gems` | member | the `frontend.emerging_scaling_products` feed reranked by cosine(app embedding from `frontend.app_embeddings`, caller's liked centroid). Centroid + cosine computed in Go, in memory (~1.4k × 768d is trivial). No likes yet → fall back to GemScore order and say so in the payload. Apps without an embedding (~47% today) rank below embedded ones, GemScore order, flagged `unscored: true` |
| `search_gems` | member | shared directory (`frontend.emerging_scaling_products` view): name/cohort/score/store filters, paginated |
| `gem_detail` | member | one gem: scores, axes, ads summary, funnel URL, store links (matview read — resolves non-representative advertiser ids, same rule as the dossier) |
| `scraping_health` | **admin** | the 4 ops metrics from Python `metrics.py` (engine pulse, discovery rate, idea diversity, overview), ported to Go SQL |

Member key calling an admin tool → structured MCP error ("This tool requires an admin
account."), not a 401.

## Error handling & limits

- Tools return structured MCP errors with plain, actionable messages ("No ratings yet —
  rate apps in the review queue first"); never raw SQL/driver errors.
- Per-key in-memory token-bucket rate limit, 60 req/min; exceeding returns a
  retry-after error. (Single-pod deployment today, so in-memory is sufficient.)
- Revoked mid-session: next request 401s (lookup is per-request).

## Testing

- **Go unit/integration** (test DB `agf_test_pg` @55433): key hash/lookup; middleware
  matrix (valid / revoked / missing / malformed / member-hits-admin-tool); each tool's
  SQL against seeded fixtures; centroid + cosine math with fixture embeddings;
  no-likes fallback.
- **Web:** login against DB users; key mint (plaintext once) / revoke flow.
- **Live e2e gate (required before "done"):** deploy → mint a key via the real
  Settings page → add the connector to Claude Code by URL → live calls:
  `my_taste_profile`, `recommended_gems`, and `scraping_health` with the founder
  (admin) key; `scraping_health` with a member key expecting denial; a cookieless
  GET to a previously-open `/api` route expecting 401.

## Out of scope (later)

OAuth 2.1 (dynamic client registration + PKCE), public signup, per-key scopes,
usage metering/billing, multi-pod shared rate limiting.
