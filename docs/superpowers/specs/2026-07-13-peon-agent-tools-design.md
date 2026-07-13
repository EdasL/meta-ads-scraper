# Peon agent tools — chat that explains, configures, and starts hunts

**Date:** 2026-07-13
**Status:** Approved (founder: "ye")
**Builds on:** 2026-07-13-hunt-ondemand-discovery-design.md (SHIPPED)

## What this is

Peon (the Hunt chat) becomes a Gemini **function-calling agent**. Today every
message returns a term proposal; Peon cannot say what a round actually did and
cannot change scrape behavior. After this increment the founder can ask "what
did you do exactly / why did it stop at page 3?" and get answers grounded in
round telemetry, tell Peon to scrape differently ("40 pages deep, don't give
up so fast"), and tell it to run — Peon starts the round itself.

Founder-locked decisions:
- **Full agent with tools** (not reply-only, not settings-only).
- **Peon starts rounds on explicit command** — the founder's message is the
  approval. The proposal card + Start hunt button remains for hand-picking.
- Peon never starts a round unprompted; settings are hard-bounded server-side
  so chat cannot burn unbounded proxy budget. US-only stands.

## Data (migration `db/schema/156_hunt_agent.sql` — 156 verified free)

- `ALTER TABLE frontend.hunt_rounds ADD COLUMN settings JSONB NOT NULL DEFAULT '{}'`
  — keys (all optional): `page_cap`, `patience`, `listing_pages`.
- `ALTER TABLE frontend.hunt_messages ADD COLUMN tool_log JSONB` — nullable;
  array of short strings describing tool calls the assistant made, rendered
  verbatim in the UI (e.g. `["checked round 2's report", "started round 3 — depth 40"]`).
- No bare `pct`-sign anywhere in the file, including comments.

**Settings bounds (one source of truth, enforced at BOTH ends):**
`page_cap` 1–100 (default 40 = HUNT_PAGE_CAP), `patience` 1–10 (default 3),
`listing_pages` 1–30 (default 10). Go clamps at tool-arg/HTTP validation;
Python clamps again in the executor (defense against hand-inserted rows).

## Worker (src/agf/discovery/hunt.py)

- `load_round` (src/agf/db/hunts.py) also selects `settings`.
- `run_hunt_round` resolves effective knobs per round:
  `page_cap = clamp(settings.get("page_cap", HUNT_PAGE_CAP), 1, 100)` etc.,
  used by the term-lane loop (`depth >= page_cap`, `consec_dry >= patience`)
  and phase 0 (`search_listing(max_pages=listing_pages)`).
- **Per-term telemetry** for the report tool: at each term's end the executor
  writes `progress["terms"][term] = {"pages": N, "found": N, "new": N,
  "stop": "exhausted"|"dry"|"cap"|"error"}` ("exhausted" = Meta returned no
  next page; "dry" = patience early-exit; "cap" = hit page_cap; "error" =
  search failed). Competitor pulls record `progress["terms"]["page:<name>"] =
  {"pages": n, "found": N, "new": N, "stop": "listing"}`. Events unchanged.

## Go API

### Gemini agent loop (backend/helpers/gemini.go)

`GenerateHuntTurn(ctx, in HuntTurnInput, tools HuntToolExecutor) (HuntTurn, error)`:
genai function-calling loop, max 6 tool iterations, 30s ctx (existing budget).

- `HuntTurnInput`: `{Message string; History []HuntTurn2 /*role+content*/;
  TriedTerms []string; RoundSummaries []string}` (superset of today's input).
- `HuntToolExecutor` interface (implemented in the controller, repo-backed):
  - `GetRoundReport(roundID int) (string, error)` — compact JSON digest:
    settings used, per-term telemetry (pages/found/new/stop), triage counts,
    started/finished/status/error_text.
  - `GetRoundResults(roundID int, triage string) (string, error)` — rows
    (page_name, store, source_term, was_new, triage, gem_score), triage
    filter optional ("" = all), capped 50 rows.
  - Both must verify the round belongs to the CURRENT hunt (ownership is
    already established by the HTTP layer; the executor is constructed
    per-request with the hunt id and rejects foreign round ids with an error
    string the model sees).
- Terminal function declarations (no Go-side execution inside the loop):
  - `propose_round(terms []string, competitor_apps []string, settings {page_cap, patience, listing_pages})`
  - `start_round(terms []string, competitor_apps []string, settings {...})`
  When the model calls either, the loop ENDS and `HuntTurn` carries it.
- `HuntTurn` result: `{Reply string; Proposal *HuntProposalV2; Start *HuntRoundSpec;
  ToolLog []string}` where `HuntProposalV2 = {Terms, CompetitorApps, Settings}`
  and `HuntRoundSpec` is the same shape. ToolLog entries are written by the
  loop itself, plain sentences.
- System instruction (replaces huntSystemInstruction): Peon is Field Desk's
  discovery agent; generic niche terms only, brands → competitor_apps; use
  get_round_report/get_round_results before answering questions about what
  happened; call `start_round` ONLY when the founder's LATEST message
  explicitly tells you to run/start/go; otherwise use `propose_round`; at
  most 15 terms, 3 competitors; settings within bounds; reply text is 1–3
  plain sentences. Peon-the-name appears only in prose.
- Term/settings post-processing reuses `cleanHuntTerms`/caps + a
  `clampSettings` helper shared with the controller.

### Controller (backend/controllers/hunt_controller.go)

- `Create`/`Message` call `GenerateHuntTurn` with a per-request tool executor
  (holds hunt id + gorm handle). Behavior:
  - Persist user message first (unchanged). On Gemini error: 502, message kept.
  - Reply with proposal → assistant message `proposal` jsonb now includes
    `settings`; `tool_log` jsonb persisted.
  - Reply with `Start` → controller validates/clamps, `InsertHuntRound(...,
    settings)` (round + pipeline job in one txn as today), assistant message
    records tool_log incl. "started round N"; response includes the new round
    so the UI renders it immediately.
- `StartRound` HTTP handler (button path) accepts optional `settings` from
  the proposal card and passes them through (clamped) — card and chat paths
  produce identical rounds.
- `HuntRoundSummaries` fed to the model gains settings + stop info so
  follow-up proposals are informed without a tool call.

### Repo (backend/repositories/hunt_repo.go)

- `InsertHuntRound(..., settings json.RawMessage)` — additional CAST column.
- `RoundReport(ctx, g, huntID, roundID)` / `RoundResults(ctx, g, huntID,
  roundID, triage)` — the tool executor's queries (scoped by hunt id via JOIN).
- `ListHuntMessages` returns `tool_log`; `ListHuntRounds` returns `settings`.

## Web

- `HuntMessage` gains `tool_log: string[] | null` — rendered under the Peon
  reply as a muted mono trace line ("checked round 2's report · started round
  3 — depth 40").
- `HuntRound` gains `settings`; RoundBlock's status line appends non-default
  settings plainly ("depth 40 · patience 8") — real stored values only.
- ProposalCard shows the proposal's settings as read-only text (editing
  happens through chat, per the founder's flow); Start hunt posts them through.
- A Peon-started round appears via the existing response-refresh path
  (Message response now may include a round).

## Testing

- Python: settings respected (cap/patience/listing from a seeded settings
  row; clamped when out of bounds), per-term telemetry shape + stop reasons
  ("dry", "exhausted", "cap", "error") via scripted FakeExtractor pages.
- Go: `clampSettings` table test; tool-executor unit tests against the repo
  (report digest shape, foreign-round rejection, results filter/cap);
  integration tests for the button path with settings passthrough + rounds
  listing settings/tool_log roundtrip. The live Gemini loop is covered by the
  live gate only (same policy as v1) — file comment says so.
- Web: hunt.ts helpers extended (settings formatting) with node:test cases.

## Live verify (the gate)

On prod, founder-account hunt via the credentials-provider recipe: (1) ask
Peon "what exactly did you do in the last round and why did each term stop?"
→ grounded answer + visible tool trace; (2) "run those same terms again but
30 pages deep with patience 6" → Peon starts the round itself, DB shows
`settings {"page_cap":30,"patience":6}`, executor telemetry shows terms
honoring them; (3) button path still works with settings passthrough. Zero
failed suites (solo, last), GKE + worker rolled.

## Non-goals

- No Meta access from the API pod (competitor page resolution stays
  worker-side; Peon cannot browse).
- No autonomous rounds (schedules, retries) — founder's message is the only
  trigger.
- No per-term budget accounting UI (watch first).
