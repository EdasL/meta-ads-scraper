# Peon Agent Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Peon becomes a Gemini function-calling agent: explains what rounds did from real telemetry, sets per-round scrape settings, and starts rounds on explicit founder command.

**Architecture:** Migration 156 adds `hunt_rounds.settings` + `hunt_messages.tool_log`. The executor honors per-round settings and records per-term stop telemetry into `progress.terms`. The Go API gains a bounded agent loop (`GenerateHuntTurn`, genai v1.47.0 function calling, max 6 iterations) over a per-request repo-backed tool executor; `start_round`/`propose_round` are terminal tools. Web renders the tool trace + settings.

**Tech Stack:** Same as Hunt v1. genai function-calling names verified against the installed SDK (v1.47.0): `genai.FunctionDeclaration`, `genai.Tool{FunctionDeclarations}`, `resp.FunctionCalls() []*genai.FunctionCall` (`.Name`, `.Args map[string]any`), `genai.NewPartFromFunctionResponse(name, map[string]any)`, `genai.NewContentFromParts(parts, genai.RoleUser|genai.RoleModel)`.

**Spec:** `docs/superpowers/specs/2026-07-13-peon-agent-tools-design.md`

## Global Constraints

- AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it (Python + Go repo tests; Go TestMain accepts it since 8579d63). Go integration: AGF_IT_DB_URL same value. A skip is not a pass. **Suites against agf_it run ONE consumer at a time; the definitive gate run is solo and last.**
- Migration 156: no bare percent-sign anywhere incl. comments.
- Settings bounds (both ends): page_cap 1–100 default 40, patience 1–10 default 3, listing_pages 1–30 default 10.
- start_round fires ONLY on explicit founder command (system-instruction rule; live-gate verified).
- Copy plain; "Peon" in UI strings/prose only. US-only. Explicit git paths. TDD.
- Sequential implementers; reviewer+next-implementer concurrency OK.

---

### Task 1: Migration 156 + settings-aware executor + per-term telemetry (Python)

**Files:** Create `db/schema/156_hunt_agent.sql`; Modify `src/agf/db/hunts.py` (load_round selects settings; new `DEFAULT_HUNT_SETTINGS`/`clamp_hunt_settings`), `src/agf/discovery/hunt.py`; Test: extend `tests/db/test_hunts.py`, `tests/discovery/test_hunt.py`.

**Migration:**
```sql
-- Peon agent tools (spec 2026-07-13-peon-agent-tools): per-round scrape
-- settings + assistant tool trace.
ALTER TABLE frontend.hunt_rounds
    ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL DEFAULT '{}';
ALTER TABLE frontend.hunt_messages
    ADD COLUMN IF NOT EXISTS tool_log JSONB;
```

**hunts.py additions:**
```python
DEFAULT_HUNT_SETTINGS = {"page_cap": 40, "patience": 3, "listing_pages": 10}
_SETTINGS_BOUNDS = {"page_cap": (1, 100), "patience": (1, 10), "listing_pages": (1, 30)}


def clamp_hunt_settings(raw: dict | None) -> dict:
    """Effective per-round knobs: defaults overlaid with bounded stored values."""
    out = dict(DEFAULT_HUNT_SETTINGS)
    for key, (lo, hi) in _SETTINGS_BOUNDS.items():
        val = (raw or {}).get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = max(lo, min(hi, int(val)))
    return out
```
`load_round` SELECT gains `settings`.

**hunt.py:** resolve `knobs = clamp_hunt_settings(rnd.settings)` after load; term loop uses `knobs["patience"]`/`knobs["page_cap"]`, phase 0 uses `knobs["listing_pages"]` (constants stay as defaults only). Per-term telemetry: `_sweep_term` returns a stop reason (`"exhausted"` when not has_next/cursor, `"dry"` on patience, `"cap"` on page_cap, `"error"` on ExtractError) and the lane records `progress.term_stats(term, pages, found, new, stop)` — add to `_Progress`:
```python
    def term_stats(self, term: str, pages: int, found: int, new: int, stop: str) -> None:
        with self._lock:
            self._state.setdefault("terms", {})[term] = {
                "pages": pages, "found": found, "new": new, "stop": stop}
            self._flush()
```
(`_state` init gains `"terms": {}`; per-term found/new need per-term counters in `_sweep_term` — count locally alongside the shared stats.) Competitor pulls record `term_stats(f"page:{name}", 1, found, new, "listing")`.

