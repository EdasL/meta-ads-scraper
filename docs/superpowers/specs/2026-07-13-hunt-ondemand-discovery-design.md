# Hunt — on-demand AI-assisted gem discovery

**Date:** 2026-07-13
**Status:** Approved design (founder-reviewed in session)

## What this is

A new **Hunt** page where the founder chats with an AI to hunt a specific niche
or a competitor's ads on demand. The AI brainstorms Ad Library search terms,
the founder edits/approves them, the worker sweeps them against Meta
immediately (deep, adaptive paging), pulls named competitors' own ad pages,
and streams results back live. Everything found flows into the normal
system-wide pipeline (candidates → triage → enrich → score), and the terms
join the keyword pool — a hunt permanently enriches the system, not just the
chat.

Founder-locked scope decisions:

- Result = **fast raw list** (streamed as found), not wait-for-full-scores.
  Kept finds continue into enrich/scoring and the hunt view links to gem
  pages as they land.
- AI **shows terms first**; founder edits, then fires the run.
- **Hunts page with history** — each hunt is a persistent thread.
- **Conversational rounds** — follow-up messages in the same thread spawn new
  term batches (AI knows what was tried/found) and new runs.
- **Always deep** — no quick/deep toggle. Adaptive paging (patience + cap),
  because the gem band sits below incumbents in the impressions-desc order.
- **Fast execution + live activity feed** — parallel term lanes, per-page
  progress events rendered in the UI.

Standing product constraints restated: US-only sweeps, apps are the target
(web-funnel rejects still fold into `funnel_candidates` automatically via the
shared sweep classifier). Product nouns: this surface is called **Hunt**; all
copy plain and grounded in real data per CLAUDE.md.

## Architecture (approved fork)

Chat + term brainstorming live in the **Go API** (Gemini, existing
`GeminiHelper` pattern). Sweep execution lives on the **worker VM** (proxies +
Python scraper live there; GKE cannot reach Meta). Transport between them is
the existing **`public.pipeline_jobs`** queue with a new kind `'hunt'`,
drained at top priority. Rejected alternatives: direct API→worker HTTP call
(new network surface, dies with the connection) and porting scraping to Go
(non-starter).

## Data model — migration `db/schema/155_hunts.sql`

All new tables in `frontend` schema (owned by the product UI, like
`saved_gems`, `app_ratings`). No bare `%` anywhere in the migration file,
including comments (use "pct"/"mod(a,b)").

- `frontend.hunts` — the thread. `id bigserial PK`, `user_email text NOT
  NULL`, `title text NOT NULL` (first request, truncated), `created_at`,
  `updated_at`.
- `frontend.hunt_messages` — chat transcript. `id bigserial PK`, `hunt_id FK`,
  `role text CHECK (role IN ('user','assistant'))`, `content text` (assistant
  reply prose), `proposal jsonb` (assistant-only: `{"terms": [...],
  "competitor_apps": [{"name":..., "store_hint":...}]}`), `created_at`.
- `frontend.hunt_rounds` — one approved run. `id bigserial PK`, `hunt_id FK`,
  `message_id FK` (the proposal it came from), `terms jsonb NOT NULL` (the
  founder-edited final list), `competitor_apps jsonb NOT NULL DEFAULT '[]'`,
  `status text NOT NULL DEFAULT 'queued' CHECK (status IN
  ('queued','running','done','error'))`, `progress jsonb NOT NULL DEFAULT
  '{}'`, `error_text text`, `created_at`, `started_at`, `finished_at`.
- `frontend.hunt_results` — one row per (round, found advertiser page).
  `id bigserial PK`, `round_id FK`, `candidate_id bigint` (→
  `discovery.candidates.id`), `page_id text NOT NULL`, `page_name text`,
  `store text`, `store_id text`, `link_url text`, `source_term text NOT NULL`
  (the term or `page:<name>` for competitor-page pulls), `was_new boolean NOT
  NULL` (new to the system vs already-known candidate), `triage text`
  (`kept` / `vetoed` / `pending`), `kept_advertiser_id bigint` (set when
  triage promotes; the gem-page link key), `created_at`.
  Unique `(round_id, page_id, store, store_id)`.
- Indexes: `hunt_messages(hunt_id)`, `hunt_rounds(hunt_id)`,
  `hunt_results(round_id)`.

`pipeline_jobs` reuse: enqueue `kind='hunt'` with `advertiser_id = round_id`
(the column is a generic bigint key for the dedup index `(kind,
advertiser_id) WHERE queued|running`; note the naming mismatch in a code
comment at the enqueue site). No schema change to `pipeline_jobs`.

