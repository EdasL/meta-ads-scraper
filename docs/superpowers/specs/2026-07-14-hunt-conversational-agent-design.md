# Hunt conversational agent — design

2026-07-14. Follow-up to `2026-07-13-peon-agent-tools-design.md` (Peon v2).

## Problem

Three founder complaints about the /hunt Peon chat:

1. **Peon can start a scrape from a misread message.** Asking "what is patience?"
   triggered a round with patience 6. The only gate on the `start_round` tool is a
   prompt sentence ("ONLY when the founder's latest message explicitly says
   run/start/go") — `runTurn` (`backend/controllers/hunt_controller.go:312`)
   inserts the round row directly when the model calls it. A prompt rule alone
   cannot hold a tool closed.
2. **The chat is "do and gone", not a conversation.** Each turn is transactional:
   propose → button → round runs → the block flips to Done. Peon can't refine a
   pending proposal in dialogue, has no memory beyond the current hunt's rounds,
   and says nothing when a round finishes.
3. **No web capability.** Peon can't research a niche, read a page the founder
   pastes, or look at a store listing. And keyword search in the Ad Library
   returns ads that match the term but not the founder's actual ask — nothing
   judges relevance to the request.

## Verified facts (live probes, 2026-07-14)

- `gemini-2.5-flash` `generateContent` **rejects** combining built-in tools with
  function declarations: HTTP 400 "Built-in tools ({google_search}) and Function
  Calling cannot be combined".
- `gemini-3.5-flash` on the **prod** key accepts `google_search` + `url_context`
  + our function declarations in one request when
  `tool_config.include_server_side_tool_invocations: true` is set. The response
  interleaves server-side `toolCall`/`toolResponse` parts (the browsing) and
  ends with our `functionCall` — verified: it searched, then called
  `propose_round` with terms derived from search results.
- The **local dev key** has no search-grounding quota (429 even on search-only)
  — browsing works only on the billed prod key.
- Go SDK: `google.golang.org/genai` v1.47.0 (current) lacks
  `IncludeServerSideToolInvocations`; **v1.58.0 has it** (already in module
  cache). Bump required.
- Grounded search is billed per request. Founder-chat volume → negligible, but
  it is prod-key-only spend.

## Design

### 1. Model + SDK

- Bump `backend/go.mod`: `google.golang.org/genai` v1.47.0 → v1.58.0. Re-run the
  existing Gemini-touching Go tests; the explainer and proposal call sites keep
  their current shape.
- New env `GEMINI_HUNT_MODEL`, default `gemini-3.5-flash`, used **only** by
  `GenerateHuntTurn`. Every other call site stays on `GEMINI_MODEL` (default
  `gemini-2.5-flash`) — no behavior change outside the hunt chat.

### 2. Web tools on the hunt turn

- `GenerateHuntTurn` config gains `google_search` + `url_context` built-in tools
  plus `ToolConfig.IncludeServerSideToolInvocations = true` (exact struct
  placement pinned at plan time from genai v1.58.0 `types.go`).
- Env flag `AGF_HUNT_WEB_TOOLS` (default on; `0`/`false` disables) drops the two
  built-in tools for keys without grounding quota (local dev). Peon still chats
  and uses function tools with the flag off.
- Server-side browsing surfaces in the existing tool trace: when response parts
  carry server-side tool invocations, append plain `tool_log` lines —
  "searched the web", "read a page". No new UI; the muted trace line under the
  bubble already renders `tool_log`.
- Turn budget `huntProposalTimeout` 30s → 90s (browsing turns are slower).
  `maxIterations` stays 6 — browsing happens inside a single model call, it does
  not consume loop iterations.
- The loop already appends the model's own content (including
  `thoughtSignature` parts) back into `contents` before tool responses; keep
  that intact — Gemini 3 models require thought signatures round-tripped within
  a turn.

### 3. Scrapes start only from the button

- **Delete the `start_round` tool** and its paths: `buildStartTurn`, the
  `turn.Start` branch in `runTurn`, the start-side of `PlanIsRunnable` usage.
  `HuntTurn.Start` field goes away.
- `propose_round` gains an optional boolean arg `allow_tried`: when the founder
  asks to re-run previous terms, the model sets it and the tried-term strip is
  skipped (this preserves the `bab9f57` re-run fix — Peon must not refuse
  re-runs; it re-proposes them and the founder clicks Start).
