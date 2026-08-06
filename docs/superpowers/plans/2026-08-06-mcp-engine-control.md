# MCP Engine Control and History Tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add seven tools to the hosted Field Desk MCP so Claude Code can read query yield, steer the keyword flywheel, run an on-demand Ad Library pull at a chosen depth, and fetch creative bytes.

**Architecture:** Every tool is a thin handler in `backend/mcpserver` over a raw-SQL repository function. Writes reuse the existing job queue (`public.pipeline_jobs`) and per-round settings JSONB rather than introducing any new control plane. Admin tools are registered only on an admin's server and re-check the role inside the handler.

**Tech Stack:** Go 1.x, gin, gorm (raw SQL only), `github.com/modelcontextprotocol/go-sdk/mcp`, Postgres.

Spec: `docs/superpowers/specs/2026-08-06-mcp-engine-control-design.md`

## Global Constraints

- **Product copy voice:** plain, clear, professional. Tool descriptions and error strings are short and honest. No marketing words ("leverage", "seamless", "unlock", "empower", "supercharge", "delightful"), no exclamation marks.
- **Error strings:** lowercase sentence, ending in a period, never leaking a driver error. Match the existing style: `"something went wrong reading your ratings."`
- **Repositories use raw SQL through gorm**, wrapped in `db.WithReadTx` / `db.WithWriteTx`. No gorm model/AutoMigrate calls.
- **Admin gating is two-layer**, both required: (a) register the tool inside the `if u.Role == "admin"` block in `buildServer`, (b) re-check `if h.u.Role != "admin"` as the first statement of the handler and return `errors.New("This tool requires an admin account.")`.
- **`clamp_hunt_settings` (Python, `src/agf/db/hunts.py`) stays the single bound authority** for hunt settings. Go passes settings through and does not re-clamp. Bounds for documentation only: `page_cap` 1–100, `patience` 1–10, `listing_pages` 1–30, `min_pages` 1–30.
- **Integration tests require `AGF_IT_DB_URL`.** With it unset, `TestMain` calls `os.Exit(0)` and the suite reports success having run nothing. Every verification step below must confirm tests actually ran. Local DSN: `postgres://agf:agf@localhost:55433/agf_it?sslmode=disable`.
- **Test emails must be per-run unique** (`fmt.Sprintf("...-%d@test.local", time.Now().UnixNano())`) — the test DB is shared across runs.
- **Seeded test rows must use a unique prefix and be cleaned up** with a `defer db.DB().Exec("DELETE ... WHERE ... LIKE ?", prefix+"%")`, matching the existing `MCPHEALTH-` pattern.
- **Commit to `main` directly.** No feature branches.

---

### Task 1: Split mcpserver.go by domain

Pure refactor, no behavior change. `mcpserver.go` is 243 lines and this plan roughly triples it. Establish the file boundaries before adding anything.

**Files:**
- Modify: `backend/mcpserver/mcpserver.go` — keep the package doc, `Handler`, `buildServer`, the `handlers` struct, and `clampLimit`. Remove the seven tool handler bodies.
- Create: `backend/mcpserver/tools_member.go` — the six member handlers (`tasteProfile`, `myRatings`, `mySavedGems`, `recommended`, `searchGems`, `gemDetail`) and their input structs.
- Create: `backend/mcpserver/tools_engine.go` — `scrapingHealth` and its input struct move here.
- Test: `backend/internal/integration/mcp_test.go` (unchanged — it is the regression gate)

**Interfaces:**
- Consumes: nothing.
- Produces: `handlers` struct (field `u middlewares.AuthedUser`) and `func clampLimit(n int) int`, both used by every later task. Both stay in `mcpserver.go`.

- [ ] **Step 1: Confirm the current suite passes before touching anything**

```bash
cd /Users/edaslakavicius/Projects/emerging-app-finder
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run TestMCPServer -v
```

Expected: `PASS`, and the output lists the subtests (`member tools/list excludes scraping_health`, etc.). If you see `ok ... 0.0s [no tests to run]` or no subtest lines, `AGF_IT_DB_URL` did not reach `TestMain` — fix that before continuing. A skip is not a pass.

- [ ] **Step 2: Create `tools_member.go`**

Move — do not rewrite — the six member handlers and their input structs out of `mcpserver.go`. Header:

```go
// Package-local: the member tool handlers. Every one reads identity from h.u
// (never from tool input) and returns structured output the SDK serializes as
// JSON text content.
package mcpserver

import (
	"context"
	"errors"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)
```

Then the moved blocks verbatim: `tasteProfileIn`/`tasteProfile`, `myRatingsIn`/`myRatings`, `mySavedGemsIn`/`mySavedGems`, `recommendedIn`/`recommended`, `searchGemsIn`/`searchGems`, `gemDetailIn`/`gemDetail`, keeping their existing `// ---- name ----` comment banners and the `gem_detail` matview comment.

- [ ] **Step 3: Create `tools_engine.go`**

```go
// Package-local: admin-only engine tools. These read and steer the shared
// discovery engine, so every handler re-checks the admin role even though
// buildServer only registers them for admins.
package mcpserver

import (
	"context"
	"errors"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)
```

Move `scrapingHealthIn` and `scrapingHealth` here verbatim, including the defense-in-depth comment.

- [ ] **Step 4: Trim `mcpserver.go`**

What remains: the package doc comment, imports, `Handler`, `buildServer`, the `handlers` struct with its comment, and `clampLimit`. Drop now-unused imports (`db`, `repositories` are no longer referenced from this file).

- [ ] **Step 5: Build and vet**

```bash
go build ./backend/... && go vet ./backend/mcpserver/
```

Expected: no output from either.

