# Hunt chat UX redesign — post-round Peon report, results grid, deep paging

**Date:** 2026-07-15
**Status:** Approved by founder (design conversation 2026-07-15)

## Problem

Hunts are hard to follow as a conversation. After scraping, the founder gets a canned
one-line count ("Round 3 done: 5 kept of 21 found") with no app names, no judgment, and
no next-step proposal — the Peon only reflects on results if the founder writes another
message himself. Results render as a plain text row list with no icons, no descriptions,
and no way to save an app. An empty assistant bubble sometimes renders before/around
scraping. And measured hunt history shows hunts almost never read past SERP page 5:
pages full of repeat incumbents (Pocket FM ×5 pages, DramaBox, Audio Adventures) count
as "dry" and trip the patience early-exit at page 3 — right before the depth where
net-new apps appear (the one patience-6 round found its only net-new on pages 7–9;
corpus-wide, ~33% of net-new advertisers were first seen past page 5).

## Measured evidence (prod probe, 2026-07-15)

- 6 hunts, 18 rounds, 210 results; 206 results from depth 1–5, zero past page 10.
- Nearly every term stops at `pages=3, stop='dry'` — repeat-incumbent pages feed the
  dry counter.
- Rounds 17/18 (patience 8, cap 50–60) produced the most keeps and net-new of any rounds.
- Hyper-specific long-tail terms are ~0-yield ("womens marathon training", "couch to 5k
  female" = 0); broad head terms carry everything ("pregnancy running app" 13).
- Hunts already produced gems: Health & Wellness Digest 80.8, PeptidePal 76.1,
  ZenFit 66.6, Couch-to-5k 66.4.

## Scope — four deliverables

### 1. Peon reports after every round (agent-generated completion message)

When the daemon finishes a round, the real Go Peon agent — same persona, memory, and
proposal machinery as chat turns — writes the wrap-up message with an optional proposal
card, instead of the canned counts sentence.

**Mechanism:**
- New authed internal Go endpoint: `POST /internal/hunts/rounds/:round_id/completed`,
  authenticated with the existing service token (`AGF_API_TOKEN`), not user session auth.
- Handler loads the round (terms, settings, `progress` per-term telemetry
  `{pages, found, new, stop}`), its results (names, triage, relevance, scores), and the
  hunt thread context, then runs a round-completion agent turn via the existing
  `gemini.GenerateHuntTurn` path (`backend/controllers/hunt_controller.go:282-327` is the
  chat-turn analog) with a synthetic "round finished" input. Output = one assistant
  `hunt_messages` row: content (what was found by name, which terms were dead, what came
  up empty) + optional `proposal` JSONB (next terms / deeper settings), same shape the
  chat agent produces today.
- Python daemon (`src/agf/discovery/hunt_one.py:67-75`): after `finish_round`, call the
  endpoint (timeout ~120s). **On any failure, fall back to the current canned message**
  (`compose_round_message`, `src/agf/db/hunts.py:98-105`) so a round can never finish
  silently. Error/safety-net paths (`hunt_one.py:90-98`, `hunt_daemon.py:60-64`) keep the
  canned message.
- Daemon env: needs API base URL + `AGF_API_TOKEN`. Per-service compose env is a known
  landmine — add to the hunt daemon service in BOTH compose files (repo + VM).
- Agent context: extend round summaries (`HuntRoundSummaries`, `hunt_repo.go:236-258` /
  `summarizeRounds`, `hunt_controller.go:576-599`) with per-term yield detail from
  `progress` so proposals can say which terms to drop or deepen.
- Prompt additions: broad-terms lesson (few broad head terms beat many long-tail
  variants — measured); propose deeper paging when terms were still producing at the cap.

### 2. Results grid with save-to-gems

Replace the `ResultRow` text list (`web/src/components/hunt/RoundBlock.tsx:74-127`) with
a card grid under the round.

- **Card contents:** app icon, name, store badge (existing `StoreBadge`), GemScore badge
  once scored, one-line description/tagline. Kept results join via `kept_advertiser_id`
  to the same advertiser-level data the Discover feed uses (extend `ListHuntResults`,
  `backend/repos/hunt_repo.go:120-137`, with LEFT JOINs; follow the fullstack-field-add
  pattern: repo → Go model → api.ts → contracts → component).
- **Heart button** on kept cards: one tap = `POST /saved-gems/:advertiser_id` (appears on
  Saved page) AND `POST /ratings/:advertiser_id {rating:"like"}` (feeds preference
  learning). Both endpoints exist (`routers.go:61-67`); no new tables. Toggle off =
  remove saved + rating none. Current saved/rating state is served with hunt results
  (extend the `ListHuntResults` join) so state survives reloads.
- Pending (still-triaging) results: name + store badge + checking state, no heart (no
  `advertiser_id` yet — populated by `apply_triage_to_results`, `hunts.py:65-79`).
  Vetoed and off-target results stay in the existing collapsed groups.
- Copy stays plain and grounded in real data (project voice rules).

### 3. Empty-bubble fix

Root cause: a proposal-only Gemini turn stores `content=''` (`hunt_controller.go:310`,
column default `''`) and `MessageBubble` (`web/src/components/hunt/HuntView.tsx:455-481`)
renders the bordered box unconditionally at `:472`.

- UI guard: render the bubble only when content is non-empty; a proposal card can stand
  alone.
- Agent prompt: instruct the model to always accompany a proposal with a short reply
  (belt and suspenders; the UI guard is the fix).

### 4. Hunts reach the deep pages + depth telemetry

- **Minimum page floor:** new round setting `min_pages` (default 10, range 1–30,
  clamped ≤ `page_cap`); the dry early-exit may not fire before `min_pages` is reached.
  Sweep loop: `src/agf/discovery/hunt.py` (patience logic feeding
  `progress->'terms'->{term}.stop`).
- **Patience default 3 → 6** for hunts (`src/agf/db/hunts.py:13-14` defaults; Go-side
  validation ranges in the hunt controller/agent settings stay 1–10).
- **Record page depth per result:** migration 158 adds `frontend.hunt_results.page INT`
  (nullable); `_persist_page` (`hunt.py:93-123`) writes the SERP page each result came
  from; `upsert_hunt_result` (`hunts.py:48-56`) keeps the MIN page on conflict. From now
  on depth-yield is measurable per hunt.
  Migration rule: no bare `%` anywhere in the file, including comments.
- **No incumbent skip-list in v1** — the page floor achieves the goal with less
  machinery; new depth telemetry will show whether more is needed.

## Out of scope

- Incumbent/drama-app skip-list or dedup-before-paging.
- Liking pre-keeper (pending) candidates.
- Any change to the general (non-hunt) sweep lanes' patience/depth behavior.
- SSE/streaming for chat (polling stays).

## Error handling

- Completion-endpoint failure → canned fallback message (round never finishes silently).
- Agent turn returns empty reply → fallback message, never an empty row.
- Heart tap failure → optimistic UI reverts, toast with plain error.
- Judge/relevance behavior unchanged (fail-open, kept rows only).

## Testing & acceptance

- Unit/integration: Go (endpoint auth, completion turn writes exactly one message row,
  fallback on agent error; results join fields), Python (min_pages floor honored before
  dry-exit, page recorded on results, API-call fallback), web (bubble empty-guard, grid
  render states, heart toggle calls both endpoints).
- Full suites green (zero failed), `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`,
  one test consumer at a time, gates run bare.
- **Live e2e gate (required):** run a real hunt round on prod after deploy — verify the
  Peon completion message with real app names + proposal card arrives, grid renders with
  icons/scores, heart saves to Saved page (row in `frontend.saved_gems` +
  `frontend.app_ratings`), no empty bubble, and round telemetry shows terms paging past
  page 3 with `page` populated on results.