**Tests (all RED first):** settings row `{"page_cap": 2, "patience": 1}` limits a 5-page scripted term to 2 pages (`stop == "cap"`); out-of-bounds `{"page_cap": 999}` clamps to 100 (assert via `clamp_hunt_settings` unit test + executor accepts); patience stop records `stop == "dry"`; scripted exhaustion records `"exhausted"`; error term records `"error"`; `progress["terms"]` shape asserted in the existing happy-path test; clamp unit tests incl. bool/None/str rejection. Run: hunt + hunts + placeholder-lint suites.

Commit: `db+worker: per-round hunt settings honored + per-term stop telemetry (migration 156)`

---

### Task 2: Go repo — settings/tool_log plumbing + report/results queries

**Files:** Modify `backend/repositories/hunt_repo.go`, `backend/models/hunt.go`, existing callers/tests; Test: extend `backend/repositories/hunt_repo_test.go`.

- `InsertHuntRound(ctx, g, huntID int, messageID *int, terms, competitors, settings json.RawMessage) (int, error)` — settings column via `jsonbArgDefault` (`{}` default: adjust helper call to default `[]byte("{}")` for this column — add `jsonbArgDefaultObj` or parameterize). Update ALL existing callers (controller, tests) — grep them.
- `InsertHuntMessage(..., toolLog json.RawMessage)` — nullable via `jsonbArg`. Update callers.
- `ListHuntMessages` returns `tool_log`; `ListHuntRounds` returns `settings` (models: `HuntMessageRow.ToolLog json.RawMessage`, `HuntRoundRow.Settings json.RawMessage`).
- New `RoundReport(ctx, g, huntID, roundID int) (models.HuntRoundReport, error)`: one query joining hunt_rounds (scoped `WHERE r.id = ? AND r.hunt_id = ?`) returning status, error_text, started/finished, settings, progress (the digest is assembled Go-side from progress: terms map, found/new counters) + triage counts from hunt_results grouped. Returns `sql.ErrNoRows`-equivalent (nil, nil? use a found bool) for foreign/missing rounds — the tool executor turns that into an error string.
- New `RoundResults(ctx, g, huntID, roundID int, triage string, limit int) ([]models.HuntResultRow, error)` — existing ListHuntResults SQL + `AND hr.round_id = ?` + optional `AND hr.triage = ?`, `LIMIT`.
- Tests: settings roundtrip on InsertHuntRound (stored `{"page_cap":30}` read back via ListHuntRounds); tool_log roundtrip; RoundReport foreign-hunt returns not-found; RoundResults triage filter + cap.

Commit: `api(repo): hunt settings/tool_log columns + round report/results queries`

---

### Task 3: Go Gemini agent loop

**Files:** Modify `backend/helpers/gemini.go`; Test: `backend/helpers/gemini_hunt_test.go` (pure parts only).

- `type HuntSettings struct { PageCap, Patience, ListingPages *int }` + `func ClampHuntSettings(s HuntSettings) map[string]int` (only present keys, bounded; empty map when none) + `func settingsSchema() *genai.Schema` (three optional integer props with min/max in Description).
- `type HuntToolExecutor interface { GetRoundReport(roundID int) (string, error); GetRoundResults(roundID int, triage string) (string, error) }`
- `type HuntTurn struct { Reply string; Proposal *HuntPlan; Start *HuntPlan; ToolLog []string }`, `type HuntPlan struct { Terms []string; CompetitorApps []string; Settings map[string]int }`.
- `func (h *GeminiHelper) GenerateHuntTurn(ctx context.Context, in HuntTurnInput, tools HuntToolExecutor) (HuntTurn, error)` — loop skeleton (adapt/complete; names verified against genai v1.47.0):