- [ ] **Step 6: Re-run the MCP integration test**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run TestMCPServer -v
```

Expected: `PASS` with the same subtests as Step 1. A pure move must change nothing.

- [ ] **Step 7: Commit**

```bash
git add backend/mcpserver/
git commit -m "refactor(mcp): split mcpserver.go into member and engine tool files" -- backend/mcpserver/
```

---

### Task 2: keyword_yield — which queries produced keepers

**Files:**
- Create: `backend/repositories/keyword_repo.go`
- Create: `backend/mcpserver/tools_history.go`
- Modify: `backend/mcpserver/mcpserver.go` (register the tool in the admin block)
- Modify: `backend/models/models.go` (append `KeywordYieldRow`)
- Test: `backend/internal/integration/mcp_test.go`

**Interfaces:**
- Consumes: `handlers`, `clampLimit` from Task 1.
- Produces:
  - `models.KeywordYieldRow`
  - `repositories.KeywordYieldParams{Source string; Status string; MinSweeps int; Limit int; Offset int}`
  - `repositories.KeywordYield(ctx context.Context, g *gorm.DB, p KeywordYieldParams) ([]models.KeywordYieldRow, error)`

- [ ] **Step 1: Write the failing test**

Append this subtest inside `TestMCPServer` in `backend/internal/integration/mcp_test.go`, immediately before the `t.Run("no bearer is rejected before handshake", ...)` block. It seeds one keyword with two candidates (one kept, one dropped) and asserts the keeper count is 1, not 2.

```go
	t.Run("keyword_yield reports keeper counts per keyword", func(t *testing.T) {
		kw := fmt.Sprintf("mcp-yield-kw-%d", time.Now().UnixNano())
		pfx := fmt.Sprintf("MCPYIELD-%d-", time.Now().UnixNano())
		require.NoError(t, db.DB().Exec(
			`INSERT INTO discovery.dynamic_keywords (keyword, source, status, sweeps_used, new_candidates_total)
			 VALUES (?, 'mcp-test', 'active', 3, 2)`, kw).Error)
		require.NoError(t, db.DB().Exec(
			`INSERT INTO discovery.candidates (page_id, store, store_id, source_keyword, status)
			 VALUES (?, 'ios', ?, ?, 'kept'), (?, 'ios', ?, ?, 'dropped')`,
			pfx+"1", pfx+"1", kw, pfx+"2", pfx+"2", kw).Error)
		defer db.DB().Exec(`DELETE FROM discovery.candidates WHERE page_id LIKE ?`, pfx+"%")
		defer db.DB().Exec(`DELETE FROM discovery.dynamic_keywords WHERE keyword = ?`, kw)

		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_yield",
			Arguments: map[string]any{"source": "mcp-test"},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Keywords []struct {
				Keyword    string `json:"keyword"`
				Status     string `json:"status"`
				SweepsUsed int    `json:"sweeps_used"`
				Candidates int    `json:"candidates"`
				Keepers    int    `json:"keepers"`
			} `json:"keywords"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Len(t, out.Keywords, 1, "source filter isolates the seeded keyword")
		require.Equal(t, kw, out.Keywords[0].Keyword)
		require.Equal(t, "active", out.Keywords[0].Status)
		require.Equal(t, 3, out.Keywords[0].SweepsUsed)
		require.Equal(t, 2, out.Keywords[0].Candidates, "both candidates counted")
		require.Equal(t, 1, out.Keywords[0].Keepers, "only the kept one")
	})

	t.Run("member cannot see or call keyword_yield", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		require.False(t, toolNames(sess)["keyword_yield"], "admin tool must not be listed")
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{Name: "keyword_yield"})
		if err == nil {
			require.True(t, res.IsError, "member keyword_yield must not succeed")
		}
	})
```

- [ ] **Step 2: Run it to verify it fails**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer/keyword_yield' -v
```

Expected: FAIL — the tool is not registered, so `CallTool` returns an error result.

- [ ] **Step 3: Add the model**

Append to `backend/models/models.go`:

```go
// KeywordYieldRow is one discovery.dynamic_keywords row joined to its
// candidate outcomes: how many candidates the keyword surfaced and how many
// of those survived triage. Keepers is the yield number that matters — a
// keyword can flood candidates and keep none.
type KeywordYieldRow struct {
	Keyword      string     `json:"keyword"`
	Source       string     `json:"source"`
	Status       string     `json:"status"`
	SweepsUsed   int        `json:"sweeps_used"`
	Candidates   int        `json:"candidates"`
	Keepers      int        `json:"keepers"`
	DryStreak    int        `json:"dry_streak"`
	RestCount    int        `json:"rest_count"`
	LastSweptAt  *time.Time `json:"last_swept_at"`
	NextEligible *time.Time `json:"next_eligible_at"`
}
```

- [ ] **Step 4: Write the repository**

Create `backend/repositories/keyword_repo.go`:

```go
// Package repositories — discovery.dynamic_keywords reads and lifecycle writes.
//
// The keyword flywheel's whole control surface is these rows: status gates
// whether the sweep picks a keyword up, next_eligible_at gates when a resting
// one wakes, dry_streak drives automatic retirement. Changing a row takes
// effect on the next flywheel tick; nothing here needs a worker restart.
package repositories

import (
	"context"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// KeywordYieldParams filters the yield read. Zero values mean "no filter".
type KeywordYieldParams struct {
	Source    string
	Status    string
	MinSweeps int
	Limit     int
	Offset    int
}

// KeywordYield returns keywords ranked by keeper yield, highest first.
//
// The candidate counts come from a correlated aggregate over
// discovery.candidates rather than a JOIN + GROUP BY: a keyword with thousands
// of candidates would otherwise fan the row set out before grouping.
func KeywordYield(ctx context.Context, g *gorm.DB, p KeywordYieldParams) ([]models.KeywordYieldRow, error) {
	var rows []models.KeywordYieldRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT k.keyword,
			        k.source,
			        k.status,
			        k.sweeps_used,
			        k.dry_streak,
			        k.rest_count,
			        k.last_swept_at,
			        k.next_eligible_at,
			        COALESCE(c.candidates, 0) AS candidates,
			        COALESCE(c.keepers, 0)    AS keepers
			   FROM discovery.dynamic_keywords k
			   LEFT JOIN LATERAL (
			        SELECT count(*)                                  AS candidates,
			               count(*) FILTER (WHERE status = 'kept')   AS keepers
			          FROM discovery.candidates
			         WHERE source_keyword = k.keyword
			   ) c ON TRUE
			  WHERE (? = '' OR k.source = ?)
			    AND (? = '' OR k.status = ?)
			    AND k.sweeps_used >= ?
			  ORDER BY keepers DESC, candidates DESC, k.keyword
			  LIMIT ? OFFSET ?`,
			p.Source, p.Source, p.Status, p.Status, p.MinSweeps, p.Limit, p.Offset,
		).Scan(&rows).Error
	})
	return rows, err
}
```

- [ ] **Step 5: Write the tool handler**

Create `backend/mcpserver/tools_history.go`:

```go
// Package-local: read-only history tools. These answer "what has the engine
// found, and which queries produced it" — the inputs to steering decisions
// made through tools_engine.go.
package mcpserver

import (
	"context"
	"errors"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// ---- keyword_yield (admin only) ----

type keywordYieldIn struct {
	Source    string `json:"source,omitempty" jsonschema:"filter by mint source (e.g. gem_copy, frame_mint, experiment_angles, mcp)"`
	Status    string `json:"status,omitempty" jsonschema:"pending, trial, active, resting, or retired"`
	MinSweeps int    `json:"min_sweeps,omitempty" jsonschema:"only keywords swept at least this many times"`
	Limit     int    `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset    int    `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) keywordYield(ctx context.Context, _ *mcp.CallToolRequest, in keywordYieldIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	rows, err := repositories.KeywordYield(ctx, db.DB(), repositories.KeywordYieldParams{
		Source:    in.Source,
		Status:    in.Status,
		MinSweeps: in.MinSweeps,
		Limit:     clampLimit(in.Limit),
		Offset:    in.Offset,
	})
	if err != nil {
		return nil, nil, errors.New("something went wrong reading keyword yield.")
	}
	if len(rows) == 0 {
		return nil, map[string]any{"keywords": rows, "note": "No keywords matched those filters."}, nil
	}
	return nil, map[string]any{"keywords": rows}, nil
}
```

- [ ] **Step 6: Register the tool**

In `backend/mcpserver/mcpserver.go`, inside the existing `if u.Role == "admin" {` block, after the `scraping_health` registration:

```go
		mcp.AddTool(s, &mcp.Tool{Name: "keyword_yield",
			Description: "Sweep keywords ranked by how many candidates survived triage. Shows status, sweeps used, candidates, keepers, and dry streak."},
			h.keywordYield)
```

- [ ] **Step 7: Run the test to verify it passes**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer' -v
```

Expected: PASS, including both new subtests and every pre-existing one.

- [ ] **Step 8: Commit**

```bash
git add backend/repositories/keyword_repo.go backend/mcpserver/ backend/models/models.go backend/internal/integration/mcp_test.go
git commit -m "feat(mcp): keyword_yield tool — keeper yield per sweep keyword" -- backend/repositories/keyword_repo.go backend/mcpserver/ backend/models/models.go backend/internal/integration/mcp_test.go
```

---

### Task 3: keyword_control — steer the flywheel

**Files:**
- Modify: `backend/repositories/keyword_repo.go` (append)
- Modify: `backend/mcpserver/tools_engine.go` (append)
- Modify: `backend/mcpserver/mcpserver.go` (register)
- Modify: `backend/models/models.go` (append `KeywordControlResult`)
- Test: `backend/internal/integration/mcp_test.go`

**Interfaces:**
- Consumes: `models.KeywordYieldRow` conventions from Task 2.
- Produces:
  - `models.KeywordControlResult{Keyword string; Before string; After string}`
  - `repositories.ControlKeywords(ctx context.Context, g *gorm.DB, action string, keywords []string, cooldownDays int) ([]models.KeywordControlResult, []string, error)` — returns per-keyword before/after plus the list of keywords that did not exist (for `seed`, that list is always empty because they get created).

- [ ] **Step 1: Write the failing test**

Append inside `TestMCPServer`, before the `no bearer` subtest:

```go
	t.Run("keyword_control seeds and retires keywords", func(t *testing.T) {
		kw := fmt.Sprintf("mcp-ctl-kw-%d", time.Now().UnixNano())
		defer db.DB().Exec(`DELETE FROM discovery.dynamic_keywords WHERE keyword = ?`, kw)

		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()

		// seed creates the row as a trial keyword sourced 'mcp'.
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "seed", "keywords": []string{kw}},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var status, source string
		require.NoError(t, db.DB().Raw(
			`SELECT status, source FROM discovery.dynamic_keywords WHERE keyword = ?`, kw,
		).Row().Scan(&status, &source))
		require.Equal(t, "trial", status)
		require.Equal(t, "mcp", source)

		// retire flips it and reports the before/after pair.
		res, err = sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "retire", "keywords": []string{kw}},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Changed []struct {
				Keyword string `json:"keyword"`
				Before  string `json:"before"`
				After   string `json:"after"`
			} `json:"changed"`
			Unknown []string `json:"unknown"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Len(t, out.Changed, 1)
		require.Equal(t, "trial", out.Changed[0].Before)
		require.Equal(t, "retired", out.Changed[0].After)
		require.Empty(t, out.Unknown)

		require.NoError(t, db.DB().Raw(
			`SELECT status FROM discovery.dynamic_keywords WHERE keyword = ?`, kw,
		).Row().Scan(&status))
		require.Equal(t, "retired", status, "the DB row actually moved")
	})

	t.Run("keyword_control reports unknown keywords without failing", func(t *testing.T) {
		missing := fmt.Sprintf("mcp-absent-%d", time.Now().UnixNano())
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "activate", "keywords": []string{missing}},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "an unknown keyword is reported, not an error")

		var out struct {
			Changed []any    `json:"changed"`
			Unknown []string `json:"unknown"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Empty(t, out.Changed)
		require.Equal(t, []string{missing}, out.Unknown)
	})

	t.Run("keyword_control rejects an unknown action", func(t *testing.T) {
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "delete", "keywords": []string{"x"}},
		})
		if err == nil {
			require.True(t, res.IsError, "unknown action must be rejected")
		}
	})

	t.Run("member cannot see or call keyword_control", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		require.False(t, toolNames(sess)["keyword_control"], "write tool must not be listed")
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "keyword_control",
			Arguments: map[string]any{"action": "retire", "keywords": []string{"x"}},
		})
		if err == nil {
			require.True(t, res.IsError, "member keyword_control must not succeed")
		}
	})