## Go API (backend/)

New controller `backend/controllers/hunt_controller.go`, repo
`backend/repositories/hunt_repo.go`. Routes under the existing `/api` group
(KeyAuth + X-User-Email); writes add `middlewares.RequireToken()`. All hunts
scoped to the requesting `X-User-Email`.

- `POST /api/hunts` `{message}` → creates hunt + user message, calls Gemini,
  stores assistant message with proposal, returns the full thread.
- `GET /api/hunts` → founder's hunts, newest first (id, title, created_at,
  last round status).
- `GET /api/hunts/:id` → thread: messages, rounds (with progress), results
  per round. The poll endpoint; must stay cheap (indexed reads only).
- `POST /api/hunts/:id/messages` `{message}` → appends user message, calls
  Gemini with thread context + per-round summaries (terms already tried,
  found/kept counts) so follow-up proposals exclude what round N covered.
- `POST /api/hunts/:id/rounds` `{message_id, terms[], competitor_apps[]}` →
  validates (1–15 terms, ≤3 competitor apps, terms trimmed/lowercased,
  deduped), inserts `hunt_rounds` row, enqueues `pipeline_jobs('hunt',
  round_id)`. Returns the round.

Gemini: extend `backend/helpers/gemini.go` with
`GenerateHuntProposal(ctx, in HuntProposalInput) (HuntProposal, error)` —
structured ResponseSchema `{reply string, terms []string, competitor_apps
[]{name, store_hint}}`. Prompt constraints: terms are **generic niche
queries** in the style of the keyword pool ("sleep stories app", "ai bedtime
audio") — never brand names except in `competitor_apps`; ≤15 terms;
plain-language `reply` explaining the angle choices. Input carries the user
message, thread history, and prior-round summaries.

Timeouts/failure: Gemini call bounded at 30s; on error return the user
message stored + an assistant error message (`proposal` null) so the thread
stays consistent and the founder can retry.

## Worker (src/agf/)

**Priority drain.** Hunt jobs are claimed **before** every other lane
(founder is actively waiting). Throttle order becomes hunt → keeper → corpus
→ sweep. Concretely: the keeper/pipeline daemon's lane loop checks
`claim_next(conn, 'hunt', lease_sec)` first each cycle; when a hunt job is
claimed it runs the round executor as a subprocess (same isolation pattern as
`keeper_one` / `enrich_one`) and skips lower lanes for that slot. Poll cadence
for new hunt jobs ≤5s so a queued round starts near-instantly.

**Round executor** — new `src/agf/discovery/hunt.py`,
`run_hunt_round(engine, extractor_factory, round_id) -> dict`:

1. Load round (terms, competitor_apps). Mark `running`, `started_at`.
2. **Resolve competitor pages:** for each competitor app name,
   `search_pages_typeahead` → best page match(es); emit progress event
   ("Resolving <name>'s ad page"). For each resolved page:
   `facade.search_listing(page_id=...)` bounded to 10 pages, records → the
   same classify/persist path as sweep pages (`upsert_sweep_candidate`),
   results tagged `source_term='page:<name>'`.
3. **Parallel term sweep:** 3 concurrent lanes (asyncio, one extractor per
   lane — the proven DataImpulse concurrency), each lane takes terms off a
   shared list and runs the existing adaptive paging (patience-based early
   exit, hunt cap = 40 pages) via `run_sweep(keywords=[term], max_pages=...,
   collect=hook)` or the equivalent single-term internals. The collect hook
   captures per-ad `(page_id, page_name, store, store_id, link_url,
   candidate_id, was_new)` and upserts `hunt_results` **as pages complete**,
   so the UI streams.
4. **Progress contract:** after every page, update `hunt_rounds.progress`
   (single UPDATE, no row churn): `{"lanes": {"0": {"term": ..., "page": N,
   "found": N}, ...}, "terms_done": N, "terms_total": N, "found": N,
   "new": N, "events": [last 50 of {"t": iso, "msg": ...}]}`. Events are
   plain sentences — they are the UI's activity feed verbatim.
5. **Inline triage:** after sweeps finish, run the triage decision
   (store lookups + veto rules, same functions `run_triage` uses) scoped to
   this round's new candidate ids; update `hunt_results.triage` +
   `kept_advertiser_id`; kept advertisers are enqueued for enrich via the
   existing `enqueue_enrich` (they'll score and appear in the feed).
6. **Persist terms:** `add_trial_keywords(conn, terms, source='hunt',
   kind='app')` — they enter the normal flywheel lifecycle (re-swept while
   yielding, retired when dry). Competitor page pulls do NOT mint keywords.
7. Mark round `done` (or `error` with `error_text`), stamp `finished_at`,
   `set_job_status` the pipeline job. **Failure-path parity:** any
   term/page-level error is caught per-lane, logged as an event, and the
   round still finishes with partial results; only a whole-round crash marks
   `error`. The pipeline job reaches a terminal status on every path (no
   hot-loop; the `(kind, advertiser_id)` dedup index also blocks
   double-enqueue of a live round).

Budgets (constants in `hunt.py`): `HUNT_MAX_TERMS=15`, `HUNT_LANES=3`,
`HUNT_PAGE_CAP=40`, `HUNT_LISTING_PAGES=10`, `HUNT_MAX_COMPETITORS=3`,
per-page delay same as sweep default.

**Proxy contention watch:** a hunt's 3 lanes can coincide with the nightly
sweep's 3 — up to 6 concurrent DataImpulse streams for the round's duration.
Judged acceptable for v1 (residential pool, short bursts); if 1675004
rate-limit errors spike during hunts, the mitigation is pausing sweep lane
claims while a hunt round is running (do not build pre-emptively).

## Web UI (web/src/app/hunt/)

New page `/hunt` (added to the protected-routes list — verify the cookieless
307 after deploy) + nav entry.

- **Left rail:** hunt history (title, date, last-round status). New hunt
  button.
- **Thread view:** chat messages; assistant proposal renders as an editable
  term-chip list (remove/add/edit) + competitor apps with checkboxes + a
  **Run** button that posts the edited set to `/rounds`.
- **Round block** (appears under its proposal): activity feed (the
  `progress.events` sentences, newest on top, auto-scrolling), lane status
  line ("3 lanes · term 6/12 · page 23"), and the streaming results list.
- **Results list columns** (all real data, no derived labels): page name,
  store badge (iOS/Android/web), source term, first-seen ad date, new-to-
  system marker, triage outcome (pending → kept/vetoed), and — once enriched
  — GemScore linking to `/gems/<kept_advertiser_id>`.
- **Polling:** `GET /api/hunts/:id` every 2s while any round is
  `queued|running` (RefreshDataButton pattern: resume on mount, stop when
  terminal), every 30s while kept results still lack scores.
- API client additions in `web/src/lib/api.ts` (`postHunt`, `getHunts`,
  `getHunt`, `postHuntMessage`, `postHuntRound`) + contract types.
- Copy: plain and honest ("Searching 'sleep stories app' — page 14",
  "12 found · 5 new to Field Desk"). No spinners-with-no-words.

Note: this repo's Next.js is modified — read `web/AGENTS.md` +
`node_modules/next/dist/docs` conventions before writing web code
(`src/proxy.ts` is the middleware file).

## Testing

- Python: unit tests for the round executor with a fake extractor (records →
  results rows, was_new split, progress shape, event cap, per-lane error →
  partial results + `done`, whole-round error → `error` + job terminal);
  DB tests (AGF_TEST_DB_URL @55433/agf_it) for migration 155, hunt_results
  upsert/unique, round claim via pipeline_jobs, inline-triage scoping
  (only the round's candidates), `add_trial_keywords` source='hunt'.
- Go: repo + controller tests (scoping by user_email, validation bounds,
  Gemini helper behind an interface with a fake).
- Web: contract/adapter tests per existing `node --test` setup.
- Real-I/O caveat: lane count × page delay means a live round is minutes of
  real Meta traffic — fakes prove plumbing only; the live gate below is the
  real "done".

## Live verify (the gate)

On the worker VM via the throwaway-container pattern (prod image + bind-mount,
`-w` to the mount): execute one real round end-to-end — insert a hunt/round
for a known niche (e.g. "sleep stories"), run the executor, watch
`progress.events` advance, confirm `hunt_results` rows with correct
`was_new`, triage outcomes, enrich jobs enqueued, keywords minted with
source='hunt'. Then the deployed path: roll images, create a hunt from the
live UI, verify queue pickup <10s, streaming UI, and a kept find reaching its
gem page. Zero failed tests in the full suites (a skip is not a pass).

## Non-goals (v1)

- No scheduled/recurring hunts, no auto-rerun.
- No biasing of the main breadth sweep by hunt activity (taste-blind sweep
  decision stands).
- No multi-user sharing of hunts (scoped to owner email).
- No sweep-lane pause during hunts (watch first).
