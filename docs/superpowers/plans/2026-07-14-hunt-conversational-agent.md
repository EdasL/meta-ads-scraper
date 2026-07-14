# Hunt Conversational Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the /hunt Peon chat so scrapes only ever start from the Start hunt button, Peon can browse the web via Gemini's built-in tools, proposals refine in dialogue with cross-hunt memory, rounds post a completion message, and kept results are judged for relevance against the round's intent.

**Architecture:** Go API (`backend/`) hosts the Gemini agent loop and hunt endpoints; Next.js (`web/`) renders the chat; the Python worker (`src/agf/`) runs rounds via `pipeline_jobs kind='hunt'`. Web browsing = Gemini built-in `google_search`+`url_context` tools combined with our function declarations (verified live on the prod key with `gemini-3.5-flash` + `tool_config.include_server_side_tool_invocations`; `gemini-2.5-flash` rejects the combination, and the dev key has no grounding quota). The relevance judge is a new Python module using the repo's raw-REST Gemini pattern.

**Tech Stack:** Go 1.x + gin + gorm, `google.golang.org/genai` v1.58.0 (bump from v1.47.0 — verified source-compatible for every symbol we call), Next.js + TS (`node --test` for lib tests), Python + SQLAlchemy + pytest, Postgres migrations in `db/schema/`.

**Spec:** `docs/superpowers/specs/2026-07-14-hunt-conversational-agent-design.md`

## Global Constraints

- Product copy: plain, clear, professional. No marketing fluff, no exclamation spam. Product nouns intact: **Field Desk**, **GemScore**, **cohort**.
- Migrations: NO bare `%` anywhere in a migration file, including comments (write "pct"/"percent"; use `mod(a,b)`). Lint: `tests/db/test_migrations_placeholder_safe.py`.
- Python DB tests need `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`; unset = silent skip, and a skip is not a pass. Go repo tests are gated by `AGF_IT_DB_URL` (same DB).
- ONE test consumer per shared test DB at a time; the final zero-failed suite runs happen LAST and SOLO.
- Commit directly to `main`, explicit paths only (never `git add -A`), one commit per logical unit.
- Do not put the word TRUNCATE or other SQL keywords in commit messages (deny-rule string matching).
- The turn-budget comment chain: `huntProposalTimeout` (Go) must stay ≥ the browsing turn's real latency; web fetches must not time out earlier than the API budget.
- All new assistant-visible strings follow the voice rules above.

---

### Task 1: genai SDK bump + hunt model/env plumbing

**Files:**
- Modify: `backend/go.mod` (genai v1.47.0 → v1.58.0)
- Modify: `backend/helpers/gemini.go:19-53` (struct + constructor), `:590-604` (`generateWithRetry`)
- Test: `backend/helpers/gemini_hunt_test.go`

**Interfaces:**
- Consumes: existing `GeminiHelper{client, model}`, `NewGeminiHelper()`.
- Produces: `GeminiHelper` gains `huntModel string` and `huntWebTools bool`; `generateWithRetry(ctx, model string, contents, config)` takes the model explicitly. Env: `GEMINI_HUNT_MODEL` (default `gemini-3.5-flash`), `AGF_HUNT_WEB_TOOLS` (default on; `0`/`false` disables). Task 2 calls `h.generateWithRetry(ctx, h.huntModel, …)` and reads `h.huntWebTools`.

- [ ] **Step 1: Bump the SDK**

```bash
cd backend && go get google.golang.org/genai@v1.58.0 && go mod tidy && go build ./...
```
Expected: clean build (verified source-compatible: `GenerateContent`, `FunctionCalls`, `NewPartFromFunctionResponse`, `NewContentFromParts`, `NewClient`, config fields all unchanged v1.47→v1.58).

- [ ] **Step 2: Write the failing test**

Append to `backend/helpers/gemini_hunt_test.go`:

```go
func TestNewGeminiHelperHuntModelDefaults(t *testing.T) {
	t.Setenv("GEMINI_API_KEY", "test-key")
	t.Setenv("GEMINI_MODEL", "")
	t.Setenv("GEMINI_HUNT_MODEL", "")
	t.Setenv("AGF_HUNT_WEB_TOOLS", "")
	h := NewGeminiHelper()
	if h == nil {
		t.Fatal("helper nil with API key set")
	}
	if h.huntModel != "gemini-3.5-flash" {
		t.Fatalf("huntModel = %q, want gemini-3.5-flash", h.huntModel)
	}
	if !h.huntWebTools {
		t.Fatal("huntWebTools should default on")
	}
	if h.model != "gemini-2.5-flash" {
		t.Fatalf("model = %q, want gemini-2.5-flash (unchanged)", h.model)
	}
}

func TestNewGeminiHelperHuntOverrides(t *testing.T) {
	t.Setenv("GEMINI_API_KEY", "test-key")
	t.Setenv("GEMINI_HUNT_MODEL", "gemini-9-test")
	t.Setenv("AGF_HUNT_WEB_TOOLS", "0")
	h := NewGeminiHelper()
	if h.huntModel != "gemini-9-test" {
		t.Fatalf("huntModel = %q", h.huntModel)
	}
	if h.huntWebTools {
		t.Fatal("AGF_HUNT_WEB_TOOLS=0 must disable web tools")
	}
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && go test ./helpers/ -run TestNewGeminiHelperHunt -v`
Expected: FAIL (unknown fields `huntModel`/`huntWebTools`).

- [ ] **Step 4: Implement**

In `gemini.go`, extend the struct and constructor (keep existing lines; additions shown):

```go
type GeminiHelper struct {
	client       *genai.Client
	model        string
	huntModel    string
	huntWebTools bool
}
```

In `NewGeminiHelper()` after the existing `model` default block:

```go
	huntModel := os.Getenv("GEMINI_HUNT_MODEL")
	if huntModel == "" {
		// gemini-2.5-flash rejects built-in tools combined with function
		// calling; the hunt agent needs a Gemini 3 model.
		huntModel = "gemini-3.5-flash"
	}
	wt := strings.ToLower(strings.TrimSpace(os.Getenv("AGF_HUNT_WEB_TOOLS")))
	huntWebTools := wt != "0" && wt != "false"
	return &GeminiHelper{client: client, model: model, huntModel: huntModel, huntWebTools: huntWebTools}
```

Change `generateWithRetry` to take the model (and update its two existing callers to pass `h.model`; `GenerateHuntTurn` switches to `h.huntModel` in Task 2):

```go
func (h *GeminiHelper) generateWithRetry(ctx context.Context, model string, contents []*genai.Content, config *genai.GenerateContentConfig) (*genai.GenerateContentResponse, error) {
	var result *genai.GenerateContentResponse
	var err error
	maxRetries := 3
	for attempt := 0; attempt < maxRetries; attempt++ {
		result, err = h.client.Models.GenerateContent(ctx, model, contents, config)
		if err == nil {
			return result, nil
		}
		waitTime := time.Duration(1<<attempt) * time.Second
		// (keep the rest of the existing body unchanged)
```

- [ ] **Step 5: Run tests, then commit**

Run: `cd backend && go test ./helpers/ -v` — expected: all PASS.

```bash
git add backend/go.mod backend/go.sum backend/helpers/gemini.go backend/helpers/gemini_hunt_test.go
git commit -m "feat(api): genai v1.58 + hunt model env (gemini-3.5-flash, web-tools flag)"
```

---

### Task 2: Agent loop rework — web tools, propose-only, intent, richer context

**Files:**
- Modify: `backend/helpers/gemini.go` (`huntAgentInstruction` :439-462, tool decls :480-512, loop :514-586, `planSchema` :620-630, `planFromArgs` :636-650, `buildHuntTurnContents` :692-725; delete `buildStartTurn` :681-687 and `PlanIsRunnable` :673-675)
- Test: `backend/helpers/gemini_hunt_test.go`