```

- [ ] **Step 2: Run it to verify it fails**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer/keyword_control' -v
```

Expected: FAIL — tool not registered.

- [ ] **Step 3: Add the model**

Append to `backend/models/models.go`:

```go
// KeywordControlResult is one keyword's lifecycle transition, reported back so
// the caller can see what actually moved rather than trusting the request.
type KeywordControlResult struct {
	Keyword string `json:"keyword"`
	Before  string `json:"before"`
	After   string `json:"after"`
}
```

- [ ] **Step 4: Write the repository function**

Append to `backend/repositories/keyword_repo.go`:

```go
// ControlKeywords applies one lifecycle action to a batch of keywords and
// reports each row's before/after status. Keywords that do not exist are
// returned in the second slice instead of failing the call — a batch of
// winners pasted from a yield read should not be rejected wholesale because
// one entry has since been pruned.
//
// 'seed' is the exception: it inserts missing keywords as trial rows sourced
// 'mcp', so its unknown list is always empty. An existing keyword re-seeded is
// left alone (ON CONFLICT DO NOTHING) and reported with before == after.
func ControlKeywords(ctx context.Context, g *gorm.DB, action string, keywords []string, cooldownDays int) ([]models.KeywordControlResult, []string, error) {
	var changed []models.KeywordControlResult
	var unknown []string

	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		for _, kw := range keywords {
			var before string
			row := tx.Raw(
				`SELECT status FROM discovery.dynamic_keywords WHERE keyword = ?`, kw,
			).Row()
			if scanErr := row.Scan(&before); scanErr != nil {
				if action != "seed" {
					unknown = append(unknown, kw)
					continue
				}
				before = ""
			}

			var after string
			switch action {
			case "seed":
				if before != "" {
					changed = append(changed, models.KeywordControlResult{Keyword: kw, Before: before, After: before})
					continue
				}
				if e := tx.Exec(
					`INSERT INTO discovery.dynamic_keywords (keyword, source, status)
					 VALUES (?, 'mcp', 'trial')
					 ON CONFLICT (keyword) DO NOTHING`, kw).Error; e != nil {
					return e
				}
				after = "trial"
			case "activate":
				if e := tx.Exec(
					`UPDATE discovery.dynamic_keywords
					    SET status = 'active', dry_streak = 0, next_eligible_at = NULL
					  WHERE keyword = ?`, kw).Error; e != nil {
					return e
				}
				after = "active"
			case "rest":
				if e := tx.Exec(
					`UPDATE discovery.dynamic_keywords
					    SET status = 'resting',
					        next_eligible_at = now() + make_interval(days => ?),
					        rest_count = rest_count + 1
					  WHERE keyword = ?`, cooldownDays, kw).Error; e != nil {
					return e
				}
				after = "resting"
			case "retire":
				if e := tx.Exec(
					`UPDATE discovery.dynamic_keywords SET status = 'retired' WHERE keyword = ?`, kw).Error; e != nil {
					return e
				}
				after = "retired"
			}
			changed = append(changed, models.KeywordControlResult{Keyword: kw, Before: before, After: after})
		}
		return nil
	})
	return changed, unknown, err
}
```