- `propose_round` also gains a required `intent` string: one plain sentence
  describing what the founder is hunting (e.g. "sleep apps aimed at new
  parents"), derived from the conversation. Stored with the proposal and passed
  through to the round (see §5).
- Round rows are created **only** by `POST /api/hunts/:hunt_id/rounds` (the
  Start hunt button). A chat turn can no longer insert a round. The agent
  instruction is rewritten accordingly (Peon never claims it started anything).

### 4. Conversation

- **Refine the pending proposal.** `buildHuntTurnContents` injects the latest
  pending proposal (terms, competitors, settings, intent) into the context
  suffix: "Current proposal awaiting approval: …". Instruction: when a proposal
  is pending, `propose_round` calls are refinements — carry forward what the
  founder didn't ask to change. The existing UI already swaps the card to the
  newest proposal message (`proposalMsgId`, `HuntView.tsx:126`), so refinement
  needs no new card mechanics.
- **Card edits reach Peon.** When a pending proposal card exists, the web app
  sends the card's current edited state (`card_state: {terms, competitor_apps}`)
  with `POST …/messages`; the controller injects it as "The proposal card
  currently shows: …". Without this, Peon refines the stored proposal while the
  founder looks at edited chips.
- **Cross-hunt memory.** New repo query summarizing all hunts: per-round lines
  (terms → found/kept, status), newest first, capped at 50 rounds; plus up to 5
  top kept results per hunt (page name + gem score). Injected as a "Prior
  hunts:" block alongside the existing current-hunt summaries and tried terms.
  Peon can answer "did we ever try meditation terms?" and stops re-proposing
  what failed elsewhere.
- **Round completion message.** When the Python daemon finishes a round
  (`finish_round`, `src/agf/db/hunts.py:41`), it also inserts one assistant
  `hunt_messages` row — templated, no LLM call, plain voice:
  - done: "Round N done: X kept from Y found. Strongest: A, B."
  - error: "Round N failed: <short reason>."
  The existing 2s poll surfaces it. Follow-ups ("show the vetoed ones", "go
  deeper on term 2") run through the existing `get_round_report` /
  `get_round_results` tools or a new proposal. The scrape itself never waits
  for input.

### 5. Relevance judging

Ad Library keyword results can match the term but not the ask. Each round now
carries the founder's intent, and results are judged against it.

- **Schema (migration 157):** `hunt_rounds.intent text` (nullable);
  `hunt_results.relevance text` (null | `relevant` | `off_target`) and
  `hunt_results.relevance_note text`. (Migration rule: no bare `%` anywhere,
  including comments.)
- **Intent flow:** `propose_round` emits it → proposal jsonb → the web sends it
  with `POST …/rounds` → stored on the round. Rounds created without an intent
  (legacy/manual) skip judging entirely.
- **Judge (Python, inside the hunt round runner):** after triage keeps a
  result, batch-judge kept results with Gemini (existing Python client), ~20
  per call, structured output: intent + page name + store listing snippet + one
  ad text → `relevant`/`off_target` + one-line reason. Judge calls run outside
  any DB transaction; batched writes after.
- **Fail open:** judge error or timeout leaves `relevance` null, treated as
  relevant everywhere. An LLM outage must not hide results.
- **Effects:** off-target results are skipped by the round's auto-enrich
  (budget goes to on-target results); the UI moves them into a collapsed
  "Off target (N)" group at the bottom of the round block, each with its
  reason. Rows are never deleted — the verdict is metadata the founder can
  overrule with a click, matching the standing rule that heuristics don't
  filter found data out of view.
- Peon can explain verdicts afterwards: `get_round_results` output includes
  relevance + note.

### 6. Instruction rewrite

The system instruction is rewritten for the new shape: conversational tone
(still 1–3 plain sentences per reply, professional, no fluff), browse the web
when it helps (niche research, pasted URLs, store pages), refine the pending
proposal instead of starting over, explain settings (patience, page_cap,
listing_pages) when asked, never claim to have started a round — only the
founder's button starts scrapes. Terms stay generic niche queries; brands go to
competitor_apps; caps unchanged (15 terms, 3 competitors).

## Error handling

- Gemini turn failure after retries → existing 502 path, user message intact
  (unchanged).
- Built-in-tool quota errors (429 on grounding) surface as the same 502; with
  `AGF_HUNT_WEB_TOOLS=0` the turn runs without browsing.
- 90s turn budget: the web `PeonWorking` indicator already covers long turns;
  the web fetch timeout must be ≥ the API budget (check the client's fetch has
  no shorter timeout).
- Judge failures: fail open (null relevance), logged, never crash the round.
- Completion-message insert failure: logged, non-fatal — the round result is
  the source of truth, the message is a convenience.

## Testing

- Go: loop tests updated — `propose_round` with `allow_tried`/`intent`,
  server-side tool parts → `tool_log` lines, **no code path from a chat turn to
  a round insert** (regression test), context builder includes pending
  proposal/card state/cross-hunt block; controller tests for the removed start
  path and `card_state` injection.
- Python: judge unit tests with a fake Gemini (batching, fail-open, no-intent
  skip); daemon test for the completion message row; migration applies to a
  fresh DB.
- Full suites zero-failed, run last and solo
  (`AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`).
- Live gates (prod key): (1) a chat turn that browses (tool_log shows it) and
  produces a proposal with intent; (2) a refine turn against a pending
  proposal; (3) a full round from Start click → completion message + off-target
  grouping visible; (4) probe that a "start now"-style chat message yields a
  proposal, not a round row.

## Out of scope

- Taste/ratings-based memory (founder like-profile in Peon's context) — later.
- Streaming replies; Interactions API migration; changing other Gemini call
  sites' model.
- Any mid-scrape agent interaction — rounds run autonomously end to end.