**Interfaces:**
- Consumes: Task 1's `h.huntModel`, `h.huntWebTools`, `generateWithRetry(ctx, model, …)`.
- Produces (Task 4's controller compiles against these):
  - `HuntPlan{Terms []string; CompetitorApps []string; Settings map[string]int; Intent string}`
  - `HuntTurn{Reply string; Proposal *HuntPlan; ToolLog []string}` — **no `Start` field**
  - `HuntTurnInput{Message string; History []HuntHistoryTurn; TriedTerms []string; RoundSummaries []string; PendingProposal string; CardState string; PriorHunts []string}`
  - `huntToolDecls() []*genai.FunctionDeclaration` (3 tools: get_round_report, get_round_results, propose_round)
  - `serverToolLines(parts []*genai.Part) []string`

- [ ] **Step 1: Write the failing tests**

Replace `TestPlanFromArgsProposeVsStartStrip` and `TestBuildStartTurnEmptyPlanNoLog` in `gemini_hunt_test.go` with:

```go
func TestPlanFromArgsIntentAndAllowTried(t *testing.T) {
	args := map[string]any{
		"terms":       []any{"sleep stories app", "already tried"},
		"intent":      "sleep apps aimed at new parents",
		"allow_tried": true,
	}
	plan := planFromArgs(args, []string{"already tried"})
	if plan.Intent != "sleep apps aimed at new parents" {
		t.Fatalf("intent = %q", plan.Intent)
	}
	if !reflect.DeepEqual(plan.Terms, []string{"sleep stories app", "already tried"}) {
		t.Fatalf("allow_tried must keep tried terms, got %v", plan.Terms)
	}
	args["allow_tried"] = false
	plan = planFromArgs(args, []string{"already tried"})
	if !reflect.DeepEqual(plan.Terms, []string{"sleep stories app"}) {
		t.Fatalf("without allow_tried the strip must apply, got %v", plan.Terms)
	}
}

func TestHuntToolDeclsHasNoStartRound(t *testing.T) {
	var names []string
	for _, d := range huntToolDecls() {
		names = append(names, d.Name)
	}
	want := []string{"get_round_report", "get_round_results", "propose_round"}
	if !reflect.DeepEqual(names, want) {
		t.Fatalf("tool decls = %v, want %v", names, want)
	}
}

func TestServerToolLines(t *testing.T) {
	parts := []*genai.Part{
		{Text: "thinking"},
		{ToolCall: &genai.ToolCall{ToolType: genai.ToolTypeGoogleSearchWeb}},
		{ToolCall: &genai.ToolCall{ToolType: genai.ToolTypeURLContext}},
		nil,
	}
	got := serverToolLines(parts)
	want := []string{"searched the web", "read a page"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("serverToolLines = %v, want %v", got, want)
	}
}

func TestBuildHuntTurnContentsRicherContext(t *testing.T) {
	in := HuntTurnInput{
		Message:         "drop the second term",
		PendingProposal: "terms: a, b · intent: sleep apps",
		CardState:       "terms: a, b (competitors: calm)",
		PriorHunts:      []string{"hunt 3 round 9: 4 terms, 12 found, 3 kept"},
	}
	contents := buildHuntTurnContents(in)
	last := contents[len(contents)-1].Parts[0].Text
	for _, want := range []string{"Current proposal awaiting approval", "sleep apps", "The proposal card currently shows", "Prior hunts", "hunt 3 round 9"} {
		if !strings.Contains(last, want) {
			t.Fatalf("final turn missing %q:\n%s", want, last)
		}
	}
}
```

- [ ] **Step 2: Run to verify failures**

Run: `cd backend && go test ./helpers/ -run 'TestPlanFromArgsIntent|TestHuntToolDecls|TestServerToolLines|TestBuildHuntTurnContentsRicher' -v`
Expected: FAIL / compile errors (missing `Intent`, `huntToolDecls`, `serverToolLines`).

- [ ] **Step 3: Implement the loop rework**

In `gemini.go`:

3a. Types: add `Intent string` to `HuntPlan`; delete the `Start *HuntPlan` field from `HuntTurn` (and its doc line); add to `HuntTurnInput`:

```go
	// PendingProposal is the latest un-started proposal, formatted for the
	// model; empty when none is pending. CardState is what the founder's
	// proposal card currently shows (they may have edited chips). PriorHunts
	// are compact one-line summaries from the founder's other hunts.
	PendingProposal string
	CardState       string
	PriorHunts      []string
```

3b. Replace `huntAgentInstruction` verbatim:

```go
const huntAgentInstruction = "You are Peon, Field Desk's discovery agent. You help the founder find " +
	"emerging mobile apps by running Meta Ad Library search rounds and researching niches on the web.\n" +
	"\n" +
	"Search terms are generic niche queries a user would type (e.g. 'sleep stories app', " +
	"'ai bedtime audio') — short, plain, and never brand or app names. Any brand or app the " +
	"founder names belongs in competitor_apps, never in terms. At most 15 terms and at most " +
	"3 competitor apps.\n" +
	"\n" +
	"You cannot start a round. propose_round suggests a plan the founder approves with the " +
	"Start hunt button — that button is the only way a round starts. When the founder asks to " +
	"run or re-run something, reply with a propose_round call (set allow_tried true for " +
	"re-runs so previously tried terms are kept) and note they can press Start hunt. Never " +
	"say a round is running unless round telemetry says so.\n" +
	"\n" +
	"When a proposal is awaiting approval, a new propose_round is a refinement: keep " +
	"everything the founder did not ask to change, starting from the proposal card state " +
	"when it is provided.\n" +
	"\n" +
	"Every propose_round includes intent: one plain sentence saying what the founder is " +
	"hunting, from the conversation (e.g. 'sleep apps aimed at new parents'). Results are " +
	"judged against it after the round runs.\n" +
	"\n" +
	"Use web search and page reading when they help: niche research, a link the founder " +
	"pastes, a store listing. Before answering about a prior round, call get_round_report " +
	"and/or get_round_results — do not guess round outcomes.\n" +
	"\n" +
	"Optional round settings and their bounds: page_cap (Ad Library pages per term, 1-100), " +
	"patience (empty pages tolerated before stopping a term, 1-10), listing_pages (store " +
	"listing pages per app, 1-30). Set only the ones the founder asked for; when asked what " +
	"a setting means, just explain it.\n" +
	"\n" +
	"Your reply text is 1-3 plain, professional sentences. The name Peon appears only in " +
	"prose, never inside terms or competitor apps."
```

3c. Extract the declarations (delete the `start_round` entry):

```go
// huntToolDecls is the fixed tool surface of the hunt agent. There is
// deliberately no tool that starts a round — rounds start only from the
// founder's Start hunt button (POST /hunts/:id/rounds).
func huntToolDecls() []*genai.FunctionDeclaration {
	return []*genai.FunctionDeclaration{
		{
			Name:        "get_round_report",
			Description: "Telemetry for one prior round: settings used, per-term pages/found/new and why each term stopped, triage counts.",
			Parameters: &genai.Schema{
				Type:       genai.TypeObject,
				Properties: map[string]*genai.Schema{"round_id": {Type: genai.TypeInteger, Description: "The round number to inspect."}},
				Required:   []string{"round_id"},
			},
		},
		{
			Name:        "get_round_results",
			Description: "Result rows for one prior round (page name, store, source term, was_new, triage, relevance, gem score); optional triage filter kept|vetoed|pending.",
			Parameters: &genai.Schema{
				Type: genai.TypeObject,
				Properties: map[string]*genai.Schema{
					"round_id": {Type: genai.TypeInteger, Description: "The round number to inspect."},
					"triage":   {Type: genai.TypeString, Description: "Optional triage filter: kept, vetoed, or pending. Omit for all rows."},
				},
				Required: []string{"round_id"},
			},
		},
		{
			Name:        "propose_round",
			Description: "Suggest a round for the founder to approve with the Start hunt button. The only terminal action; you cannot start rounds yourself.",
			Parameters:  planSchema(),
		},
	}
}
```

3d. `planSchema()` gains two properties and a second required key (keep the existing `terms`/`competitor_apps`/`settings` entries verbatim):

```go
		"intent": {Type: genai.TypeString, Description: "One plain sentence: what the founder is hunting."},
		"allow_tried": {Type: genai.TypeBoolean, Description: "True only when the founder explicitly asked to re-run previous terms."},
```
and `Required: []string{"terms", "intent"}`.

3e. `planFromArgs` — honor `allow_tried`, carry intent:

```go
func planFromArgs(args map[string]any, tried []string) HuntPlan {
	if b, ok := args["allow_tried"].(bool); ok && b {
		// Explicit founder re-run: re-emitted prior terms are allowed.
		tried = nil
	}
	plan := HuntPlan{
		Terms:          cleanHuntTerms(stringSlice(args["terms"]), tried),
		CompetitorApps: capStrings(stringSlice(args["competitor_apps"]), 3),
		Settings:       map[string]int{},
		Intent:         stringArg(args, "intent"),
	}
	if raw, ok := args["settings"].(map[string]any); ok {
		plan.Settings = ClampHuntSettings(raw)
	}
	return plan
}
```

3f. `serverToolLines` (new, near `firstText`):

```go
// serverToolLines maps server-side built-in tool invocations (Gemini runs
// google_search/url_context itself) to the plain tool_log sentences the chat
// shows under Peon's reply.
func serverToolLines(parts []*genai.Part) []string {
	var out []string
	for _, p := range parts {
		if p == nil || p.ToolCall == nil {
			continue
		}
		switch p.ToolCall.ToolType {
		case genai.ToolTypeGoogleSearchWeb, genai.ToolTypeGoogleSearchImage:
			out = append(out, "searched the web")
		case genai.ToolTypeURLContext:
			out = append(out, "read a page")
		}
	}
	return out
}
```

3g. `GenerateHuntTurn` — config, model, loop:

```go
	tools := []*genai.Tool{{FunctionDeclarations: huntToolDecls()}}
	config := &genai.GenerateContentConfig{
		SystemInstruction: &genai.Content{Parts: []*genai.Part{{Text: huntAgentInstruction}}},
		Tools:             tools,
		MaxOutputTokens:   2048,
	}
	if h.huntWebTools {
		// Built-in tools run server-side inside one model call; combining them
		// with function declarations needs this flag (verified live: rejected
		// without it, and rejected entirely on gemini-2.5-flash).
		config.Tools = append(config.Tools,
			&genai.Tool{GoogleSearch: &genai.GoogleSearch{}},
			&genai.Tool{URLContext: &genai.URLContext{}},
		)
		config.ToolConfig = &genai.ToolConfig{IncludeServerSideToolInvocations: genai.Ptr(true)}
	}
```

Model call becomes `h.generateWithRetry(ctx, h.huntModel, contents, config)`. Right after a successful response, before the `FunctionCalls()` check, add:

```go
		if len(resp.Candidates) > 0 && resp.Candidates[0].Content != nil {
			toolLog = append(toolLog, serverToolLines(resp.Candidates[0].Content.Parts)...)
		}
```

In the switch, delete the whole `start_round` branch and its case label — the case becomes `case "propose_round":` only. Delete `buildStartTurn` and `PlanIsRunnable` (grep first: `grep -rn "PlanIsRunnable\|buildStartTurn" backend/` — the controller reference goes away in Task 4; if Task 4 is not done yet, leave `PlanIsRunnable` in place and delete it in Task 4 instead; `buildStartTurn` is deleted here).

3h. `buildHuntTurnContents` — extend the final user turn. After the existing prior-rounds block, before the message:

```go
	if len(in.PriorHunts) > 0 {
		b.WriteString("Prior hunts (other threads):\n")
		for _, line := range in.PriorHunts {
			b.WriteString("  " + line + "\n")
		}
	}
	if in.PendingProposal != "" {
		b.WriteString("Current proposal awaiting approval: " + in.PendingProposal + "\n")
	}
	if in.CardState != "" {
		b.WriteString("The proposal card currently shows: " + in.CardState + "\n")
	}
```
(Match the file's actual string-building idiom — it may use `fmt.Fprintf`/`strings.Builder`; keep its style.)

- [ ] **Step 4: Run helper tests**

Run: `cd backend && go test ./helpers/ -v`
Expected: PASS (the controller still compiles because Task 4 hasn't removed its `turn.Start` use yet — if `go build ./...` breaks on `HuntTurn.Start`, comment says: do Task 2 and Task 4 in one branch-local sequence; the intermediate commit may be helpers-only via `go test ./helpers/`. If the repo must build at every commit, fold the minimal controller edit from Task 4 Step 3a into this commit.)

**Note for the implementer:** deleting `HuntTurn.Start` breaks `controllers/hunt_controller.go` until Task 4. To keep every commit green, this task's commit includes the minimal controller change: in `runTurn`, delete lines 289-297 (`startRound := …` through the `reply` fallback), the `if startRound { … }` block inside the write txn (:312-317), and the `if !startRound … ListHuntRounds` tail (:325-340) — `runTurn` returns `(ok bool)` only and `writeThread` is called with `nil` round by both callers. Task 4 finishes the rest (card state, pending proposal, prior hunts, intent on StartRound).

- [ ] **Step 5: Build + full package tests, commit**

Run: `cd backend && go build ./... && go test ./helpers/ ./controllers/ -count=1`
Expected: PASS.

```bash
git add backend/helpers/gemini.go backend/helpers/gemini_hunt_test.go backend/controllers/hunt_controller.go
git commit -m "feat(api): Peon agent — web tools, propose-only terminal, intent, refine context"
```

---

### Task 3: Migration 157 + hunt repo/model surfacing

**Files:**
- Create: `db/schema/157_hunt_intent_relevance.sql`
- Modify: `backend/repositories/hunt_repo.go` (`InsertHuntRound`/`InsertHuntRoundTx` :207-229, `ListHuntResults` :118-134, `RoundResults` :168-186, `RoundReport` :140-163), `backend/models/hunt.go` (`HuntResultRow` :58-71, `HuntRoundReport` :44-54)
- Test: `backend/repositories/hunt_repo_test.go`, plus the migration applies via the Python fixture

**Interfaces:**
- Consumes: nothing new.
- Produces: `InsertHuntRound(ctx, g, huntID int, messageID *int, terms, competitors, settings json.RawMessage, intent *string) (int, error)` and same-shaped `InsertHuntRoundTx(tx, …)`; `HuntResultRow` gains `Relevance *string \`gorm:"column:relevance" json:"relevance"\`` and `RelevanceNote *string \`gorm:"column:relevance_note" json:"relevance_note"\``; `HuntRoundReport` gains `OffTargetCount int \`json:"off_target_count"\``. Python (Tasks 5-6) reads/writes the new columns directly.

- [ ] **Step 1: Write the migration**

`db/schema/157_hunt_intent_relevance.sql`:

```sql
-- Hunt round intent + per-result relevance verdicts.
-- intent: one founder-facing sentence the round's results are judged against.
-- relevance: NULL (unjudged, treated as relevant) | 'relevant' | 'off_target'.
ALTER TABLE frontend.hunt_rounds
    ADD COLUMN IF NOT EXISTS intent TEXT;
ALTER TABLE frontend.hunt_results
    ADD COLUMN IF NOT EXISTS relevance TEXT,
    ADD COLUMN IF NOT EXISTS relevance_note TEXT;
```

- [ ] **Step 2: Verify the migration applies fresh + lint**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/db/test_migrations_placeholder_safe.py -q` — expected: PASS (no bare pct sign).
Then confirm a fresh apply: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/db/ -q` (the `_migrated_engine` fixture runs `apply_migrations`). Expected: PASS.

- [ ] **Step 3: Failing Go repo test**

Append to `backend/repositories/hunt_repo_test.go` (follow the file's existing `AGF_IT_DB_URL` skip-gate and seeding helpers):

```go
func TestInsertHuntRoundIntentAndRelevanceColumns(t *testing.T) {
	g := testDB(t) // existing helper in this file; skips without AGF_IT_DB_URL
	huntID := seedHuntRow(t, g, "it-intent@test.local", "intent hunt")
	intent := "sleep apps aimed at new parents"
	id, err := repositories.InsertHuntRound(context.Background(), g, huntID, nil,
		json.RawMessage(`["sleep stories app"]`), nil, nil, &intent)
	if err != nil {
		t.Fatalf("insert: %v", err)
	}
	var got *string
	if err := g.Raw("SELECT intent FROM frontend.hunt_rounds WHERE id = ?", id).Scan(&got).Error; err != nil {
		t.Fatal(err)
	}
	if got == nil || *got != intent {
		t.Fatalf("intent = %v", got)
	}
}
```
(If the file's helpers differ in name, mirror its existing round-insert test verbatim and add the intent argument + assertion — the helpers exist, `TestHuntStartRoundHappyPath` seeds the same shapes at the integration level.)

- [ ] **Step 4: Run to verify it fails**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./repositories/ -run TestInsertHuntRoundIntent -v`
Expected: FAIL (too many arguments to `InsertHuntRound`).

- [ ] **Step 5: Implement repo + models**

`InsertHuntRoundTx` — add the param and column:

```go
func InsertHuntRoundTx(tx *gorm.DB, huntID int, messageID *int, terms, competitors, settings json.RawMessage, intent *string) (int, error) {
	var id int
	err := tx.Raw(
		`INSERT INTO frontend.hunt_rounds (hunt_id, message_id, terms, competitor_apps, settings, intent)
		 VALUES (?, ?, CAST(? AS jsonb), CAST(? AS jsonb), CAST(? AS jsonb), ?)
		 RETURNING id`,
		huntID, messageID, jsonbArgDefault(terms), jsonbArgDefault(competitors), jsonbArgDefaultObj(settings), intent,
	).Scan(&id).Error
	// (keep the existing pipeline_jobs insert that follows, unchanged)
```
Thread the same param through `InsertHuntRound` and every caller (`hunt_controller.go` StartRound — Task 4 passes the real value; until then pass `nil` to keep the build green).

`ListHuntResults` and `RoundResults`: add `hr.relevance, hr.relevance_note,` to both select lists (before `s.gem_score`). `HuntResultRow`: add the two pointer fields shown in Interfaces. `RoundReport`: add to its select `count(hr.id) FILTER (WHERE hr.relevance = 'off_target') AS off_target_count` and the `OffTargetCount` model field.

- [ ] **Step 6: Run tests + commit**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./repositories/ -count=1 && go build ./...`
Expected: PASS.

```bash
git add db/schema/157_hunt_intent_relevance.sql backend/repositories/hunt_repo.go backend/models/hunt.go backend/repositories/hunt_repo_test.go backend/controllers/hunt_controller.go
git commit -m "feat(db,api): migration 157 — round intent + result relevance, surfaced in hunt reads"
```

---

### Task 4: Controller — card state, pending proposal, prior hunts, intent on StartRound

**Files:**
- Modify: `backend/controllers/hunt_controller.go` (`huntMessageBody`, `Message` :195-251, `runTurn` :261-341, `marshalHuntPlan` :366-376, `startRoundBody` :454-459, `StartRound` :464-524, `writeThread` :345-361)
- Modify: `backend/repositories/hunt_repo.go` (new `PriorHuntLines`)
- Test: `backend/internal/integration/hunt_test.go`

**Interfaces:**
- Consumes: Task 2's `HuntTurnInput{…, PendingProposal, CardState, PriorHunts}` and 3-tool loop; Task 3's `InsertHuntRound(…, intent *string)`.
- Produces: `POST /hunts/:id/messages` accepts optional `card_state {terms []string, competitor_apps []string}`; `POST /hunts/:id/rounds` accepts optional `intent string` (cap 200 chars); assistant `proposal` jsonb now carries `intent`; `repositories.PriorHuntLines(ctx, g, email string, excludeHuntID int) ([]string, error)`.

- [ ] **Step 1: Failing integration tests**

Append to `backend/internal/integration/hunt_test.go` (mirror the file's `seedHunt`/`freshHuntKey`/`authed` helpers — Gemini chat paths stay excluded per the file header; these test the non-Gemini surface):

```go
func TestHuntStartRoundStoresIntent(t *testing.T) {
	email, key := freshHuntKey(t)
	huntID := seedHunt(t, email, "intent hunt")
	body := `{"message_id": null, "terms": ["sleep stories app"], "competitor_apps": [], "intent": "sleep apps aimed at new parents"}`
	w := authed(t, "POST", fmt.Sprintf("/api/hunts/%d/rounds", huntID), key, body)
	if w.Code != http.StatusAccepted {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	var got *string
	if err := db.DB().Raw("SELECT intent FROM frontend.hunt_rounds WHERE hunt_id = ?", huntID).Scan(&got).Error; err != nil {
		t.Fatal(err)
	}
	if got == nil || *got != "sleep apps aimed at new parents" {
		t.Fatalf("intent = %v", got)
	}
}

func TestPriorHuntLinesScopedToOwner(t *testing.T) {
	email, _ := freshHuntKey(t)
	otherID := seedHunt(t, email, "other hunt")
	currentID := seedHunt(t, email, "current hunt")
	foreignID := seedHunt(t, "someone-else@test.local", "foreign hunt")
	_ = foreignID
	seedRoundWithResults(t, otherID) // helper below if absent: insert one round + 2 results, 1 kept
	lines, err := repositories.PriorHuntLines(context.Background(), db.DB(), email, currentID)
	if err != nil {
		t.Fatal(err)
	}
	if len(lines) == 0 {
		t.Fatal("expected at least one prior-hunt line")
	}
	for _, l := range lines {
		if strings.Contains(l, "foreign hunt") {
			t.Fatalf("foreign hunt leaked: %q", l)
		}
		if strings.Contains(l, "current hunt") {
			t.Fatalf("current hunt must be excluded: %q", l)
		}
	}
}
```

- [ ] **Step 2: Run to verify failures**

Run: `cd backend && AGF_IT_DB_URL=… go test ./internal/integration/ -run 'TestHuntStartRoundStoresIntent|TestPriorHuntLines' -v`
(Integration tests use the shared test DB — check nothing else is running against it.)
Expected: FAIL (unknown field intent / undefined PriorHuntLines).

- [ ] **Step 3: Implement**

3a. `startRoundBody` gains `Intent *string \`json:"intent"\``. In `StartRound`, after competitors validation:

```go
	var intent *string
	if body.Intent != nil {
		v := strings.TrimSpace(*body.Intent)
		if len(v) > 200 {
			v = v[:200]
		}
		if v != "" {
			intent = &v
		}
	}
```
and pass `intent` to `InsertHuntRound`.

3b. `huntMessageBody` gains:

```go
	CardState *struct {
		Terms          []string `json:"terms"`
		CompetitorApps []string `json:"competitor_apps"`
	} `json:"card_state"`
```

3c. In `Message`, after loading `prior` and `summaries`, also load rounds and build the new context (all before the user-message insert, reusing the same `rctx`):

```go
	rounds, err := repositories.ListHuntRounds(rctx, db.DB(), huntID)
	if err != nil { /* same 500 pattern as the neighbors */ }
	priorHunts, err := repositories.PriorHuntLines(rctx, db.DB(), email, huntID)
	if err != nil { /* same 500 pattern */ }
```

and pass into `runTurn`'s input:

```go
	input := helpers.HuntTurnInput{
		Message:         message,
		History:         history,
		TriedTerms:      tried,
		RoundSummaries:  roundLines,
		PendingProposal: pendingProposalText(prior, rounds),
		CardState:       cardStateText(body.CardState),
		PriorHunts:      priorHunts,
	}
```

New helpers at the bottom of the controller:

```go
// pendingProposalText formats the newest assistant proposal that no round has
// consumed — the same rule the web uses to decide which card renders.
func pendingProposalText(msgs []models.HuntMessageRow, rounds []models.HuntRoundRow) string {
	started := map[int]bool{}
	for _, r := range rounds {
		if r.MessageID != nil {
			started[*r.MessageID] = true
		}
	}
	for i := len(msgs) - 1; i >= 0; i-- {
		m := msgs[i]
		if m.Role != "assistant" || len(m.Proposal) == 0 || started[m.ID] {
			continue
		}
		var p struct {
			Terms          []string       `json:"terms"`
			CompetitorApps []string       `json:"competitor_apps"`
			Settings       map[string]int `json:"settings"`
			Intent         string         `json:"intent"`
		}
		if err := json.Unmarshal(m.Proposal, &p); err != nil {
			return ""
		}
		out := "terms: " + strings.Join(p.Terms, ", ")
		if len(p.CompetitorApps) > 0 {
			out += " · competitors: " + strings.Join(p.CompetitorApps, ", ")
		}
		if len(p.Settings) > 0 {
			out += " · settings: " + settingsLabel(p.Settings)
		}
		if p.Intent != "" {
			out += " · intent: " + p.Intent
		}
		return out
	}
	return ""
}

func settingsLabel(s map[string]int) string {
	var parts []string
	for _, k := range []string{"page_cap", "patience", "listing_pages"} {
		if v, ok := s[k]; ok {
			parts = append(parts, fmt.Sprintf("%s %d", k, v))
		}
	}
	return strings.Join(parts, ", ")
}

func cardStateText(cs *struct {
	Terms          []string `json:"terms"`
	CompetitorApps []string `json:"competitor_apps"`
}) string {
	if cs == nil || len(cs.Terms) == 0 {
		return ""
	}
	out := "terms: " + strings.Join(cs.Terms, ", ")
	if len(cs.CompetitorApps) > 0 {
		out += " (competitors: " + strings.Join(cs.CompetitorApps, ", ") + ")"
	}
	return out
}
```
(If an anonymous struct in the signature reads badly, declare `type huntCardState struct{…}` and use it in both the body and helper — implementer's choice, keep it small. `email` is already in scope in `Message` from the owner check; if not, fetch it the same way `requireOwner` does.)

3d. `marshalHuntPlan` — add intent:

```go
	return json.Marshal(struct {
		Terms          []string       `json:"terms"`
		CompetitorApps []string       `json:"competitor_apps"`
		Settings       map[string]int `json:"settings"`
		Intent         string         `json:"intent,omitempty"`
	}{emptyIfNil(p.Terms), emptyIfNil(p.CompetitorApps), clampedOrEmpty(p.Settings), p.Intent})
```

3e. `runTurn`/`writeThread`: complete the Task 2 interim edit — `runTurn(ctx, huntID, input) bool` (no round return), `writeThread(ctx, huntID, status)` (drop the round key entirely; the web's `HuntChatTurn.round` is optional). Delete `PlanIsRunnable` in helpers if it survived Task 2.

3f. `PriorHuntLines` in `hunt_repo.go`:

```go
// PriorHuntLines summarizes the owner's other hunts for Peon's context:
// newest rounds first, one line each, capped so the prompt stays small.
func PriorHuntLines(ctx context.Context, g *gorm.DB, email string, excludeHuntID int) ([]string, error) {
	var rows []struct {
		HuntID    int
		Title     string
		RoundID   int
		Terms     json.RawMessage
		Status    string
		Found     int
		Kept      int
	}
	err := g.WithContext(ctx).Raw(`
		SELECT h.id AS hunt_id, h.title, r.id AS round_id, r.terms, r.status,
		       (SELECT count(*) FROM frontend.hunt_results hr WHERE hr.round_id = r.id) AS found,
		       (SELECT count(*) FROM frontend.hunt_results hr WHERE hr.round_id = r.id AND hr.triage = 'kept') AS kept
		FROM frontend.hunt_rounds r
		JOIN frontend.hunts h ON h.id = r.hunt_id
		WHERE h.user_email = ? AND h.id <> ?
		ORDER BY r.id DESC
		LIMIT 50`, email, excludeHuntID).Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	var out []string
	for _, r := range rows {
		var terms []string
		_ = json.Unmarshal(r.Terms, &terms)
		out = append(out, fmt.Sprintf("hunt %d (%s) round %d [%s]: terms: %s — %d found, %d kept",
			r.HuntID, r.Title, r.RoundID, r.Status, strings.Join(terms, ", "), r.Found, r.Kept))
	}
	return out, nil
}
```

- [ ] **Step 4: Run tests + commit**

Run: `cd backend && go build ./... && AGF_IT_DB_URL=… go test ./internal/integration/ ./repositories/ ./controllers/ ./helpers/ -count=1`
Expected: PASS.

```bash
git add backend/controllers/hunt_controller.go backend/repositories/hunt_repo.go backend/internal/integration/hunt_test.go
git commit -m "feat(api): hunt chat context (pending proposal, card state, prior hunts) + intent on round start"
```

---

### Task 5: Python relevance judge

**Files:**
- Create: `src/agf/discovery/hunt_judge.py`
- Modify: `src/agf/db/hunts.py` (`load_round` :27 — add `intent`), `src/agf/discovery/hunt.py` (phase 5, :258-282)
- Test: Create `tests/discovery/test_hunt_judge.py`; modify `tests/discovery/test_hunt.py` if its phase-5 assertions break

**Interfaces:**
- Consumes: migration 157 columns; `load_round` row gains `.intent`; `agf.net.http_backoff.post_json`; `begin(engine)` conn pattern.
- Produces: `judge_round(engine, round_id: int, intent: str | None, *, model=_MODEL, api_key=None, _post=None) -> dict` returning `{"judged": int, "off_target": int}` (plus `"skipped"` reason when it didn't run). `hunt.py` filters enrich enqueue by `COALESCE(relevance,'relevant') <> 'off_target'`.

- [ ] **Step 1: Failing tests**

`tests/discovery/test_hunt_judge.py` (uses the root `pg_conn`/`_migrated_engine` fixtures; seed shapes copied from `tests/db/test_hunts.py`'s `_seed_hunt`/`_seed_round`/`_result_row`):

```python
import json

from sqlalchemy import text

from agf.discovery.hunt_judge import judge_round


def _seed(conn):
    hunt_id = conn.execute(text(
        "INSERT INTO frontend.hunts (user_email, title) VALUES ('j@t.local', 'judge') RETURNING id"
    )).scalar_one()
    round_id = conn.execute(text(
        "INSERT INTO frontend.hunt_rounds (hunt_id, terms, intent) "
        "VALUES (:h, '[\"sleep app\"]', 'sleep apps for new parents') RETURNING id"
    ), {"h": hunt_id}).scalar_one()
    ids = []
    for i, name in enumerate(["Baby Sleep Sounds", "Casino Slots Party"]):
        ids.append(conn.execute(text(
            "INSERT INTO frontend.hunt_results (round_id, page_id, page_name, store, store_id, "
            "source_term, was_new, triage) VALUES (:r, :p, :n, 'ios', :s, 'sleep app', true, 'kept') "
            "RETURNING id"
        ), {"r": round_id, "p": f"pg{i}", "n": name, "s": f"id{i}"}).scalar_one())
    return round_id, ids


def test_judge_round_marks_off_target(_migrated_engine, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from agf.db.engine import begin  # match hunt.py's import path
    with begin(_migrated_engine) as conn:
        round_id, ids = _seed(conn)

    def fake_post(url, body):
        verdicts = [
            {"id": ids[0], "relevant": True, "reason": "matches the ask"},
            {"id": ids[1], "relevant": False, "reason": "casino game, not a sleep app"},
        ]
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"verdicts": verdicts})}]}}]}

    stats = judge_round(_migrated_engine, round_id, "sleep apps for new parents", _post=fake_post)
    assert stats == {"judged": 2, "off_target": 1}
    with begin(_migrated_engine) as conn:
        rows = dict(conn.execute(text(
            "SELECT id, relevance FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id}).fetchall())
    assert rows[ids[0]] == "relevant"
    assert rows[ids[1]] == "off_target"
    # cleanup
    with begin(_migrated_engine) as conn:
        conn.execute(text("DELETE FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunt_rounds WHERE id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunts WHERE title = 'judge'"))


def test_judge_round_fails_open(_migrated_engine, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from agf.db.engine import begin
    with begin(_migrated_engine) as conn:
        round_id, ids = _seed(conn)

    def broken_post(url, body):
        raise KeyError("boom")

    stats = judge_round(_migrated_engine, round_id, "sleep apps for new parents", _post=broken_post)
    assert stats["judged"] == 0
    with begin(_migrated_engine) as conn:
        left = conn.execute(text(
            "SELECT count(*) FROM frontend.hunt_results WHERE round_id = :r AND relevance IS NOT NULL"),
            {"r": round_id}).scalar_one()
    assert left == 0  # NULL relevance everywhere = treated as relevant
    with begin(_migrated_engine) as conn:
        conn.execute(text("DELETE FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunt_rounds WHERE id = :r"), {"r": round_id})
        conn.execute(text("DELETE FROM frontend.hunts WHERE title = 'judge'"))


def test_judge_round_no_intent_skips(_migrated_engine):
    stats = judge_round(_migrated_engine, 999999, "")
    assert stats["judged"] == 0 and "skipped" in stats
```
(Check `begin`'s real import path first — `grep -n "from agf.db" src/agf/discovery/hunt.py` — and mirror it.)

- [ ] **Step 2: Run to verify failures**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt_judge.py -q`
Expected: FAIL (`ModuleNotFoundError: agf.discovery.hunt_judge`).

- [ ] **Step 3: Implement `src/agf/discovery/hunt_judge.py`**

```python
"""LLM relevance judge for hunt rounds.

Ad Library keyword search returns pages that match the term but not the
founder's ask. After triage keeps a result, this module judges it against the
round's intent (one founder sentence) using Gemini structured output.

Fail-open by design: any error leaves relevance NULL, which every reader
treats as relevant. A judge outage must never hide results.

Network calls run OUTSIDE any DB transaction; reads and writes are their own
short transactions.
"""
from __future__ import annotations

import json
import logging
import os

from sqlalchemy import text

from agf.db.engine import begin  # adjust to the import hunt.py actually uses
from agf.net.http_backoff import HttpBackoffError, post_json

log = logging.getLogger(__name__)

_GEN_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent?key={key}")
_MODEL = "gemini-2.5-flash-lite"
_BATCH = 20

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "relevant": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "relevant", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}

_PROMPT = (
    "The founder is hunting: {intent}\n\n"
    "Each numbered item below is an app found by keyword search in an ad "
    "library. Decide for each whether it plausibly matches what the founder "
    "is hunting. Keyword overlap alone is not relevance; judge from the name, "
    "genre, store listing, and ad text. reason is one short plain sentence.\n\n"
    "{items}"
)


def _kept_rows(conn, round_id: int):
    return conn.execute(text(
        "SELECT hr.id, hr.page_name, a.name AS app_name, a.genre, a.description, ct.body_text "
        "FROM frontend.hunt_results hr "
        "LEFT JOIN public.apps a ON a.store = hr.store AND a.store_id = hr.store_id "
        "LEFT JOIN LATERAL ("
        "    SELECT c.body_text FROM public.ads ad "
        "    JOIN public.creatives c ON c.ad_id = ad.id "
        "    WHERE ad.advertiser_id = hr.kept_advertiser_id AND c.body_text IS NOT NULL "
        "    LIMIT 1"
        ") ct ON true "
        "WHERE hr.round_id = :r AND hr.triage = 'kept'"), {"r": round_id}).fetchall()


def _item_line(r) -> str:
    listing = (r.description or "")[:300]
    ad = (r.body_text or "")[:200]
    return (f"id {r.id}: {r.page_name or r.app_name or '?'}"
            f" | genre: {r.genre or '?'}"
            f" | listing: {listing or '?'}"
            f" | ad text: {ad or '?'}")


def judge_round(engine, round_id: int, intent, *, model: str = _MODEL,
                api_key: str | None = None, _post=None) -> dict:
    """Judge a round's kept results against its intent. Returns counts."""
    intent = (intent or "").strip()
    if not intent:
        return {"judged": 0, "off_target": 0, "skipped": "no intent"}
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        return {"judged": 0, "off_target": 0, "skipped": "no GEMINI_API_KEY"}

    with begin(engine) as conn:
        rows = _kept_rows(conn, round_id)
    if not rows:
        return {"judged": 0, "off_target": 0}

    post = _post or (lambda url, b: post_json(url, b, max_retries=3, timeout=60))
    verdicts: list[tuple[int, str, str]] = []
    for i in range(0, len(rows), _BATCH):
        batch = rows[i:i + _BATCH]
        body = {
            "contents": [{"parts": [{"text": _PROMPT.format(
                intent=intent, items="\n".join(_item_line(r) for r in batch))}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseSchema": _SCHEMA},
        }
        try:
            resp = post(_GEN_URL.format(model=model, key=key), body)
            parsed = json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
            batch_verdicts = parsed["verdicts"]
        except (HttpBackoffError, KeyError, IndexError, TypeError, ValueError) as exc:
            log.warning("hunt judge batch failed (fail open): %s", exc)
            continue
        ids = {r.id for r in batch}
        for v in batch_verdicts:
            if not isinstance(v, dict) or v.get("id") not in ids:
                continue
            rel = "relevant" if v.get("relevant") else "off_target"
            verdicts.append((int(v["id"]), rel, str(v.get("reason") or "")[:280]))

    off = 0
    if verdicts:
        with begin(engine) as conn:
            for rid, rel, note in verdicts:
                conn.execute(text(
                    "UPDATE frontend.hunt_results "
                    "SET relevance = :rel, relevance_note = :note WHERE id = :i"),
                    {"rel": rel, "note": note, "i": rid})
                off += rel == "off_target"
    return {"judged": len(verdicts), "off_target": off}
```

- [ ] **Step 4: Wire into `hunt.py` phase 5 + `load_round`**

`src/agf/db/hunts.py` `load_round` (L27): add `intent` to the SELECT list (`SELECT id, hunt_id, terms, competitor_apps, status, settings, intent FROM frontend.hunt_rounds WHERE id=:i`).

`src/agf/discovery/hunt.py` phase 5 (L258-282) — split the triage txn from the enqueue txn and judge between them (network outside any txn):

```python
        with begin(engine) as conn:
            counts = apply_triage_to_results(conn, round_id)
            stats.update(counts)
        progress.event("Judging relevance against the hunt intent")
        stats.update(judge_round(engine, round_id, getattr(round_row, "intent", None)))
        with begin(engine) as conn:
            kept_ids = [r.kept_advertiser_id for r in conn.execute(text(
                "SELECT DISTINCT kept_advertiser_id FROM frontend.hunt_results "
                "WHERE round_id = :r AND kept_advertiser_id IS NOT NULL "
                "AND COALESCE(relevance, 'relevant') <> 'off_target'"),
                {"r": round_id})]
            stats["enrich_enqueued"] = enqueue_enrich(conn, kept_ids)
```
(`round_row` is the row `load_round` returned at the top of `run_hunt_round`; use the variable name the file actually has. Import `judge_round` at module top.)

- [ ] **Step 5: Run tests + commit**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt_judge.py tests/discovery/test_hunt.py tests/db/test_hunts.py -q`
Expected: PASS (if `tests/discovery/test_hunt.py` asserts phase-5 behavior, its fakes see the judge skip on missing intent/key — no relevance writes, enqueue unchanged).

```bash
git add src/agf/discovery/hunt_judge.py src/agf/discovery/hunt.py src/agf/db/hunts.py tests/discovery/test_hunt_judge.py
git commit -m "feat(worker): hunt relevance judge — intent-scoped, fail-open, gates auto-enrich"
```

---

### Task 6: Round completion message

**Files:**
- Modify: `src/agf/db/hunts.py` (two new functions), `src/agf/discovery/hunt_one.py` (:58-60 success, `_fail` :79-82), `src/agf/discovery/hunt_daemon.py` (safety net :52-59)
- Test: `tests/discovery/test_hunt_judge.py` sibling — create `tests/discovery/test_hunt_messages.py`

**Interfaces:**
- Consumes: `frontend.hunt_messages` (155), counts columns incl. Task 3's `relevance`.
- Produces: `insert_round_message(conn, round_id: int, content: str) -> None`; `round_summary_counts(conn, round_id: int) -> dict` (`found`, `kept`, `off_target`); `compose_round_message(round_id: int, status: str, counts: dict, error: str | None = None) -> str` (pure, in `hunt_one.py` or `db/hunts.py` — put it in `db/hunts.py` so the daemon reuses it).

- [ ] **Step 1: Failing tests**

`tests/discovery/test_hunt_messages.py`:

```python
from sqlalchemy import text

from agf.db.hunts import compose_round_message, insert_round_message, round_summary_counts


def test_compose_round_message_copy():
    msg = compose_round_message(7, "done", {"found": 40, "kept": 12, "off_target": 3})
    assert msg == "Round 7 done: 12 kept of 40 found, 3 off target."
    msg = compose_round_message(7, "done", {"found": 40, "kept": 12, "off_target": 0})
    assert msg == "Round 7 done: 12 kept of 40 found."
    msg = compose_round_message(8, "error", {}, error="proxy pool exhausted")
    assert msg == "Round 8 failed: proxy pool exhausted."


def test_insert_round_message_lands_in_thread(pg_conn):
    hunt_id = pg_conn.execute(text(
        "INSERT INTO frontend.hunts (user_email, title) VALUES ('m@t.local', 'msg') RETURNING id"
    )).scalar_one()
    round_id = pg_conn.execute(text(
        "INSERT INTO frontend.hunt_rounds (hunt_id, terms) VALUES (:h, '[]') RETURNING id"
    ), {"h": hunt_id}).scalar_one()
    insert_round_message(pg_conn, round_id, "Round 1 done: 2 kept of 5 found.")
    row = pg_conn.execute(text(
        "SELECT role, content FROM frontend.hunt_messages WHERE hunt_id = :h"), {"h": hunt_id}).one()
    assert row.role == "assistant"
    assert row.content == "Round 1 done: 2 kept of 5 found."
```
(`pg_conn` rolls back — no cleanup needed.)

- [ ] **Step 2: Run to verify failures**

Run: `AGF_TEST_DB_URL=… python -m pytest tests/discovery/test_hunt_messages.py -q`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement**

In `src/agf/db/hunts.py`:

```python
def round_summary_counts(conn, round_id: int) -> dict:
    row = conn.execute(text(
        "SELECT count(*) AS found, "
        "count(*) FILTER (WHERE triage = 'kept') AS kept, "
        "count(*) FILTER (WHERE relevance = 'off_target') AS off_target "
        "FROM frontend.hunt_results WHERE round_id = :r"), {"r": round_id}).one()
    return {"found": row.found, "kept": row.kept, "off_target": row.off_target}


def compose_round_message(round_id: int, status: str, counts: dict, error=None) -> str:
    """Plain, honest chat copy for a finished round. No marketing voice."""
    if status != "done":
        detail = (error or "unknown error").strip()[:160]
        return f"Round {round_id} failed: {detail}."
    base = f"Round {round_id} done: {counts.get('kept', 0)} kept of {counts.get('found', 0)} found"
    off = counts.get("off_target", 0)
    return f"{base}, {off} off target." if off else f"{base}."


def insert_round_message(conn, round_id: int, content: str) -> None:
    """One assistant message into the round's hunt thread (non-fatal helper)."""
    conn.execute(text(
        "INSERT INTO frontend.hunt_messages (hunt_id, role, content) "
        "SELECT hunt_id, 'assistant', :c FROM frontend.hunt_rounds WHERE id = :r"),
        {"c": content, "r": round_id})
```

`src/agf/discovery/hunt_one.py` success block (L58-60) becomes:

```python
    with begin(engine) as conn:
        finish_round(conn, args.round_id, status="done")
        set_job_status(conn, args.job_id, "done")
        try:
            counts = round_summary_counts(conn, args.round_id)
            insert_round_message(conn, args.round_id,
                                 compose_round_message(args.round_id, "done", counts))
        except Exception:  # message is a convenience; the round row is the truth
            log.exception("round completion message failed")
```
`_fail` (L79-82) mirrors with `compose_round_message(args.round_id, "error", {}, error=<the error text>)`. The daemon safety net (`hunt_daemon.py` L52-59) adds the same insert with `error="worker died mid-run"` inside its existing conditional (wrapped in the same try/except).

- [ ] **Step 4: Run tests + commit**

Run: `AGF_TEST_DB_URL=… python -m pytest tests/discovery/test_hunt_messages.py tests/discovery/test_hunt_daemon.py -q`
Expected: PASS.

```bash
git add src/agf/db/hunts.py src/agf/discovery/hunt_one.py src/agf/discovery/hunt_daemon.py tests/discovery/test_hunt_messages.py
git commit -m "feat(worker): round completion message into the hunt thread"
```

---

### Task 7: Web — intent passthrough, card state, off-target grouping

**Files:**
- Modify: `web/src/lib/api.ts` (:249-373), `web/src/lib/hunt.ts` (+ its sibling test `web/src/lib/hunt.test.ts`), `web/src/components/hunt/HuntView.tsx`, `web/src/components/hunt/ProposalCard.tsx`, `web/src/components/hunt/RoundBlock.tsx`
- Test: `web/src/lib/hunt.test.ts` (`node --test`; components stay logic-free per repo convention)

**Interfaces:**
- Consumes: API from Tasks 3-4 (`card_state` on messages, `intent` on rounds, `relevance`/`relevance_note` on results, `intent` in proposal jsonb).
- Produces: `splitByRelevance(results: HuntResult[]): { onTarget: HuntResult[]; offTarget: HuntResult[] }` in `lib/hunt.ts`; `postHuntMessage(id, message, cardState?)`; `ProposalCard` gains `onChange?: (terms: string[], competitors: string[]) => void`.

- [ ] **Step 1: Failing lib test**

Append to `web/src/lib/hunt.test.ts`:

```ts
test("splitByRelevance keeps null/relevant on target, moves off_target", () => {
  const mk = (id: number, relevance: "relevant" | "off_target" | null) =>
    ({ id, relevance } as unknown as HuntResult);
  const { onTarget, offTarget } = splitByRelevance([mk(1, null), mk(2, "relevant"), mk(3, "off_target")]);
  assert.deepEqual(onTarget.map((r) => r.id), [1, 2]);
  assert.deepEqual(offTarget.map((r) => r.id), [3]);
});
```
(Match the file's existing import style for `test`/`assert` and types.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && node --test src/lib/hunt.test.ts`
Expected: FAIL (`splitByRelevance` not exported).

- [ ] **Step 3: Implement**

3a. `web/src/lib/api.ts`:
- `HuntProposal` gains `intent?: string`.
- `HuntResult` gains `relevance: "relevant" | "off_target" | null; relevance_note: string | null;`.
- `postHuntMessage`:

```ts
export type HuntCardState = { terms: string[]; competitor_apps: string[] };

export async function postHuntMessage(id: number, message: string, cardState?: HuntCardState): Promise<HuntChatTurn> {
  return live<HuntChatTurn>(`/hunts/${id}/messages`, {
    method: "POST",
    body: JSON.stringify(cardState ? { message, card_state: cardState } : { message }),
  });
}
```
- `postHuntRound` body type gains `intent?: string`.

3b. `web/src/lib/hunt.ts`:

```ts
export function splitByRelevance(results: HuntResult[]): { onTarget: HuntResult[]; offTarget: HuntResult[] } {
  const onTarget: HuntResult[] = [];
  const offTarget: HuntResult[] = [];
  for (const r of results) (r.relevance === "off_target" ? offTarget : onTarget).push(r);
  return { onTarget, offTarget };
}
```

3c. `ProposalCard.tsx`: add `onChange?: (terms: string[], competitorApps: string[]) => void` to props; call it whenever `terms` or `checked` change (a `useEffect` on `[terms, checked]` invoking `onChange?.(terms, proposal.competitor_apps.filter((a) => checked.has(a)))`).

3d. `HuntView.tsx`:
- Hold the live card state in a ref:

```ts
const cardStateRef = useRef<HuntCardState | null>(null);
```
- Wire `<ProposalCard … onChange={(terms, comps) => { cardStateRef.current = { terms, competitor_apps: comps }; }} />` and reset `cardStateRef.current = null` when `proposalMsgId` changes (effect on `[proposalMsgId]`).
- `send` (L187-216): `postHuntMessage(activeId, message, proposalMsgId != null && cardStateRef.current ? cardStateRef.current : undefined)`.
- `startRound` (L171-185): include `intent: m.proposal?.intent` in the `postHuntRound` body — the callsite at L322-328 already closes over `m`; pass intent the same way settings are passed.

3e. `RoundBlock.tsx`: split rows:

```ts
const { onTarget, offTarget } = splitByRelevance(results);
```
Render `onTarget` with the existing row mapping. Below it, when `offTarget.length > 0`:

```tsx
<details className="hunt-offtarget">
  <summary>Off target ({offTarget.length})</summary>
  {offTarget.map((r) => (
    /* same row markup as above, plus a muted note line: */
    <div key={r.id}>
      {/* existing row */}
      {r.relevance_note ? <p className="hunt-offtarget-note">{r.relevance_note}</p> : null}
    </div>
  ))}
</details>
```
Extract the existing row markup into a local `ResultRow` component so both groups reuse it (no duplicated JSX). Style with the file's existing class conventions; copy stays plain ("Off target (3)").

- [ ] **Step 4: Gates + commit**

Run: `cd web && npx tsc --noEmit && npx eslint src/lib/hunt.ts src/lib/api.ts src/components/hunt/ && node --test src/lib/hunt.test.ts`
Expected: all clean.

```bash
git add web/src/lib/api.ts web/src/lib/hunt.ts web/src/lib/hunt.test.ts web/src/components/hunt/HuntView.tsx web/src/components/hunt/ProposalCard.tsx web/src/components/hunt/RoundBlock.tsx
git commit -m "feat(web): hunt chat — intent passthrough, card state to Peon, off-target group"
```

---

### Task 8: Full-suite gates, deploy, live verification (controller-run, not a subagent)

**Files:** none new (deploy + verification).

- [ ] **Step 1: Zero-failed suites, LAST and SOLO**

Check nothing else runs against the test DB (`pgrep -fl pytest; pgrep -fl "go test"`), then bare, sequentially, each as its own command:

```bash
AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest -q
cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./... -count=1
cd web && npx tsc --noEmit && npx eslint . && node --test src/lib/
```
Expected: 0 failed everywhere (grep the pytest summary for "skipped" — DB tests must have RUN). Fix anything red before deploying.

- [ ] **Step 2: Migration + deploy**

- Apply migration 157 to prod (the repo's standard apply path — worker container runs `apply_migrations` on boot; otherwise apply per `ops/RUNBOOK.md` §Production).
- Deploy in order: **api** (GKE, per `DEPLOYMENT.md` — includes the genai bump + new envs), **web**, **worker** image (Cloud Build from a clean `git worktree add … HEAD`, then VM `docker compose pull/up`; compose files have drifted before — check BOTH repo and VM copies if env changes are needed).
- Env: `GEMINI_HUNT_MODEL` and `AGF_HUNT_WEB_TOOLS` default correctly — nothing to set unless overriding. Worker already has `GEMINI_API_KEY` (judge reuses it).
- Watch the API NEG 502 re-registration window after the GKE roll.

- [ ] **Step 3: Live gates (prod key)**

1. **Browse turn:** in /hunt ask Peon to research a niche ("what's a growing niche in sleep apps right now? propose a round for it"). Expect: tool trace shows "searched the web"; a proposal card with terms + intent; **no round row created** (`SELECT count(*) FROM frontend.hunt_rounds WHERE hunt_id = <id>` = 0).
2. **Start-now phrasing safety:** send "start scraping now with patience 6". Expect: a proposal (settings patience 6), zero rounds until the button is clicked.
3. **Refine turn:** edit a chip on the card, then ask "drop the first term and add one about bedtime routines". Expect: new proposal card that reflects both the chip edit and the ask.
4. **Full round:** click Start hunt. Expect: round runs autonomously; on finish an assistant message "Round N done: …" appears in the thread; results show the Off target group when the judge flagged any; `pipeline_jobs kind='enrich'` rows exist only for on-target advertisers.
5. **Post-round chat:** ask "why was <off-target app> off target?" — Peon answers from `get_round_results` (relevance + note present).

- [ ] **Step 4: Memory + wrap-up**

Update the hunt memory file with what shipped + landmines; run /self-eval; report results honestly (including anything deferred).

---

## Self-review notes (already applied)

- Spec coverage: model/SDK (T1), web tools + trace + budget (T2 — **the 90s `huntProposalTimeout` bump lands in T4's controller edit**; add `const huntProposalTimeout = 90 * time.Second` there), button-only starts (T2+T4), refinement + card state + cross-hunt memory (T2+T4+T7), completion message (T6), relevance judge + enrich gate + UI group (T3+T5+T7), env fallback (T1), live gates (T8).
- Type consistency: `HuntPlan.Intent` (T2) → `marshalHuntPlan` intent (T4) → `HuntProposal.intent` TS (T7) → `postHuntRound` intent → `startRoundBody.Intent` (T4) → `InsertHuntRound(…, intent)` (T3) → `hunt_rounds.intent` (T3) → `load_round` (T5) → `judge_round` (T5). `relevance`/`relevance_note` columns (T3) → Go readers (T3) → TS types (T7) → judge writes (T5) → counts (T6).
- Web fetch timeout: check `live()` in `web/src/lib/api.ts` for an AbortController/timeout shorter than 90s during T7; if one exists, raise it for the two hunt POSTs.