- [ ] **Step 5: Write the tool handler**

Append to `backend/mcpserver/tools_engine.go`:

```go
// ---- keyword_control (admin only) ----

// defaultKeywordCooldownDays matches the flywheel's own resting cooldown so a
// manual rest behaves like an automatic one unless the caller overrides it.
const defaultKeywordCooldownDays = 7

type keywordControlIn struct {
	Action       string   `json:"action" jsonschema:"activate, rest, retire, or seed"`
	Keywords     []string `json:"keywords" jsonschema:"the keywords to act on"`
	CooldownDays int      `json:"cooldown_days,omitempty" jsonschema:"rest cooldown in days (default 7); ignored by other actions"`
}

func (h *handlers) keywordControl(ctx context.Context, _ *mcp.CallToolRequest, in keywordControlIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	switch in.Action {
	case "activate", "rest", "retire", "seed":
	default:
		return nil, nil, errors.New("action must be activate, rest, retire, or seed.")
	}
	if len(in.Keywords) == 0 {
		return nil, nil, errors.New("keywords must not be empty.")
	}
	cooldown := in.CooldownDays
	if cooldown <= 0 {
		cooldown = defaultKeywordCooldownDays
	}
	changed, unknown, err := repositories.ControlKeywords(ctx, db.DB(), in.Action, in.Keywords, cooldown)
	if err != nil {
		return nil, nil, errors.New("something went wrong updating those keywords.")
	}
	out := map[string]any{"changed": changed, "unknown": unknown}
	out["note"] = "Takes effect on the next flywheel tick."
	return nil, out, nil
}
```

- [ ] **Step 6: Register the tool**

In `buildServer`'s admin block, after `keyword_yield`:

```go
		mcp.AddTool(s, &mcp.Tool{Name: "keyword_control",
			Description: "Change sweep keyword lifecycle: activate, rest, retire, or seed new trial keywords. Applies on the next flywheel tick."},
			h.keywordControl)
```

- [ ] **Step 7: Run the tests**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer' -v
```

Expected: PASS, all subtests.

- [ ] **Step 8: Commit**

```bash
git add backend/repositories/keyword_repo.go backend/mcpserver/ backend/models/models.go backend/internal/integration/mcp_test.go
git commit -m "feat(mcp): keyword_control tool — activate, rest, retire, seed" -- backend/repositories/keyword_repo.go backend/mcpserver/ backend/models/models.go backend/internal/integration/mcp_test.go
```

---

### Task 4: hunt_term_yield and users_overview

Two read tools, one task: both are single queries into `tools_history.go` with no shared dependency on later work.

**Files:**
- Modify: `backend/repositories/hunt_repo.go` (append `HuntTermYield`)
- Modify: `backend/repositories/user_repo.go` (append `UsersOverview`)
- Modify: `backend/mcpserver/tools_history.go` (append both handlers)
- Modify: `backend/mcpserver/mcpserver.go` (register both)
- Modify: `backend/models/models.go` (append two row types)
- Test: `backend/internal/integration/mcp_test.go`

**Interfaces:**
- Consumes: `handlers`, `clampLimit`.
- Produces:
  - `models.HuntTermYieldRow`, `models.UserOverviewRow`
  - `repositories.HuntTermYield(ctx context.Context, g *gorm.DB, roundID *int, limit, offset int) ([]models.HuntTermYieldRow, error)`
  - `repositories.UsersOverview(ctx context.Context, g *gorm.DB, limit, offset int) ([]models.UserOverviewRow, error)`

- [ ] **Step 1: Write the failing tests**

Append inside `TestMCPServer`, before the `no bearer` subtest. `huntID`/`roundID` are created locally so this subtest owns its data.

```go
	t.Run("hunt_term_yield tallies triage per source term", func(t *testing.T) {
		hEmail := fmt.Sprintf("mcp-hunt-%d@test.local", time.Now().UnixNano())
		require.NoError(t, repositories.UpsertUser(ctx, db.DB(), hEmail, "x", "Hunt", "admin"))
		huntID, err := repositories.CreateHunt(ctx, db.DB(), hEmail, "mcp term yield")
		require.NoError(t, err)
		defer db.DB().Exec(`DELETE FROM frontend.hunts WHERE id = ?`, huntID)

		roundID, err := repositories.InsertHuntRound(ctx, db.DB(), huntID, nil,
			json.RawMessage(`["sleep tracker"]`), nil, nil, nil)
		require.NoError(t, err)

		require.NoError(t, db.DB().Exec(
			`INSERT INTO frontend.hunt_results
			   (round_id, page_id, page_name, store, store_id, source_term, was_new, triage, relevance)
			 VALUES (?, 'p1', 'One', 'ios', 's1', 'sleep tracker', true, 'kept',   'relevant'),
			        (?, 'p2', 'Two', 'ios', 's2', 'sleep tracker', true, 'vetoed', 'off_target')`,
			roundID, roundID).Error)

		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "hunt_term_yield",
			Arguments: map[string]any{"round_id": roundID},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Terms []struct {
				SourceTerm string `json:"source_term"`
				Found      int    `json:"found"`
				Kept       int    `json:"kept"`
				Relevant   int    `json:"relevant"`
				OffTarget  int    `json:"off_target"`
			} `json:"terms"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Len(t, out.Terms, 1)
		require.Equal(t, "sleep tracker", out.Terms[0].SourceTerm)
		require.Equal(t, 2, out.Terms[0].Found)
		require.Equal(t, 1, out.Terms[0].Kept)
		require.Equal(t, 1, out.Terms[0].Relevant)
		require.Equal(t, 1, out.Terms[0].OffTarget)
	})

	t.Run("users_overview includes the seeded member with its rating count", func(t *testing.T) {
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "users_overview",
			Arguments: map[string]any{"limit": 200},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Users []struct {
				Email   string `json:"email"`
				Role    string `json:"role"`
				Ratings int    `json:"ratings"`
			} `json:"users"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		found := false
		for _, u := range out.Users {
			if u.Email == memberEmail {
				found = true
				require.Equal(t, "member", u.Role)
				require.GreaterOrEqual(t, u.Ratings, 1, "the seeded like is counted")
			}
		}
		require.True(t, found, "the seeded member appears in the overview")
	})

	t.Run("member cannot see the admin history tools", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		names := toolNames(sess)
		require.False(t, names["hunt_term_yield"])
		require.False(t, names["users_overview"])
	})