```go
decls := []*genai.FunctionDeclaration{
    {Name: "get_round_report", Description: "Telemetry for one prior round: settings used, per-term pages/found/new and why each term stopped, triage counts.", Parameters: &genai.Schema{Type: genai.TypeObject, Properties: map[string]*genai.Schema{"round_id": {Type: genai.TypeInteger}}, Required: []string{"round_id"}}},
    {Name: "get_round_results", Description: "Result rows for one prior round; optional triage filter kept|vetoed|pending.", Parameters: &genai.Schema{...round_id required, triage optional string...}},
    {Name: "propose_round", Description: "Suggest a new round for the founder to approve with the Start hunt button. Use unless the founder explicitly told you to run.", Parameters: planSchema()},
    {Name: "start_round", Description: "Start a round NOW. Only when the founder's latest message explicitly says to run/start/go.", Parameters: planSchema()},
}
config := &genai.GenerateContentConfig{
    SystemInstruction: &genai.Content{Parts: []*genai.Part{{Text: huntAgentInstruction}}},
    Tools:             []*genai.Tool{{FunctionDeclarations: decls}},
    MaxOutputTokens:   2048,
}
contents := buildHuntTurnContents(in) // history as RoleUser/RoleModel turns + latest message
var toolLog []string
for iter := 0; iter < 6; iter++ {
    resp, err := h.client.Models.GenerateContent(ctx, h.model, contents, config)
    if err != nil { return HuntTurn{}, err }            // retry/backoff as GenerateExplainer
    calls := resp.FunctionCalls()
    if len(calls) == 0 {
        return HuntTurn{Reply: firstText(resp), ToolLog: toolLog}, nil
    }
    // append the model's own content, then one function response per call
    contents = append(contents, resp.Candidates[0].Content)
    var responseParts []*genai.Part
    for _, call := range calls {
        switch call.Name {
        case "propose_round", "start_round":
            plan := planFromArgs(call.Args) // terms/competitors cleaned, settings clamped
            turn := HuntTurn{Reply: firstText(resp), ToolLog: toolLog}
            if call.Name == "start_round" {
                turn.ToolLog = append(turn.ToolLog, describePlan("started a round", plan))
                turn.Start = &plan
            } else {
                turn.ToolLog = append(turn.ToolLog, describePlan("proposed a round", plan))
                turn.Proposal = &plan
            }
            return turn, nil                             // terminal tools end the turn
        case "get_round_report":
            id := intArg(call.Args, "round_id")
            out, err := tools.GetRoundReport(id)
            if err != nil { out = "error: " + err.Error() }
            toolLog = append(toolLog, fmt.Sprintf("checked round %d's report", id))
            responseParts = append(responseParts, genai.NewPartFromFunctionResponse(call.Name, map[string]any{"report": out}))
        case "get_round_results":
            ...same shape, log "looked at round N's results"...
        default:
            responseParts = append(responseParts, genai.NewPartFromFunctionResponse(call.Name, map[string]any{"error": "unknown tool"}))
        }
    }
    contents = append(contents, genai.NewContentFromParts(responseParts, genai.RoleUser))
}
return HuntTurn{Reply: "Peon could not finish that request. Try rephrasing.", ToolLog: toolLog}, nil
```
- `huntAgentInstruction` per the spec's system-instruction paragraph (verbatim rules: explicit-command gate for start_round, generic terms, caps, 1–3 plain sentences, use report/results tools before answering "what happened" questions).
- Keep `GenerateHuntProposal` DELETED only if nothing else references it — otherwise leave and mark deprecated; the controller switches to GenerateHuntTurn (Task 4 removes the old call site; delete the old func + its prompt builder in THIS task only if the build stays green, else in Task 4).
- Tests (no live API): `ClampHuntSettings` table test; `planFromArgs` (args map → cleaned plan; floats from JSON become ints; junk terms dropped); `buildHuntTurnContents` ordering; `describePlan` copy ("started a round — 3 terms · depth 30 · patience 6").

Commit: `api(gemini): Peon agent loop — function-calling with report/results tools + terminal propose/start`

---

### Task 4: Controller wiring + integration tests

