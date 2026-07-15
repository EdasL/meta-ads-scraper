# Hunt Chat UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a hunt round finishes, the real Peon agent writes the wrap-up message (named apps + next-round proposal); results render as a card grid with a save+like heart; the empty assistant bubble is fixed; hunts reach deep SERP pages (min-page floor, patience 6, per-result page telemetry).

**Architecture:** Python hunt daemon finishes a round, then calls a new token-authed internal Go endpoint; the Go Peon agent (existing `GenerateHuntTurn` loop + tools) writes the completion message, falling back to the current canned sentence on any failure. The results read gains LEFT JOINs to advertiser data + per-user saved/rating state; the web hunt view swaps the row list for a card grid wired to the existing saved-gems + ratings endpoints.

**Tech Stack:** Go (gin, gorm raw SQL), Python (SQLAlchemy text(), httpx), Next.js 16 (App Router, client components), Postgres migrations under `db/schema/`.

**Spec:** `docs/superpowers/specs/2026-07-15-hunt-chat-ux-redesign-design.md`

## Global Constraints

- Product copy: plain, clear, professional; no marketing fluff; product nouns intact (Field Desk, GemScore, cohort). UI shows only real data — no derived labels restating adjacent numbers.
- Migrations: NO bare `%` anywhere in a migration file, including comments (write "pct"/"percent"/"mod(a,b)").
- Python DB tests need `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`; a skip is not a pass. ONE test consumer per shared test DB at a time; Go and Python suites run serially, never concurrently.
- Verification commands run BARE as their own Bash call (no pipes to tail/grep in the same call as a success decision).
- Commit per task with explicit paths (`git add <paths>`, never `-A`). Commits go to `main` directly.
- Web: this repo is Next.js 16 — read `web/node_modules/next/dist/docs/` guides before writing unfamiliar web code (`web/AGENTS.md`).
- Settings bounds after this plan: page_cap 1-100 (default 40), patience 1-10 (default **6**), listing_pages 1-30 (default 10), min_pages 1-30 (default **10**).

---

### Task 1: Page telemetry + deep-paging floor (migration 158 + Python sweep)

**Files:**
- Create: `db/schema/158_hunt_results_page.sql`
- Modify: `src/agf/db/hunts.py:13-14` (defaults/bounds), `src/agf/db/hunts.py:48-56` (upsert)
- Modify: `src/agf/discovery/hunt.py:33-39` (constants), `:93-129` (`_persist_page`), `:132-170` (`_sweep_term`), `:226-243` (lane passes knob)
- Test: `tests/discovery/test_hunt.py`, `tests/db/test_hunts.py`

**Interfaces:**
- Consumes: existing `clamp_hunt_settings`, `upsert_hunt_result`, `_sweep_term` (signatures above).
- Produces: `frontend.hunt_results.page INT` (nullable); `clamp_hunt_settings` returns a dict that now includes `min_pages` (default 10) and `patience` default 6; `upsert_hunt_result(conn, row)` REQUIRES a `"page"` key in `row`; `_sweep_term(..., patience, page_cap, min_pages)` — dry early-exit cannot fire before `depth >= min_pages`.

- [ ] **Step 1: Write the migration**

```sql
-- 158_hunt_results_page.sql
-- Record the SERP page each hunt result was first found on, so depth-yield is
-- measurable per hunt (design 2026-07-15: hunts never read past page 5 because
-- repeat-incumbent pages tripped the dry exit; from now on we measure depth).
-- Nullable: rows written before this migration carry no depth. No backfill.
ALTER TABLE frontend.hunt_results ADD COLUMN IF NOT EXISTS page INT;
```

(Contains no bare percent character anywhere, comments included.)

- [ ] **Step 2: Write failing tests**