```

- [ ] **Step 2: Run to verify failure**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer/(hunt_term_yield|users_overview)' -v
```

Expected: FAIL — tools not registered.

- [ ] **Step 3: Add the models**

Append to `backend/models/models.go`:

```go
// HuntTermYieldRow is one hunt search term's outcome tally across rounds.
// Competitor page pulls appear with SourceTerm "page:<name>". Relevance is
// nullable in the table (unjudged results fail open), so Relevant + OffTarget
// need not sum to Found.
type HuntTermYieldRow struct {
	SourceTerm string `json:"source_term"`
	Rounds     int    `json:"rounds"`
	Found      int    `json:"found"`
	Kept       int    `json:"kept"`
	Relevant   int    `json:"relevant"`
	OffTarget  int    `json:"off_target"`
}

// UserOverviewRow is one account with its activity counts.
type UserOverviewRow struct {
	Email       string     `json:"email"`
	Name        *string    `json:"name"`
	Role        string     `json:"role"`
	Provider    string     `json:"auth_provider"`
	Ratings     int        `json:"ratings"`
	SavedGems   int        `json:"saved_gems"`
	CreatedAt   time.Time  `json:"created_at"`
	OnboardedAt *time.Time `json:"onboarded_at"`
}
```

- [ ] **Step 4: Write both repository functions**

Append to `backend/repositories/hunt_repo.go`:

```go
// HuntTermYield tallies hunt outcomes per search term. With roundID nil it
// aggregates every round, which is the "which terms have ever worked" read;
// with a round id it scopes to that run. Ordered by keepers so the useful
// terms sort to the top.
func HuntTermYield(ctx context.Context, g *gorm.DB, roundID *int, limit, offset int) ([]models.HuntTermYieldRow, error) {
	var rows []models.HuntTermYieldRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT source_term,
			        count(DISTINCT round_id)                          AS rounds,
			        count(*)                                          AS found,
			        count(*) FILTER (WHERE triage = 'kept')           AS kept,
			        count(*) FILTER (WHERE relevance = 'relevant')    AS relevant,
			        count(*) FILTER (WHERE relevance = 'off_target')  AS off_target
			   FROM frontend.hunt_results
			  WHERE (? IS NULL OR round_id = ?)
			  GROUP BY source_term
			  ORDER BY kept DESC, found DESC, source_term
			  LIMIT ? OFFSET ?`,
			roundID, roundID, limit, offset,
		).Scan(&rows).Error
	})
	return rows, err
}
```

Append to `backend/repositories/user_repo.go`:

```go
// UsersOverview lists accounts with their activity counts, newest first.
// Counts are correlated aggregates so an account with no ratings still
// appears with a zero rather than dropping out of the result.
func UsersOverview(ctx context.Context, g *gorm.DB, limit, offset int) ([]models.UserOverviewRow, error) {
	var rows []models.UserOverviewRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT u.email, u.name, u.role, u.auth_provider, u.created_at, u.onboarded_at,
			        (SELECT count(*) FROM frontend.app_ratings r WHERE r.user_email = u.email) AS ratings,
			        (SELECT count(*) FROM frontend.saved_gems s WHERE s.user_email = u.email)  AS saved_gems
			   FROM frontend.users u
			  ORDER BY u.created_at DESC
			  LIMIT ? OFFSET ?`,
			limit, offset,
		).Scan(&rows).Error
	})
	return rows, err
}
```

Before writing this, open `backend/repositories/user_repo.go` and confirm the file's existing import block already has `db`, `models`, `gorm`, and `context`; add whichever are missing.

- [ ] **Step 5: Write both handlers**

Append to `backend/mcpserver/tools_history.go`:

```go
// ---- hunt_term_yield (admin only) ----

type huntTermYieldIn struct {
	RoundID int `json:"round_id,omitempty" jsonschema:"scope to one hunt round; omit for every round"`
	Limit   int `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset  int `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) huntTermYield(ctx context.Context, _ *mcp.CallToolRequest, in huntTermYieldIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	var round *int
	if in.RoundID > 0 {
		round = &in.RoundID
	}
	rows, err := repositories.HuntTermYield(ctx, db.DB(), round, clampLimit(in.Limit), in.Offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading hunt term yield.")
	}
	if len(rows) == 0 {
		return nil, map[string]any{"terms": rows, "note": "No hunt results yet."}, nil
	}
	return nil, map[string]any{"terms": rows}, nil
}

// ---- users_overview (admin only) ----

type usersOverviewIn struct {
	Limit  int `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset int `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) usersOverview(ctx context.Context, _ *mcp.CallToolRequest, in usersOverviewIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	rows, err := repositories.UsersOverview(ctx, db.DB(), clampLimit(in.Limit), in.Offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading the user list.")
	}
	return nil, map[string]any{"users": rows}, nil
}
```

- [ ] **Step 6: Register both**

In `buildServer`'s admin block, after `keyword_control`:

```go
		mcp.AddTool(s, &mcp.Tool{Name: "hunt_term_yield",
			Description: "Hunt search terms tallied by outcome: found, kept, relevant, off target. Competitor pulls appear as page:<name>."},
			h.huntTermYield)
		mcp.AddTool(s, &mcp.Tool{Name: "users_overview",
			Description: "Accounts with rating counts, saved-gem counts, provider, and onboarding date."},
			h.usersOverview)
```

- [ ] **Step 7: Run the tests**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer' -v
```

Expected: PASS, all subtests.

- [ ] **Step 8: Commit**

```bash
git add backend/repositories/ backend/mcpserver/ backend/models/models.go backend/internal/integration/mcp_test.go
git commit -m "feat(mcp): hunt_term_yield and users_overview tools" -- backend/repositories/ backend/mcpserver/ backend/models/models.go backend/internal/integration/mcp_test.go
```

---

### Task 5: ad_creatives — creative metadata and download URLs

**Files:**
- Create: `backend/repositories/creative_repo.go`
- Modify: `backend/mcpserver/tools_history.go` (append)
- Modify: `backend/mcpserver/mcpserver.go` (register as a member tool)
- Modify: `backend/models/models.go` (append `AdCreativeRow`)
- Test: `backend/internal/integration/mcp_test.go`

**Interfaces:**
- Consumes: `handlers`, `clampLimit`.
- Produces:
  - `models.AdCreativeRow`
  - `repositories.AdCreatives(ctx context.Context, g *gorm.DB, advertiserID int, limit, offset int) ([]models.AdCreativeRow, error)`

- [ ] **Step 1: Write the failing test**

`advID` is the package-level seeded advertiser from `integration_test.go`. Append inside `TestMCPServer`:

```go
	t.Run("ad_creatives returns media urls for an advertiser", func(t *testing.T) {
		ext := fmt.Sprintf("MCPCREATIVE-%d", time.Now().UnixNano())
		var adID int
		require.NoError(t, db.DB().Raw(
			`INSERT INTO public.ads (external_ad_id, advertiser_id, is_active)
			 VALUES (?, ?, true) RETURNING id`, ext, advID).Row().Scan(&adID))
		require.NoError(t, db.DB().Exec(
			`INSERT INTO public.creatives (ad_id, type, s3_key, content_type, file_size, title)
			 VALUES (?, 'image', 'creatives/mcp-test.jpg', 'image/jpeg', 1234, 'Test creative')`,
			adID).Error)
		defer db.DB().Exec(`DELETE FROM public.creatives WHERE ad_id = ?`, adID)
		defer db.DB().Exec(`DELETE FROM public.ads WHERE id = ?`, adID)

		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "ad_creatives",
			Arguments: map[string]any{"advertiser_id": advID},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			Creatives []struct {
				ExternalAdID string `json:"external_ad_id"`
				Type         string `json:"type"`
				ContentType  string `json:"content_type"`
				URL          string `json:"url"`
			} `json:"creatives"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		var seen bool
		for _, c := range out.Creatives {
			if c.ExternalAdID == ext {
				seen = true
				require.Equal(t, "image", c.Type)
				require.Equal(t, "image/jpeg", c.ContentType)
				require.Contains(t, c.URL, "/api/media/creatives/mcp-test.jpg",
					"url is the media route plus the s3 key")
			}
		}
		require.True(t, seen, "the seeded creative is returned")
	})
```

- [ ] **Step 2: Run to verify failure**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer/ad_creatives' -v
```

Expected: FAIL — tool not registered.

- [ ] **Step 3: Add the model**

Append to `backend/models/models.go`:

```go
// AdCreativeRow is one creative with the URL that serves its bytes. MCP results
// are JSON, so the tool hands back a URL and the client downloads the file
// itself rather than the bytes riding in the tool result.
type AdCreativeRow struct {
	AdID         int     `json:"ad_id"`
	ExternalAdID string  `json:"external_ad_id"`
	Type         *string `json:"type"`
	ContentType  *string `json:"content_type"`
	FileSize     *int64  `json:"file_size"`
	Title        *string `json:"title"`
	BodyText     *string `json:"body_text"`
	S3Key        *string `json:"-"`
	URL          string  `json:"url"`
	LinkURL      *string `json:"link_url"`
	IsActive     *bool   `json:"is_active"`
}
```

- [ ] **Step 4: Write the repository**

Create `backend/repositories/creative_repo.go`:

```go
// Package repositories — creative reads for the MCP download path.
//
// public.creatives(ad_id) is indexed (migration 144); filtering by advertiser
// goes through public.ads so the join stays on that index rather than scanning
// creatives.
package repositories

import (
	"context"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// AdCreatives returns one advertiser's creatives, newest ad first. The URL
// field is left empty here — the caller fills it in, because the public base
// URL is a transport concern the repository has no business knowing.
func AdCreatives(ctx context.Context, g *gorm.DB, advertiserID int, limit, offset int) ([]models.AdCreativeRow, error) {
	var rows []models.AdCreativeRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT a.id            AS ad_id,
			        a.external_ad_id,
			        a.link_url,
			        a.is_active,
			        c.type,
			        c.content_type,
			        c.file_size,
			        c.title,
			        c.body_text,
			        c.s3_key
			   FROM public.ads a
			   JOIN public.creatives c ON c.ad_id = a.id
			  WHERE a.advertiser_id = ?
			    AND c.s3_key IS NOT NULL
			  ORDER BY a.last_seen DESC, c.id
			  LIMIT ? OFFSET ?`,
			advertiserID, limit, offset,
		).Scan(&rows).Error
	})
	return rows, err
}
```

- [ ] **Step 5: Write the handler**

Append to `backend/mcpserver/tools_history.go`. Add `"os"` and `"strings"` to that file's imports.

```go
// ---- ad_creatives ----

// mediaBase is the origin the download URLs are built against. AGF_PUBLIC_API_URL
// is the deployed API's public origin; the empty fallback yields a root-relative
// URL, which is still correct for a client that knows the host.
func mediaBase() string {
	return strings.TrimSuffix(os.Getenv("AGF_PUBLIC_API_URL"), "/")
}

type adCreativesIn struct {
	AdvertiserID int `json:"advertiser_id" jsonschema:"the advertiser whose creatives to list"`
	Limit        int `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset       int `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) adCreatives(ctx context.Context, _ *mcp.CallToolRequest, in adCreativesIn) (*mcp.CallToolResult, any, error) {
	if in.AdvertiserID <= 0 {
		return nil, nil, errors.New("advertiser_id is required.")
	}
	rows, err := repositories.AdCreatives(ctx, db.DB(), in.AdvertiserID, clampLimit(in.Limit), in.Offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading those creatives.")
	}
	base := mediaBase()
	for i := range rows {
		if rows[i].S3Key != nil {
			rows[i].URL = base + "/api/media/" + *rows[i].S3Key
		}
	}
	if len(rows) == 0 {
		return nil, map[string]any{"creatives": rows, "note": "No stored creatives for that advertiser."}, nil
	}
	return nil, map[string]any{"creatives": rows}, nil
}
```

- [ ] **Step 6: Register as a member tool**

In `buildServer`, with the member tools (after `gem_detail`, outside the admin block):

```go
	mcp.AddTool(s, &mcp.Tool{Name: "ad_creatives",
		Description: "An advertiser's ad creatives with download URLs, ad copy, and link targets."},
		h.adCreatives)
```

- [ ] **Step 7: Run the tests**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer' -v
```

Expected: PASS, all subtests.

- [ ] **Step 8: Commit**

```bash
git add backend/repositories/creative_repo.go backend/mcpserver/ backend/models/models.go backend/internal/integration/mcp_test.go
git commit -m "feat(mcp): ad_creatives tool with media download urls" -- backend/repositories/creative_repo.go backend/mcpserver/ backend/models/models.go backend/internal/integration/mcp_test.go
```

---

### Task 6: run_hunt and hunt_status — on-demand scrape

**Files:**
- Modify: `backend/mcpserver/tools_engine.go` (append both handlers)
- Modify: `backend/mcpserver/mcpserver.go` (register both)
- Test: `backend/internal/integration/mcp_test.go`

No new repository code: `repositories.CreateHunt`, `repositories.InsertHuntRound` (which writes the round **and** the `pipeline_jobs` `kind='hunt'` row in one transaction), and `repositories.GetHunt`/`ListHuntRounds` already exist.

**Interfaces:**
- Consumes: `handlers`; `repositories.CreateHunt(ctx, g, email, title string) (int, error)`; `repositories.InsertHuntRound(ctx, g, huntID int, messageID *int, terms, competitors, settings json.RawMessage, intent *string) (int, error)`.
- Produces: nothing later tasks depend on.

**Round reads compose from three existing functions — write no new SQL:**

```go
repositories.HuntForRound(ctx, g, roundID) (huntID int, ownerEmail string, err error)  // (0, "", nil) when absent
repositories.RoundReport(ctx, g, huntID, roundID) (*models.HuntRoundReport, error)     // (nil, nil) when absent
repositories.RoundResults(ctx, g, huntID, roundID, triage string, limit int) ([]models.HuntResultRow, error)
```

`RoundReport` returns `status`, `error_text`, `started_at`, `finished_at`, `settings`, a progress digest, and `kept_count` / `vetoed_count` / `pending_count` / `off_target_count`. Its progress digest is `(progress - 'events' - 'lanes')` — the verbose event log and live lane map are stripped. That is the right trade for a poll tool; do not add them back.

- [ ] **Step 1: Write the failing test**

Append inside `TestMCPServer`:

```go
	t.Run("run_hunt enqueues a competitor pull with custom settings", func(t *testing.T) {
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name: "run_hunt",
			Arguments: map[string]any{
				"competitors": []string{"Quizlooker"},
				"intent":      "pull one advertiser's ads",
				"settings":    map[string]any{"listing_pages": 5},
			},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))

		var out struct {
			HuntID  int `json:"hunt_id"`
			RoundID int `json:"round_id"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &out))
		require.Greater(t, out.RoundID, 0)
		defer db.DB().Exec(`DELETE FROM frontend.hunts WHERE id = ?`, out.HuntID)

		// The round carries the competitor and the settings verbatim.
		var competitors, settings string
		require.NoError(t, db.DB().Raw(
			`SELECT competitor_apps::text, settings::text FROM frontend.hunt_rounds WHERE id = ?`,
			out.RoundID).Row().Scan(&competitors, &settings))
		require.Contains(t, competitors, "Quizlooker")
		require.Contains(t, settings, "listing_pages")

		// Exactly one queued hunt job for this round.
		var jobs int64
		require.NoError(t, db.DB().Raw(
			`SELECT count(*) FROM public.pipeline_jobs WHERE kind = 'hunt' AND advertiser_id = ?`,
			out.RoundID).Row().Scan(&jobs))
		require.EqualValues(t, 1, jobs)

		// hunt_status reads it back as queued.
		res, err = sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "hunt_status",
			Arguments: map[string]any{"round_id": out.RoundID},
		})
		require.NoError(t, err)
		require.False(t, res.IsError, "content: %s", firstText(res))
		var st struct {
			Status string `json:"status"`
		}
		require.NoError(t, json.Unmarshal([]byte(firstText(res)), &st))
		require.Equal(t, "queued", st.Status)
	})

	t.Run("run_hunt rejects a call with no terms and no competitors", func(t *testing.T) {
		sess, err := connect(adminKey)
		require.NoError(t, err)
		defer sess.Close()
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "run_hunt",
			Arguments: map[string]any{"intent": "nothing to search"},
		})
		if err == nil {
			require.True(t, res.IsError, "an empty hunt must be rejected")
		}
	})

	t.Run("member cannot see or call run_hunt", func(t *testing.T) {
		sess, err := connect(memberKey)
		require.NoError(t, err)
		defer sess.Close()
		require.False(t, toolNames(sess)["run_hunt"])
		res, err := sess.CallTool(ctx, &mcp.CallToolParams{
			Name:      "run_hunt",
			Arguments: map[string]any{"competitors": []string{"x"}},
		})
		if err == nil {
			require.True(t, res.IsError, "member run_hunt must not succeed")
		}
	})
```

- [ ] **Step 3: Run to verify failure**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer/run_hunt' -v
```

Expected: FAIL — tools not registered.

- [ ] **Step 4: Write the handlers**

Append to `backend/mcpserver/tools_engine.go`. Add `"encoding/json"` and `"fmt"` to that file's imports.

```go
// ---- run_hunt (admin only) ----

// huntSettingsIn mirrors the per-round knobs the Python executor reads. Bounds
// are enforced by clamp_hunt_settings on the worker side (page_cap 1-100,
// patience 1-10, listing_pages 1-30, min_pages 1-30); Go passes values through
// so there is exactly one place that decides what is legal.
type huntSettingsIn struct {
	PageCap      int `json:"page_cap,omitempty" jsonschema:"max pages per term, 1-100 (default 40)"`
	Patience     int `json:"patience,omitempty" jsonschema:"consecutive pages with nothing new before giving up on a term, 1-10 (default 6)"`
	ListingPages int `json:"listing_pages,omitempty" jsonschema:"pages pulled per competitor page listing, 1-30 (default 10)"`
	MinPages     int `json:"min_pages,omitempty" jsonschema:"pages to read before the patience exit may fire, 1-30 (default 10)"`
}

type runHuntIn struct {
	Terms       []string       `json:"terms,omitempty" jsonschema:"keyword searches, US only, paged until dry"`
	Competitors []string       `json:"competitors,omitempty" jsonschema:"app or brand names; each is resolved to its Ad Library page and its whole ad set is pulled, globally"`
	Settings    huntSettingsIn `json:"settings,omitempty" jsonschema:"per-round depth knobs"`
	Intent      string         `json:"intent,omitempty" jsonschema:"what this round is looking for; used to judge result relevance"`
	HuntID      int            `json:"hunt_id,omitempty" jsonschema:"add the round to an existing hunt thread; omit to start a new one"`
}

func (h *handlers) runHunt(ctx context.Context, _ *mcp.CallToolRequest, in runHuntIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	if len(in.Terms) == 0 && len(in.Competitors) == 0 {
		return nil, nil, errors.New("provide at least one term or one competitor.")
	}

	terms, err := json.Marshal(in.Terms)
	if err != nil {
		return nil, nil, errors.New("terms could not be encoded.")
	}
	competitors, err := json.Marshal(in.Competitors)
	if err != nil {
		return nil, nil, errors.New("competitors could not be encoded.")
	}
	settings, err := json.Marshal(in.Settings)
	if err != nil {
		return nil, nil, errors.New("settings could not be encoded.")
	}

	huntID := in.HuntID
	if huntID == 0 {
		title := in.Intent
		if title == "" {
			title = fmt.Sprintf("MCP hunt: %d terms, %d competitors", len(in.Terms), len(in.Competitors))
		}
		if len(title) > 120 {
			title = title[:120]
		}
		huntID, err = repositories.CreateHunt(ctx, db.DB(), h.u.Email, title)
		if err != nil {
			return nil, nil, errors.New("something went wrong starting the hunt.")
		}
	}

	var intent *string
	if in.Intent != "" {
		intent = &in.Intent
	}
	roundID, err := repositories.InsertHuntRound(ctx, db.DB(), huntID, nil, terms, competitors, settings, intent)
	if err != nil {
		return nil, nil, errors.New("something went wrong queueing that round.")
	}
	return nil, map[string]any{
		"hunt_id":  huntID,
		"round_id": roundID,
		"status":   "queued",
		"note":     "The worker picks this up on its next poll. Check progress with hunt_status.",
	}, nil
}

// ---- hunt_status (admin only) ----

type huntStatusIn struct {
	RoundID int `json:"round_id" jsonschema:"the round to poll"`
}

func (h *handlers) huntStatus(ctx context.Context, _ *mcp.CallToolRequest, in huntStatusIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	if in.RoundID <= 0 {
		return nil, nil, errors.New("round_id is required.")
	}
	// RoundReport and RoundResults are both hunt-scoped, so resolve the round's
	// hunt first. A round created by run_hunt belongs to the caller, but this is
	// read-only over the shared engine and the tool is already admin-gated, so
	// ownership is not re-checked here.
	huntID, _, err := repositories.HuntForRound(ctx, db.DB(), in.RoundID)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading that round.")
	}
	if huntID == 0 {
		return nil, nil, errors.New("round not found.")
	}
	report, err := repositories.RoundReport(ctx, db.DB(), huntID, in.RoundID)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading that round.")
	}
	if report == nil {
		return nil, nil, errors.New("round not found.")
	}
	results, err := repositories.RoundResults(ctx, db.DB(), huntID, in.RoundID, "", clampLimit(0))
	if err != nil {
		return nil, nil, errors.New("something went wrong reading that round's results.")
	}
	return nil, map[string]any{
		"hunt_id": huntID,
		"status":  report.Status,
		"round":   report,
		"results": results,
	}, nil
}
```

Confirm `models.HuntRoundReport`'s status field is exported as `Status` with a `json:"status"` tag before relying on `report.Status`; if the model names it differently, use that name.

- [ ] **Step 5: Register both**

In `buildServer`'s admin block, after `users_overview`:

```go
		mcp.AddTool(s, &mcp.Tool{Name: "run_hunt",
			Description: "Queue an on-demand Ad Library run. Terms are US keyword searches; competitors are resolved to their ad page and pulled globally. Optional per-round depth settings."},
			h.runHunt)
		mcp.AddTool(s, &mcp.Tool{Name: "hunt_status",
			Description: "One hunt round: status, live progress, and results with triage and relevance."},
			h.huntStatus)
```

- [ ] **Step 6: Run the full MCP test**

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' \
  go test ./backend/internal/integration/ -run 'TestMCPServer' -v
```

Expected: PASS, all subtests.

- [ ] **Step 7: Run the whole Go suite bare**

Run this as its own command. Do not pipe it through `tail`, `head`, or `grep` — the pipe eats the exit code.

```bash
AGF_IT_DB_URL='postgres://agf:agf@localhost:55433/agf_it?sslmode=disable' go test ./backend/...
```

Expected: every package `ok` or `no test files`. Zero `FAIL`.

- [ ] **Step 8: Commit**

```bash
git add backend/mcpserver/ backend/repositories/hunt_repo.go backend/internal/integration/mcp_test.go
git commit -m "feat(mcp): run_hunt and hunt_status tools for on-demand scrapes" -- backend/mcpserver/ backend/repositories/hunt_repo.go backend/internal/integration/mcp_test.go
```

---

### Task 7: Live end-to-end verification

Green integration tests are not sufficient: this feature spans the Go API, Postgres, and the VM hunt daemon. It is done when a real round runs from Claude Code against production.

**Files:**
- Modify: `docs/superpowers/specs/2026-08-06-mcp-engine-control-design.md` (record the media-auth finding)
- Possibly modify: `backend/routers/routers.go` (only if the media route rejects `fd_` keys)

- [ ] **Step 1: Resolve the open media-auth question**

The spec flags this as unverified. Against the deployed API, with a personal `fd_` key:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $FD_KEY" \
  "https://api.peon.works/api/media/<a-real-s3-key>"
```

`200` means `ad_creatives` URLs work as designed — record that in the spec and move on. `401`/`403` means the media route admits only `AGF_API_TOKEN`; in that case read `routers.Init` and `middlewares.KeyAuth` to confirm the cause before changing anything, then make the media route accept `fd_` keys the same way the other `/api` GETs do, and add an integration test that a keyed member can fetch a media key.

- [ ] **Step 2: Deploy**

Follow `DEPLOYMENT.md` for the API. The MCP handler ships inside the API binary, so no separate service needs a restart.

- [ ] **Step 3: Reconnect the MCP client and confirm the tool list**

From Claude Code, connect to `api.peon.works/mcp` with the admin `fd_` key and list tools. Expected: the six original member tools plus `ad_creatives`, `keyword_yield`, `keyword_control`, `hunt_term_yield`, `users_overview`, `run_hunt`, `hunt_status`, `scraping_health`.

- [ ] **Step 4: Drive the loop end to end**

1. `keyword_yield` with `min_sweeps: 3` — confirm real keywords come back with non-zero keepers.
2. `run_hunt` with `competitors: ["Quizlooker"]`, `settings: {listing_pages: 5}`, an intent string.
3. `hunt_status` on the returned `round_id`, polled until `done` — confirm the progress blob shows the competitor resolution events and that results land.
4. `ad_creatives` on a kept advertiser from that round.
5. `curl` one returned URL to disk and confirm the file is a real image or video (`file <path>`).

Record the round id, the number of results, and the downloaded file in the completion report. If the competitor does not resolve, that is a legitimate outcome (`no_match`) — note it and retry with a different name rather than treating it as a bug.

- [ ] **Step 5: Update the spec's open item and commit**

Replace the "Open item to verify" paragraph in the spec with the finding from Step 1.

```bash
git add docs/superpowers/specs/2026-08-06-mcp-engine-control-design.md
git commit -m "docs(spec): record media auth finding from live MCP verification" -- docs/superpowers/specs/2026-08-06-mcp-engine-control-design.md
```

---

## Self-review notes

- **Spec coverage:** `keyword_yield` → Task 2. `hunt_term_yield`, `users_overview` → Task 4. `keyword_control` → Task 3. `run_hunt`, `hunt_status` → Task 6. `ad_creatives` → Task 5. File split → Task 1. Error handling contract → Global Constraints. Testing approach → every task's Steps 1–2 and 7. Live verification → Task 7. Out-of-scope items (global env settings table, member writes, binary payloads) have no task, correctly.
- **Repository reuse verified:** Task 6 writes no new SQL. `HuntForRound`, `RoundReport`, and `RoundResults` were read in `backend/repositories/hunt_repo.go` and their signatures are transcribed verbatim into the task. `InsertHuntRound` already enqueues the `pipeline_jobs` `kind='hunt'` row inside its write transaction, so `run_hunt` does not enqueue anything itself.
- **Remaining unverified item:** whether `GET /api/media/*key` accepts a personal `fd_` key. Task 7 Step 1 resolves it with a live probe before the download path is claimed working, and branches on the answer rather than assuming one.