**Files:** Modify `backend/controllers/hunt_controller.go`, `backend/routers/routers.go` (only if constructor signature changes — it shouldn't); Test: extend `backend/internal/integration/hunt_test.go`.

- Per-request executor `huntTools{g *gorm.DB, huntID int, ctx context.Context}` implementing `helpers.HuntToolExecutor` via Task 2's repo funcs; report/results marshalled to compact JSON strings; foreign round → `("", errors.New("no such round in this hunt"))`.
- `Create`/`Message`: build `HuntTurnInput` (history from ListHuntMessages incl. whether a proposal/tool_log existed → plain text turns; TriedTerms/RoundSummaries as today + settings/stop info), call `GenerateHuntTurn`. Persist assistant message (content=Reply, proposal jsonb when `Proposal != nil` — shape `{terms, competitor_apps, settings}` — tool_log jsonb when non-empty). When `Start != nil`: clamp again, `InsertHuntRound` (message_id = the new assistant message id), include the created round in the JSON response (`{messages: [...], round: {...}}`).
- `StartRound` handler: body gains optional `settings` (`helpers.HuntSettings` shape) → clamped map → marshalled into InsertHuntRound. 422 copy unchanged.
- nil-gemini 503 + gemini-error 502 behavior unchanged.
- Integration tests (stub-free, non-Gemini paths per v1 policy): rounds POST with `settings {"page_cap": 30, "patience": 6}` → 202 + stored settings read back via GET (rounds carry settings + a seeded assistant message carries tool_log); out-of-bounds settings clamped in the stored row (page_cap 999 → 100); missing settings → `{}` stored, GET returns it.

Commit: `api: Peon chat runs the agent loop; chat- and button-started rounds share clamped settings`

---

### Task 5: Web — tool trace, settings display, passthrough

**Files:** Modify `web/src/lib/api.ts` (HuntMessage.tool_log, HuntRound.settings, HuntChatTurn may carry round, postHuntRound settings param), `web/src/lib/hunt.ts` (+`formatSettings(settings): string` — non-default keys only, "depth 40 · patience 8 · listing 20"; empty string when default/empty), `web/src/lib/hunt.test.ts`, `web/src/components/hunt/HuntView.tsx` (chat-turn response may include a round → merge into thread state + poll), `web/src/components/hunt/ProposalCard.tsx` (render proposal.settings read-only; pass through on Start hunt), `web/src/components/hunt/RoundBlock.tsx` (status line appends formatSettings(round.settings)), Peon message renders tool_log as a muted mono line joined with " · ".
- node:test cases for formatSettings (defaults hidden, bounds text, empty). tsc/lint/build + cookieless /hunt 307 check.

Commit: `web: Peon tool trace, per-round scrape settings shown and passed through`

---

### Task 6 (controller-run): final review, suites, deploy, live gate

Whole-branch review (opus) over the increment's commits; consolidated fixer if needed. Suites SOLO and LAST: Python full, Go full (build/vet/all incl. integration), web gates. Deploy: migration 156 → worker image (clean worktree Cloud Build) → VM compose pull/up (no compose edits needed — same services) → GKE api+web (:tag = HEAD short). Live gate per the spec: (1) "what exactly did you do in the last round and why did each term stop?" → grounded reply + tool trace rendered; (2) "run those same terms again but 30 pages deep with patience 6" → Peon-started round, DB settings row, executor honors (progress.terms stop/pages reflect cap 30); (3) button path with settings passthrough; (4) founder's existing hunts unaffected. Memory + ledger updates.

## Self-review notes (done)
- Spec coverage: migration/telemetry (T1), repo (T2), agent loop + instruction gate (T3), controller + both start paths (T4), UI trace/settings (T5), live verify incl. explicit-command behavior (T6).
- Type consistency: settings flows as `map[string]int` (Go) / jsonb `{page_cap?, patience?, listing_pages?}` (DB) / `Record<string, number>`-ish optional fields (web types may use `{page_cap?: number; patience?: number; listing_pages?: number}`); round-report digest is a STRING to the model (compact JSON).
- Placeholders: the two `...` in Task 3's skeleton are schema/log-string ellipses whose full content is specified in the surrounding text (schema fields named; log copy given); implementer completes them per those words.