In `tests/db/test_hunts.py` add (mirror the existing upsert test's fixtures/conventions in that file — it already creates a hunt + round; reuse its helpers):

```python
def test_upsert_hunt_result_records_page(db_conn, hunt_round):
    upsert_hunt_result(db_conn, {
        "round_id": hunt_round, "candidate_id": None, "page_id": "p9",
        "page_name": "Deep Find", "store": "ios", "store_id": "id9",
        "link_url": None, "source_term": "running app", "was_new": True,
        "page": 7,
    })
    row = db_conn.execute(text(
        "SELECT page FROM frontend.hunt_results "
        "WHERE round_id = :r AND page_id = 'p9'"), {"r": hunt_round}).one()
    assert row.page == 7
```

In `tests/discovery/test_hunt.py` add (using the existing `FakeExtractor`/`make_ad` conftest helpers and the file's existing run/sweep test shape):

```python
def test_clamp_settings_min_pages_and_patience_default():
    knobs = clamp_hunt_settings(None)
    assert knobs["patience"] == 6
    assert knobs["min_pages"] == 10
    assert clamp_hunt_settings({"min_pages": 99})["min_pages"] == 30
    assert clamp_hunt_settings({"min_pages": 0})["min_pages"] == 1


def test_dry_exit_waits_for_min_pages(...):
    # Script a term whose page 1 yields one new ad and pages 2..N yield ads
    # already seen (new=0 every page), has_next always true, page_cap 20,
    # patience 2, min_pages 6.
    # OLD behavior stopped at page 3 (consec_dry=2). NEW: the dry exit may not
    # fire before depth >= 6 -> assert term telemetry pages == 6, stop == "dry".


def test_results_carry_page_depth(...):
    # Script a term with a new ad on page 1 and another new ad on page 3;
    # assert the two frontend.hunt_results rows have page = 1 and page = 3.
```

- [ ] **Step 3: Run the new tests, verify they FAIL**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt.py tests/db/test_hunts.py -q`
Expected: new tests FAIL (missing `min_pages`, patience still 3, no `page` column handling). Apply migration 158 to the test DB first if the harness doesn't auto-apply (check how existing tests get schema — `tests/db` conftest applies migrations).

- [ ] **Step 4: Implement**

`src/agf/db/hunts.py` — replace lines 13-14:

```python
DEFAULT_HUNT_SETTINGS = {
    "page_cap": 40, "patience": 6, "listing_pages": 10, "min_pages": 10}
_SETTINGS_BOUNDS = {
    "page_cap": (1, 100), "patience": (1, 10),
    "listing_pages": (1, 30), "min_pages": (1, 30)}
```

`src/agf/db/hunts.py` — `upsert_hunt_result` gains the page column:

```python
def upsert_hunt_result(conn, row: dict) -> None:
    """One found page per round; re-finds (other term/page) keep the first row."""
    conn.execute(text(
        "INSERT INTO frontend.hunt_results "
        "  (round_id, candidate_id, page_id, page_name, store, store_id, "
        "   link_url, source_term, was_new, page) "
        "VALUES (:round_id, :candidate_id, :page_id, :page_name, :store, "
        "        :store_id, :link_url, :source_term, :was_new, :page) "
        "ON CONFLICT (round_id, page_id, store, store_id) DO NOTHING"), row)
```

`src/agf/discovery/hunt.py`:
- Constants: `HUNT_PATIENCE = 6` (comment: "consecutive net-new-dry pages before early exit — raised from 3, design 2026-07-15") and add `HUNT_MIN_PAGES = 10  # dry exit may not fire before this depth (incumbent-flooded early pages fed the dry counter)`.
- `_persist_page`: in the `upsert_hunt_result(conn, {...})` dict add `"page": depth,`.
- `_sweep_term` signature: `..., patience: int = HUNT_PATIENCE, page_cap: int = HUNT_PAGE_CAP, min_pages: int = HUNT_MIN_PAGES) -> tuple[int, int, int, str]:` and change the dry check to:

```python
        if consec_dry >= patience and depth >= min_pages:
            return depth, term_found, term_new, "dry"
```

(`page_cap` still wins when smaller than `min_pages` — the cap check is unchanged and runs after.)
- In `run_hunt_round`'s `lane()`: pass the knob — `knobs["patience"], knobs["page_cap"], knobs["min_pages"])`.
- Competitor listing pulls (`_persist_page(engine, round_id, f"page:{name}", records, 1, ...)`) already pass depth 1 — no change.

Update any existing tests that assumed patience 3 / no floor (scripted FakeExtractor page counts will shift; fix the expectations, don't weaken assertions).

- [ ] **Step 5: Run the full hunt test files, verify PASS**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt.py tests/db/test_hunts.py tests/discovery/test_hunt_daemon.py tests/discovery/test_hunt_messages.py -q`
Expected: all pass, 0 failed, no unexpected skips.

- [ ] **Step 6: Commit**

```bash
git add db/schema/158_hunt_results_page.sql src/agf/db/hunts.py src/agf/discovery/hunt.py tests/discovery/test_hunt.py tests/db/test_hunts.py
git commit -m "feat(hunt): record page depth per result + min_pages floor, patience 6"
```

---

### Task 2: `min_pages` knob through the Go agent + round API

**Files:**
- Modify: `backend/helpers/gemini.go:379-403` (HuntSettings/Clamp), `:485-488` (instruction settings sentence), `:659-670` (settingsSchema), `:702-709` (planFromArgs), `:715-727` (describePlan)
- Modify: `backend/controllers/hunt_controller.go:434-438` (huntSettingsBody), `:493-501` (StartRound clamp), `:689-698` (settingsLabel)
- Test: `backend/helpers/gemini_hunt_test.go`

**Interfaces:**
- Consumes: `ClampHuntSettings` map keys land in `hunt_rounds.settings` jsonb, read by Python `clamp_hunt_settings` (Task 1 already accepts `min_pages`).
- Produces: `helpers.HuntSettings{MinPages *int}`; clamped key `"min_pages"` (1-30); proposal jsonb may carry `settings.min_pages`.

- [ ] **Step 1: Write failing test**

In `backend/helpers/gemini_hunt_test.go`, extend the existing ClampHuntSettings test (match its table style):

```go
func TestClampHuntSettingsMinPages(t *testing.T) {
	v := 99
	got := ClampHuntSettings(HuntSettings{MinPages: &v})
	if got["min_pages"] != 30 {
		t.Fatalf("min_pages clamp high: got %d, want 30", got["min_pages"])
	}
	lo := 0
	got = ClampHuntSettings(HuntSettings{MinPages: &lo})
	if got["min_pages"] != 1 {
		t.Fatalf("min_pages clamp low: got %d, want 1", got["min_pages"])
	}
	if _, ok := ClampHuntSettings(HuntSettings{})["min_pages"]; ok {
		t.Fatal("absent MinPages must not emit a key")
	}
}
```

- [ ] **Step 2: Run it, verify FAIL (compile error: no MinPages field)**

Run: `cd backend && go test ./helpers/ -run TestClampHuntSettingsMinPages -v`

- [ ] **Step 3: Implement**

`backend/helpers/gemini.go`:

```go
type HuntSettings struct {
	PageCap      *int // Ad Library pages per term (1-100)
	Patience     *int // empty pages tolerated before stopping a term (1-10)
	ListingPages *int // store listing pages fetched per app (1-30)
	MinPages     *int // pages to read before the dry exit may fire (1-30)
}
```

In `ClampHuntSettings` add:

```go
	if s.MinPages != nil {
		out["min_pages"] = clampInt(*s.MinPages, 1, 30)
	}
```

In `settingsSchema()` Properties add:

```go
	"min_pages": {Type: genai.TypeInteger, Description: "Pages to read per term before the dry early-exit may fire (1-30). Early pages are often full of repeat incumbents; a higher floor reaches new apps."},
```

In `planFromArgs` settings block add `MinPages: intPtrArg(raw, "min_pages"),`.

In `describePlan` add (after the patience segment):

```go
	if m, ok := plan.Settings["min_pages"]; ok {
		segs = append(segs, fmt.Sprintf("min %d", m))
	}
```

In `huntAgentInstruction`, replace the settings sentence with:

```
"Optional round settings and their bounds: page_cap (Ad Library pages per term, 1-100), " +
"patience (empty pages tolerated before stopping a term, 1-10), min_pages (pages to read " +
"before the dry exit may fire, 1-30), listing_pages (store listing pages per app, 1-30). " +
"Set only the ones the founder asked for; when asked what a setting means, just explain it.\n"
```

`backend/controllers/hunt_controller.go`:

```go
type huntSettingsBody struct {
	PageCap      *int `json:"page_cap"`
	Patience     *int `json:"patience"`
	ListingPages *int `json:"listing_pages"`
	MinPages     *int `json:"min_pages"`
}
```

In `StartRound`'s clamp call add `MinPages: body.Settings.MinPages,`. In `settingsLabel` change the key list to `[]string{"page_cap", "patience", "min_pages", "listing_pages"}`.

- [ ] **Step 4: Run the backend suite bare, verify PASS**

Run: `cd backend && go test ./...`
Expected: ok everywhere, 0 failures. (Do not run concurrently with any Python suite.)

- [ ] **Step 5: Commit**

```bash
git add backend/helpers/gemini.go backend/helpers/gemini_hunt_test.go backend/controllers/hunt_controller.go
git commit -m "feat(hunt): min_pages knob through agent schema, clamp, and round API"
```

---

### Task 3: Round-completion agent turn + internal endpoint

**Files:**
- Modify: `backend/helpers/gemini.go:459-491` (instruction: broad terms + completion-turn guidance)
- Modify: `backend/controllers/hunt_controller.go` (extract `executeTurn` from `runTurn`; add `RoundCompleted`)
- Modify: `backend/repositories/hunt_repo.go` (add `HuntForRound`)
- Modify: `backend/routers/routers.go:88-94` (route on the internal group)
- Test: `backend/internal/integration/hunt_test.go` (follow that file's existing seeding/auth conventions)

**Interfaces:**
- Consumes: `GenerateHuntTurn`, `summarizeRounds`, `ListHuntMessages`, `HuntRoundSummaries`, `PriorHuntLines`, `InsertHuntMessageTx`, `middlewares.RequireInternalToken()`.
- Produces: `POST /api/internal/hunts/rounds/:round_id/completed` → 200 `{"message_id": N}` after inserting ONE assistant message with non-empty content; 404 unknown round; 502 on Gemini failure or empty reply; 503 when Gemini unconfigured. `repositories.HuntForRound(ctx, g, roundID) (huntID int, email string, err error)` — `(0, "", nil)` when missing.

- [ ] **Step 1: Write failing integration test**

In `backend/internal/integration/hunt_test.go` add a test that (following the file's existing setup style — DB seed helpers, router bootstrap, internal-token header):

```go
// TestRoundCompletedWritesAgentMessage seeds a hunt + a finished round, stubs
// the Gemini side the way the file's other agent tests do (if the suite runs
// with GEMINI_API_KEY unset, assert the 503 contract instead and cover the
// happy path in a controller-level test with a fake helper), and asserts:
//  - POST /api/internal/hunts/rounds/{id}/completed with the internal bearer
//    token returns 200 and {"message_id": N}
//  - frontend.hunt_messages gained exactly one 'assistant' row for the hunt
//    with non-empty content
//  - an unknown round id returns 404
//  - a missing/wrong bearer token returns 401 (RequireInternalToken fails closed)
```

NOTE for the implementer: check how `gemini_hunt_test.go` fakes the model loop — `GenerateHuntTurn` takes the concrete `*GeminiHelper`, so the honest unit seam is `HuntToolExecutor` + a nil-helper 503 test at the controller level, with the full happy path exercised in the live gate (Task 7). Do NOT invent an interface refactor of GeminiHelper for this; test the endpoint contract (auth, 404, 503, message-write on success with a configured helper if the integration env has a key, else skip that one assertion with a loud skip message).

- [ ] **Step 2: Run it, verify FAIL**

Run: `cd backend && go test ./internal/integration/ -run TestRoundCompleted -v`
Expected: FAIL (404 route not found).

- [ ] **Step 3: Implement**

`backend/repositories/hunt_repo.go` append:

```go
// HuntForRound resolves a round to its hunt id and owner email, for the
// internal round-completion hook. Returns (0, "", nil) when the round does not
// exist — the caller 404s without leaking existence semantics.
func HuntForRound(ctx context.Context, g *gorm.DB, roundID int) (int, string, error) {
	var rows []struct {
		HuntID int    `gorm:"column:hunt_id"`
		Email  string `gorm:"column:user_email"`
	}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT r.hunt_id, h.user_email
			 FROM frontend.hunt_rounds r
			 JOIN frontend.hunts h ON h.id = r.hunt_id
			 WHERE r.id = ?`,
			roundID,
		).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return 0, "", err
	}
	return rows[0].HuntID, rows[0].Email, nil
}
```

`backend/controllers/hunt_controller.go` — extract the turn core so chat turns and the completion hook share one implementation. Replace `runTurn`'s body with a call to the new `executeTurn` and add `RoundCompleted`:

```go
// executeTurn runs one Peon agent turn and persists the assistant message,
// returning the new message id and the reply text. Shared by the chat turns
// (runTurn) and the internal round-completion hook. parent should be the
// request context; the turn itself is bounded by huntProposalTimeout.
func (c *HuntController) executeTurn(parent context.Context, huntID int, input helpers.HuntTurnInput) (int, string, error) {
	gctx, cancel := context.WithTimeout(parent, huntProposalTimeout)
	defer cancel()

	tools := huntTools{ctx: gctx, g: db.DB(), huntID: huntID}
	turn, err := c.gemini.GenerateHuntTurn(gctx, input, tools)
	if err != nil {
		return 0, "", err
	}

	var proposalJSON json.RawMessage
	if turn.Proposal != nil {
		if proposalJSON, err = marshalHuntPlan(*turn.Proposal); err != nil {
			return 0, "", err
		}
	}
	var toolLogJSON json.RawMessage
	if len(turn.ToolLog) > 0 {
		if toolLogJSON, err = json.Marshal(turn.ToolLog); err != nil {
			return 0, "", err
		}
	}

	wctx, cancel2 := context.WithTimeout(parent, 15*time.Second)
	defer cancel2()
	var msgID int
	err = db.WithWriteTx(wctx, db.DB(), func(tx *gorm.DB) error {
		var e error
		msgID, e = repositories.InsertHuntMessageTx(tx, huntID, "assistant", turn.Reply, proposalJSON, toolLogJSON)
		return e
	})
	if err != nil {
		return 0, "", err
	}
	return msgID, turn.Reply, nil
}
```

Rewrite `runTurn` to keep its exact HTTP semantics (502 on Gemini failure, 500 on save failure — collapse both to the current user-facing messages; a distinct save-failure message is fine to keep by checking `errors.Is`? No — keep it simple and honest: return 502 "Peon could not answer. Try again." on `executeTurn` error; the previous 500-vs-502 split is not worth re-plumbing):

```go
func (c *HuntController) runTurn(ctx *gin.Context, huntID int, input helpers.HuntTurnInput) bool {
	if _, _, err := c.executeTurn(ctx.Request.Context(), huntID, input); err != nil {
		helpers.Detail(ctx, http.StatusBadGateway, "Peon could not answer. Try again.")
		return false
	}
	return true
}
```

(`reqCtx` import stays used elsewhere; drop now-unused locals. Keep the comment about rounds starting only from the Start hunt button.)

Add the completion handler:

```go
// roundCompletedInstruction is the synthetic turn the daemon triggers when a
// round finishes. It is NOT stored as a user message — only Peon's answer
// lands in the thread.
func roundCompletedInstruction(roundID int) string {
	return fmt.Sprintf(
		"Hunt round %d just finished scraping. Report the outcome to the founder: "+
			"call get_round_report and get_round_results for round id %d first. Name the "+
			"promising kept apps plainly, say which terms produced finds and which found "+
			"nothing, and note anything off target. Then, unless there is nothing worth "+
			"trying, call propose_round with a concrete next step — broader terms, deeper "+
			"paging (min_pages / page_cap), dropping dead terms. Your reply text must "+
			"never be empty.", roundID, roundID)
}

// RoundCompleted handles POST /api/internal/hunts/rounds/:round_id/completed —
// the hunt daemon's completion hook (RequireInternalToken; no user session).
// On success exactly one assistant message is written. Any non-200 tells the
// daemon to write its canned fallback message instead, so a round can never
// finish silently.
func (c *HuntController) RoundCompleted(ctx *gin.Context) {
	roundID, err := strconv.Atoi(ctx.Param("round_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusNotFound, "no such round.")
		return
	}
	if c.gemini == nil {
		helpers.Detail(ctx, http.StatusServiceUnavailable, "Peon is not configured.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	huntID, email, err := repositories.HuntForRound(rctx, db.DB(), roundID)
	if err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if huntID == 0 {
		cancel()
		helpers.Detail(ctx, http.StatusNotFound, "no such round.")
		return
	}
	prior, err := repositories.ListHuntMessages(rctx, db.DB(), huntID)
	if err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	summaries, err := repositories.HuntRoundSummaries(rctx, db.DB(), huntID)
	if err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	priorHunts, err := repositories.PriorHuntLines(rctx, db.DB(), email, huntID)
	if err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	cancel()

	history := make([]helpers.HuntHistoryTurn, 0, len(prior))
	for _, m := range prior {
		history = append(history, helpers.HuntHistoryTurn{Role: m.Role, Content: m.Content})
	}
	tried, roundLines := summarizeRounds(summaries)

	msgID, reply, err := c.executeTurn(ctx.Request.Context(), huntID, helpers.HuntTurnInput{
		Message:        roundCompletedInstruction(roundID),
		History:        history,
		TriedTerms:     tried,
		RoundSummaries: roundLines,
		PriorHunts:     priorHunts,
	})
	if err != nil || strings.TrimSpace(reply) == "" {
		// An empty wrap-up is useless — let the daemon write the canned counts
		// message instead. (The message row, if written empty, is harmless: the
		// web renders proposal cards without a bubble; but signal fallback.)
		helpers.Detail(ctx, http.StatusBadGateway, "Peon could not report this round.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"message_id": msgID})
}
```

CAVEAT for the implementer: if `executeTurn` succeeded but the reply was empty, a message row exists AND the daemon will add the canned fallback — two messages. Prevent that: check the reply BEFORE persisting. Restructure `executeTurn` with a `requireReply bool` parameter (completion passes true, chat passes false) that returns an error WITHOUT writing when the reply is empty and there is no proposal-with-content path to salvage:

```go
	if requireReply && strings.TrimSpace(turn.Reply) == "" {
		return 0, "", fmt.Errorf("empty completion reply")
	}
```

(placed immediately after the `GenerateHuntTurn` call, before any DB write). Adjust the two callers.

`backend/helpers/gemini.go` — append two paragraphs to `huntAgentInstruction`:

```
"\n" +
"Prefer a few broad head terms ('running app', 'sleep tracker') over many hyper-" +
"specific long-tail variants: measured across prior hunts, long-tail terms almost " +
"always find nothing on the Ad Library while broad terms carry the round.\n" +
"\n" +
"Some turns ask you to report a just-finished round. Read its telemetry first " +
"(get_round_report, get_round_results), name the promising kept apps, say which " +
"terms worked and which came up empty, then propose the next round. For those " +
"reports 2-5 sentences are fine; never reply with empty text."
```

(The existing "reply text is 1-3 plain, professional sentences" line stays — the report paragraph explicitly overrides length for completion turns.)

`backend/routers/routers.go` — after line 94 (`internal.POST("/keys/revoke", ia.RevokeKey)`):

```go
	// Hunt round-completion hook: the worker VM's hunt daemon reports a
	// finished round; Peon writes the wrap-up message. Internal token only.
	internal.POST("/hunts/rounds/:round_id/completed", hunt.RoundCompleted)
```

(`hunt` is declared at line 33, in scope.)

- [ ] **Step 4: Run backend suite bare, verify PASS**

Run: `cd backend && go test ./...`
Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add backend/controllers/hunt_controller.go backend/repositories/hunt_repo.go backend/routers/routers.go backend/helpers/gemini.go backend/internal/integration/hunt_test.go
git commit -m "feat(hunt): Peon round-completion turn via internal endpoint"
```

---

### Task 4: Daemon calls the hook (with canned fallback) + compose env

**Files:**
- Modify: `src/agf/discovery/hunt_one.py:61-98`
- Modify: `ops/prod/docker-compose.yml:179-193` (hunt service env)
- Test: `tests/discovery/test_hunt_daemon.py` (or the file where hunt_one's tests live — grep `hunt_one` under tests/ first)

**Interfaces:**
- Consumes: `POST {AGF_API_URL}/api/internal/hunts/rounds/{id}/completed`, bearer `AGF_API_TOKEN` (Task 3).
- Produces: on round success — Peon message via the hook, OR the existing canned message when the hook is unreachable/non-200/unconfigured. Error rounds keep the canned failure message unconditionally. Round + job status are stamped BEFORE the hook fires (the agent must read final state).

- [ ] **Step 1: Write failing tests**

```python
def test_notify_completed_posts_with_token(monkeypatch):
    # env AGF_API_URL=http://api.test, AGF_API_TOKEN=tok; fake client captures
    # (url, headers); returns 200 -> _notify_completed(...) is True; assert URL
    # ends with /api/internal/hunts/rounds/7/completed and the Authorization
    # header is "Bearer tok".

def test_notify_completed_false_on_error_or_missing_env(monkeypatch):
    # (a) no env -> False without any HTTP call; (b) client raises -> False;
    # (c) non-200 -> False.

def test_done_path_writes_canned_message_only_when_notify_fails(...):
    # Follow this test file's existing hunt_one/main-path test conventions:
    # monkeypatch run_hunt_round to a no-op and _notify_completed to
    # False -> canned "Round N done:" message row inserted;
    # then to True -> NO assistant message inserted by hunt_one.
```

- [ ] **Step 2: Run, verify FAIL**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt_daemon.py -q`

- [ ] **Step 3: Implement**

`src/agf/discovery/hunt_one.py` — add `import os` up top, and:

```python
def _notify_completed(round_id: int, http_client) -> bool:
    """POST the internal round-completed hook; True when the API wrote Peon's
    wrap-up message. Any failure returns False and the caller writes the
    canned fallback, so a round never finishes silently."""
    base = os.environ.get("AGF_API_URL", "").rstrip("/")
    token = os.environ.get("AGF_API_TOKEN", "")
    if not base or not token:
        return False
    try:
        resp = http_client.post(
            f"{base}/api/internal/hunts/rounds/{round_id}/completed",
            headers={"Authorization": f"Bearer {token}"},
            timeout=120)
        if resp.status_code != 200:
            log.warning("completion hook returned %d for round %d",
                        resp.status_code, round_id)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 — fallback message covers any failure
        log.exception("completion hook failed for round %d", round_id)
        return False
```

Replace the success block in `main()` (current lines 67-76):

```python
        with begin(engine) as conn:
            finish_round(conn, args.round_id, status="done")
            set_job_status(conn, args.job_id, "done")
        # The round is committed done; disarm the watchdog BEFORE the (up to
        # 120s) completion hook so a late alarm can't overwrite 'done' with
        # 'error' via _fail.
        signal.alarm(0)
        if not _notify_completed(args.round_id, http_client):
            with begin(engine) as conn:
                try:
                    counts = round_summary_counts(conn, args.round_id)
                    insert_round_message(
                        conn, args.round_id,
                        compose_round_message(args.round_id, "done", counts))
                except Exception:  # message is a convenience; the round row is the truth
                    log.exception("round completion message failed")
        log.info("hunt round %d done: %s", args.round_id, stats)
        return 0
```

Error paths (`_fail`, daemon safety net) stay exactly as they are — canned failure message.

`ops/prod/docker-compose.yml` hunt service — add to `environment:`:

```yaml
      # Round-completion hook: Peon writes the wrap-up message via the API
      # (design 2026-07-15). Unset/unreachable -> canned fallback message.
      AGF_API_URL: ${AGF_API_URL:-https://api.peon.works}
      AGF_API_TOKEN: ${AGF_API_TOKEN}
```

DEPLOY NOTE (for Task 7): the VM's compose file is hand-edited and drifts — edit BOTH the repo copy and the VM copy, and confirm `AGF_API_TOKEN` exists in the VM's `.env` (it is an API-side secret; copy the value from the GKE secret if absent).

- [ ] **Step 4: Run the Python hunt suites bare, verify PASS**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/ tests/db/test_hunts.py -q`
Expected: 0 failed.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/hunt_one.py ops/prod/docker-compose.yml tests/discovery/test_hunt_daemon.py
git commit -m "feat(hunt): daemon completion hook with canned-message fallback"
```

---

### Task 5: Results read gains app data + per-user saved/rating state

**Files:**
- Modify: `backend/repositories/hunt_repo.go:118-137` (ListHuntResults)
- Modify: `backend/models/hunt.go:57-74` (HuntResultRow)
- Modify: `backend/controllers/hunt_controller.go:373-432` (Get passes email)
- Test: `backend/internal/integration/hunt_test.go`

**Interfaces:**
- Consumes: `frontend.emerging_scaling_apps` (advertiser-level matview: `advertiser_id, app_name, icon_url, tagline, cohort` — VERIFY the exact column names against the newest matview definition in `db/schema/` before writing SQL; grep `CREATE MATERIALIZED VIEW frontend.emerging_scaling_apps` and use the highest-numbered file), `frontend.saved_gems (user_email, advertiser_id, ...)`, `frontend.app_ratings (user_email, advertiser_id, rating, ...)`, `hunt_results.page` (Task 1).
- Produces: `ListHuntResults(ctx, g, huntID int, email string)`; `HuntResultRow` gains `Page *int`, `AppName *string`, `IconURL *string`, `Tagline *string`, `Cohort *string`, `Saved bool`, `Rating *string` (JSON keys: `page, app_name, icon_url, tagline, cohort, saved, rating`).

- [ ] **Step 1: Write failing integration test**

In `backend/internal/integration/hunt_test.go` (reuse its seed helpers):

```go
// TestHuntResultsCarryAppAndUserState seeds a hunt + round + kept result with
// kept_advertiser_id=A and page=4, a frontend.saved_gems row (owner email, A)
// and a frontend.app_ratings row (owner email, A, 'like'). GET /api/hunts/:id
// as the owner. Assert the result row JSON carries page=4, saved=true,
// rating="like", and null-safe app fields (matview not populated in the test
// DB -> app_name/icon_url/tagline/cohort null, no scan error). A second user's
// saved row must NOT leak: re-seed saved_gems under another email and assert
// saved=false.
```

- [ ] **Step 2: Run, verify FAIL**

Run: `cd backend && go test ./internal/integration/ -run TestHuntResultsCarryAppAndUserState -v`

- [ ] **Step 3: Implement**

`backend/models/hunt.go` — extend `HuntResultRow`:

```go
	Page             *int     `json:"page" gorm:"column:page"`
	AppName          *string  `json:"app_name" gorm:"column:app_name"`
	IconURL          *string  `json:"icon_url" gorm:"column:icon_url"`
	Tagline          *string  `json:"tagline" gorm:"column:tagline"`
	Cohort           *string  `json:"cohort" gorm:"column:cohort"`
	Saved            bool     `json:"saved" gorm:"column:saved"`
	Rating           *string  `json:"rating" gorm:"column:rating"`
```

`backend/repositories/hunt_repo.go` — `ListHuntResults` gains `email string` and the joins:

```go
// ListHuntResults returns every result across a hunt's rounds, joined at read
// time with the kept advertiser's GemScore, its Discover-feed app data
// (icon/tagline/cohort; null until the matview carries it), and the CALLER's
// saved/rating state (email-scoped — another user's saves never leak).
func ListHuntResults(ctx context.Context, g *gorm.DB, huntID int, email string) ([]models.HuntResultRow, error) {
	var rows []models.HuntResultRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT hr.id, hr.round_id, hr.page_id, hr.page_name, hr.store, hr.store_id,
			        hr.link_url, hr.source_term, hr.was_new, hr.triage, hr.kept_advertiser_id,
			        hr.relevance, hr.relevance_note, hr.page,
			        s.gem_score,
			        esa.app_name, esa.icon_url, esa.tagline, esa.cohort,
			        (sg.advertiser_id IS NOT NULL) AS saved,
			        ar.rating
			 FROM frontend.hunt_results hr
			 JOIN frontend.hunt_rounds r ON r.id = hr.round_id
			 LEFT JOIN adtech.advertiser_scores s ON s.advertiser_id = hr.kept_advertiser_id
			 LEFT JOIN frontend.emerging_scaling_apps esa ON esa.advertiser_id = hr.kept_advertiser_id
			 LEFT JOIN frontend.saved_gems sg
			        ON sg.advertiser_id = hr.kept_advertiser_id AND lower(sg.user_email) = lower(?)
			 LEFT JOIN frontend.app_ratings ar
			        ON ar.advertiser_id = hr.kept_advertiser_id AND lower(ar.user_email) = lower(?)
			 WHERE r.hunt_id = ?
			 ORDER BY hr.id`,
			email, email, huntID,
		).Scan(&rows).Error
	})
	return rows, err
}
```

(Adjust `esa.` column names to the verified matview definition. If the matview lacks `cohort`, drop that select + model field and pass `cohort: ""` in the web save body instead — do not invent a column.)

`hunt_controller.go` `Get`: `results, err := repositories.ListHuntResults(rctx, db.DB(), huntID, email)`.

`RoundResults` (the agent tool read) is untouched — the new model fields scan as nil there; the tool payload stays accurate.

- [ ] **Step 4: Run backend suite bare, verify PASS**

Run: `cd backend && go test ./...`

- [ ] **Step 5: Commit**

```bash
git add backend/repositories/hunt_repo.go backend/models/hunt.go backend/controllers/hunt_controller.go backend/internal/integration/hunt_test.go
git commit -m "feat(hunt): results carry app data, page depth, and caller saved/rating state"
```

---

### Task 6: Web — results card grid with heart, empty-bubble fix, min_pages label

**Files:**
- Modify: `web/src/lib/api.ts:249-253` (HuntSettings), `:309-327` (HuntResult)
- Modify: `web/src/lib/hunt.ts:19` (DEFAULT_SETTINGS), `:28-36` (parseSettings), `:45-58` (formatSettings)
- Modify: `web/src/components/hunt/RoundBlock.tsx` (grid + cards + heart)
- Modify: `web/src/components/hunt/HuntView.tsx:36-56` (session email), `:371-397` (pass email), `:455-481` (bubble guard)
- Test: the sibling `node --test` file for `web/src/lib/hunt.ts` (grep for it; extend), plus `npx tsc --noEmit` and `npm run lint` as gates

**Interfaces:**
- Consumes: `HuntResult` JSON from Task 5; `upsertSavedGem`, `removeSavedGem`, `setRating` (`web/src/lib/api.ts:165-193`); `useSession` for the email (pattern: `web/src/components/review/ReviewDeck.tsx:13-14`).
- Produces: card grid replacing the row list; one heart per kept result = save + like in one tap (toggle off = remove + rating none).

- [ ] **Step 1: Extend types**

`web/src/lib/api.ts`:

```ts
export interface HuntSettings {
  page_cap?: number;
  patience?: number;
  listing_pages?: number;
  /** Pages to read per term before the dry early-exit may fire (1-30). */
  min_pages?: number;
}
```

`HuntResult` gains:

```ts
  /** SERP page this app was first found on this round; null on pre-158 rows. */
  page: number | null;
  app_name: string | null;
  icon_url: string | null;
  tagline: string | null;
  cohort: string | null;
  /** True when the signed-in user already saved this advertiser. */
  saved: boolean;
  /** The signed-in user's rating for this advertiser, null when unrated. */
  rating: "like" | "neutral" | "dislike" | null;
```

`web/src/lib/hunt.ts`:

```ts
const DEFAULT_SETTINGS = { page_cap: 40, patience: 6, listing_pages: 10, min_pages: 10 } as const;
```

`parseSettings` adds `if (typeof s.min_pages === "number") out.min_pages = s.min_pages;`; `formatSettings` adds (after the patience segment):

```ts
  if (settings.min_pages != null && settings.min_pages !== DEFAULT_SETTINGS.min_pages) {
    parts.push(`min ${settings.min_pages}`);
  }
```

Extend the hunt.ts node --test sibling with cases for both.

NOTE: patience's default changed 3→6 — `formatSettings` now hides `patience 6` and shows `patience 3`; that matches "show only non-default", correct as-is.

- [ ] **Step 2: Card grid + heart in RoundBlock**

Replace `ResultRow` usage with a grid of `ResultCard`s (keep `StoreBadge`, `TriageState`, `adLibraryUrl`, the off-target `<details>` split, and the event feed exactly as they are). `RoundBlock` gains a `userEmail: string | null` prop. Complete component code:

```tsx
/** One found app as a card: icon, name, store badge, GemScore, tagline, and a
 *  save control once the app is kept. All fields are real data from the round;
 *  missing fields render nothing (no placeholders). */
function ResultCard({ result: r, userEmail }: { result: HuntResult; userEmail: string | null }) {
  const scored = r.gem_score != null && r.kept_advertiser_id != null;
  const name = r.app_name ?? r.page_name ?? r.store_id;
  return (
    <div
      style={{
        border: "1px solid var(--line)",
        background: "var(--surface-2)",
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        {r.icon_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- store CDN icon, remote domains not configured for next/image
          <img src={r.icon_url} alt="" width={40} height={40} style={{ flex: "0 0 auto", border: "1px solid var(--line)", objectFit: "cover" }} />
        ) : (
          <span aria-hidden style={{ width: 40, height: 40, flex: "0 0 auto", border: "1px solid var(--line)", background: "var(--surface-3)" }} />
        )}
        <div style={{ minWidth: 0, flex: "1 1 auto" }}>
          {scored ? (
            <Link className="t-sm w-strong" href={`/gems/${r.kept_advertiser_id}`} style={{ color: "var(--ink)", textDecoration: "underline", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title="Open the gem page">
              {name}
            </Link>
          ) : (
            <span className="t-sm w-strong" style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {name}
            </span>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}>
            <StoreBadge store={r.store} href={r.link_url} />
            {scored && <ScoreBadge score={r.gem_score!} variant="card" />}
          </div>
        </div>
        {r.kept_advertiser_id != null && userEmail && <HeartButton result={r} userEmail={userEmail} />}
      </div>
      {r.tagline && (
        <p className="t-xs" style={{ color: "var(--ink-soft)", margin: 0, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {r.tagline}
        </p>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <a className="t-xs" href={adLibraryUrl(r.page_id)} target="_blank" rel="noreferrer" style={{ color: "var(--ink-soft)", textDecoration: "underline" }} title="Open this page's ads in the Meta Ad Library">
          Ads
        </a>
        <span className="t-xs" style={{ color: "var(--muted)" }} title="Search term that surfaced this app">
          {r.source_term}
        </span>
        {r.page != null && (
          <span className="t-xs nums" style={{ color: "var(--muted)" }} title="Ad Library page this app was found on">
            p{r.page}
          </span>
        )}
        {r.was_new && (
          <span className="t-xs w-strong" style={{ color: "var(--up)" }}>
            New
          </span>
        )}
        <TriageState triage={r.triage} />
      </div>
    </div>
  );
}
```

Heart (save + like in one tap; toggle off removes both). Optimistic with revert:

```tsx
/** Save control: one tap saves the app to the Saved page AND records a like
 *  (feeds preference learning). Tapping again removes both. */
function HeartButton({ result: r, userEmail }: { result: HuntResult; userEmail: string }) {
  const [saved, setSaved] = useState(r.saved);
  const [busy, setBusy] = useState(false);
  useEffect(() => setSaved(r.saved), [r.saved]);
  const advertiserId = r.kept_advertiser_id!;

  async function toggle() {
    if (busy) return;
    const next = !saved;
    setSaved(next);
    setBusy(true);
    try {
      if (next) {
        await upsertSavedGem(advertiserId, {
          user_email: userEmail,
          page_name: r.page_name,
          app_name: r.app_name,
          icon_url: r.icon_url,
          cohort: r.cohort ?? "",
          gem_score: r.gem_score ?? 0,
          scaling_velocity: null,
          under_radar: null,
          funnel_intent: null,
          active_ad_count: null,
        });
        await setRating(advertiserId, userEmail, "like", "");
      } else {
        await removeSavedGem(advertiserId, userEmail);
        await setRating(advertiserId, userEmail, "none", "");
      }
    } catch {
      setSaved(!next); // revert; the next poll re-syncs from the server
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
      aria-pressed={saved}
      title={saved ? "Remove from Saved" : "Save and like"}
      style={{ border: "none", background: "transparent", cursor: "pointer", padding: 4, color: saved ? "var(--down)" : "var(--muted)", flex: "0 0 auto" }}
    >
      {saved ? <IconHeartFilled size={18} /> : <IconHeart size={18} stroke={1.75} />}
    </button>
  );
}
```

(Check `@tabler/icons-react` exports used elsewhere in web/ — `IconHeart`/`IconHeartFilled` exist in the installed version; if the repo uses different save iconography on the Saved page, match it.) Check `ApiSavedGemIn`'s full field list in `web/src/types/api.ts:259` and pass every required field (the shape above matches it today).

Grid containers replacing the two list divs in `RoundBlock` (on-target and inside the off-target `<details>`):

```tsx
<div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 10 }}>
  {onTarget.map((r) => (
    <ResultCard key={r.id} result={r} userEmail={userEmail} />
  ))}
</div>
```

Off-target cards keep their `relevance_note` line under each card (same muted style as today). Update the two `RoundBlock` call sites in `HuntView.tsx:387,392` to pass `userEmail`.

- [ ] **Step 3: Email + bubble guard in HuntView**

Top of `HuntView` (mirror ReviewDeck): `import { useSession } from "next-auth/react";` then `const { data: session } = useSession(); const userEmail = session?.user?.email ?? null;`.

`MessageBubble` — render the bordered box only when there is text (a proposal-only turn shows the Peon label + its card, no empty box):

```tsx
function MessageBubble({ message }: { message: HuntMessage }) {
  const isUser = message.role === "user";
  const toolLog = Array.isArray(message.tool_log) ? message.tool_log : [];
  const hasText = message.content.trim() !== "";
  if (!hasText && toolLog.length === 0 && isUser) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: isUser ? "flex-end" : "flex-start" }}>
      {!isUser && <span className="eyebrow">Peon</span>}
      {hasText && (
        <div
          className="t-sm"
          style={{
            maxWidth: "82%",
            whiteSpace: "pre-wrap",
            border: "1.5px solid var(--ink)",
            background: isUser ? "var(--surface-3)" : "var(--surface-2)",
            color: "var(--ink)",
            padding: "10px 12px",
          }}
        >
          {message.content}
        </div>
      )}
      {toolLog.length > 0 && (
        <div className="t-xs font-mono" style={{ maxWidth: "82%", color: "var(--muted)" }}>
          {toolLog.join(" · ")}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Gates (each bare, its own call)**

Run: `cd web && npx tsc --noEmit`
Run: `cd web && npm run lint`
Run: `cd web && node --test <the hunt.ts test file>`
Expected: all clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/hunt.ts web/src/components/hunt/RoundBlock.tsx web/src/components/hunt/HuntView.tsx <hunt.ts test file>
git commit -m "feat(hunt): results card grid with save+like, empty-bubble fix, min_pages label"
```

---

### Task 7: Full gates, deploy, live e2e (controller-run)

- [ ] Full Python suite bare, LAST and SOLO (`AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest -q`) — 0 failed, check the skip count.
- [ ] Full Go suite bare (`cd backend && go test ./...`) — serially, after Python.
- [ ] Web: `npx tsc --noEmit`, `npm run lint`, `npm run build` (bare, separate calls).
- [ ] Migration 158 fresh-apply check (placeholder-safe lint runs in the suite; also apply to a scratch DB or rely on prod apply being first-ever).
- [ ] Build images from a clean `git worktree add ... HEAD` (worker via Cloud Build; api + web via their existing build paths). Check `git status --ignored` for load-bearing gitignored files before building.
- [ ] Deploy order: apply migration 158 → roll worker VM services (worker image; edit BOTH compose copies to add `AGF_API_URL`/`AGF_API_TOKEN` to the hunt service; confirm `AGF_API_TOKEN` is in the VM `.env`) → roll GKE api → roll GKE web (NEG 502 wait).
- [ ] Live e2e on prod: run a real hunt round end-to-end. Verify: Peon wrap-up message with real app names + proposal card in the thread (`frontend.hunt_messages`), grid renders icons/scores in the UI, heart tap writes `frontend.saved_gems` + `frontend.app_ratings` and the app shows on the Saved page, no empty bubble on a proposal-only turn, round telemetry shows terms paging past page 3, and `hunt_results.page` populated.
- [ ] Update memory + run /self-eval and /reflect.
