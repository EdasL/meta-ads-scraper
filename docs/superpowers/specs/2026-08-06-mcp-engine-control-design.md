# Design — MCP engine control and history tools

Date: 2026-08-06
Surface: `backend/mcpserver` (hosted Field Desk MCP, `api.peon.works/mcp`)

## Goal

Let Claude Code drive the discovery engine from the outside: read what the
engine has found and which queries produced it, steer the keyword flywheel,
run an on-demand Ad Library pull for a named advertiser at a chosen depth, and
fetch the creative bytes. The loop we want to close is:

    keyword_yield  ->  pick the queries that produced keepers
    keyword_control ->  seed / wake the winners, retire the duds
    run_hunt       ->  test a term or pull one advertiser's ads
    hunt_status    ->  read the outcome, feed it back into keyword_yield

## What already exists (do not rebuild)

The engine's control surface is already data, not process configuration:

- **Per-keyword lifecycle** lives in `discovery.dynamic_keywords` rows:
  `status` (`pending|trial|active|resting|retired`), `next_eligible_at`,
  `dry_streak`, `rest_count`, `sweeps_used`, `new_candidates_total`, `source`,
  `last_swept_at`. Changing a row takes effect on the next flywheel tick — no
  restart, no new settings machinery.
- **Per-run scrape depth** lives in `frontend.hunt_rounds.settings` JSONB and is
  already bounded by `clamp_hunt_settings` (src/agf/db/hunts.py):
  `page_cap` 1–100, `patience` 1–10, `listing_pages` 1–30, `min_pages` 1–30.
- **Query attribution** lives in `discovery.candidates.source_keyword` +
  `status='kept'` + `kept_advertiser_id`, and in `frontend.hunt_results`
  (`source_term`, `triage`, `relevance`).
- **The job queue** is `public.pipeline_jobs` (`kind` in `hunt | add_page |
  corpus_refresh | enrich`), drained by the VM daemons.
- **Creative bytes** are served by `GET /api/media/*key` over
  `public.creatives.s3_key`.

The `AGF_*` environment variables are global defaults and safety caps
(`AGF_SWEEP_MAX_DEPTH=15`, lane counts, poll intervals). They are read at
process start and are explicitly **out of scope** — per-keyword and per-round
control give the same steering without restart semantics.

## The two Ad Library primitives

`run_hunt` exposes both, because they answer different questions:

1. **Keyword search** — `search_page(query, countries=["US"],
   search_type="KEYWORD_UNORDERED", cursor)`, cursor-paged, 3 parallel lanes,
   one proxy session each. Governed by `page_cap` / `patience` / `min_pages`.
2. **Page listing** — `typeahead(name)` → top 5 candidates re-ranked by fuzzy
   name similarity (likes break ties only) → best 3 tried →
   `search_listing(page_id, countries=[15 countries], max_pages=listing_pages)`.
   Pulls that advertiser's whole ad set, global, funnel gate auto-passed.

"Find Quizlooker's ads" is primitive 2 with `competitors: ["Quizlooker"]` and
no terms. It is not a keyword sweep.

## Tools

Seven new tools. Registration follows the existing role pattern: admin tools are
registered only on an admin's server (omission is the boundary, so a member's
`tools/list` never shows them) **and** re-check `h.u.Role != "admin"` inside the
handler as defense in depth, exactly as `scraping_health` does.

### Read — admin

**`keyword_yield`** — corpus-wide query performance. Per keyword: `status`,
`source`, `sweeps_used`, candidates found, **keepers** (`candidates.status =
'kept'`), gems reached, `dry_streak`, `last_swept_at`. Sorted by keeper yield
descending. Inputs: `source`, `status`, `min_sweeps`, `limit`, `offset`.
This is the tool that answers "which queries gave us the best results".

**`hunt_term_yield`** — the same question at hunt level, from
`frontend.hunt_results` grouped by `source_term`: `kept`, `relevant`,
`off_target`, rounds seen. Competitor pulls appear as `page:<name>`. Optional
`round_id` to scope to one round. Kept separate from `keyword_yield` because
hunt terms are founder-approved one-offs, not flywheel keywords.

**`users_overview`** — accounts with rating counts, saved-gem counts, last
activity, onboarded flag. Read-only, no PII beyond what the account already is.

### Write — admin

**`keyword_control`** — `action` ∈ `activate | rest | retire | seed`.
- `activate`: `status='active'`, `dry_streak=0`, `next_eligible_at=NULL`
- `rest`: `status='resting'`, `next_eligible_at = now() + cooldown_days`
- `retire`: `status='retired'`
- `seed`: insert new keywords as `status='trial'`, `source='mcp'`
Takes `keywords: []string` so a batch of winners moves in one call. Returns the
before/after status of each row. Unknown keywords are reported, not an error.

**`run_hunt`** — enqueue an on-demand round. Inputs: `terms: []string`,
`competitors: []string`, `settings: {page_cap, patience, listing_pages,
min_pages}`, `intent: string`, `hunt_id: int` (optional — creates a new hunt
thread titled from the intent when absent). Reuses `repositories.InsertHuntRound`,
which writes the round and the `pipeline_jobs` `kind='hunt'` row in one
transaction. Settings pass through untouched; `clamp_hunt_settings` on the
Python side is the single bound authority. Rejects a call with neither terms
nor competitors. Returns `{hunt_id, round_id, job_id, effective_settings}`.

**`hunt_status`** — poll one round: `status`, the live `progress` blob (lane
positions, event feed, per-term stats), `error_text`, and results with
`triage`/`relevance`. Read-only but grouped here because it is `run_hunt`'s
other half.

### Read — member

**`ad_creatives`** — ads and their creatives for an advertiser or a hunt round.
Per creative: `ad_id`, `external_ad_id`, `type`, `content_type`, `file_size`,
`body_text`, `title`, and a fully-qualified `url` pointing at
`/api/media/<s3_key>`. MCP results are JSON, so the tool hands back URLs and
Claude Code curls the bytes down itself with its `fd_` key. Member-visible
because it exposes the same gem data `gem_detail` already does.

## File layout

`mcpserver.go` is 243 lines and would roughly triple. Split by domain, keeping
`Handler`, `buildServer`, the `handlers` struct and `clampLimit` where they are:

    backend/mcpserver/mcpserver.go     Handler, buildServer, shared helpers
    backend/mcpserver/tools_member.go  the 6 existing member tools
    backend/mcpserver/tools_engine.go  keyword_control, run_hunt, hunt_status
    backend/mcpserver/tools_history.go keyword_yield, hunt_term_yield,
                                       users_overview, ad_creatives

New repository files, following the existing raw-SQL-through-gorm convention:

    backend/repositories/keyword_repo.go   yield read + lifecycle writes
    backend/repositories/creative_repo.go  ads + creatives for the media URLs

`hunt_term_yield` and `users_overview` extend `hunt_repo.go` and `user_repo.go`
rather than adding files.

## Error handling

Follows the file's existing contract: handlers return a plain lowercase sentence
and never leak driver errors. Not-found is an error ("gem not found."), empty is
a `note` in the result. `keyword_control` and `run_hunt` validate inputs before
touching the DB and name the offending field.

## Testing

Extend `backend/internal/integration/mcp_test.go`, which already drives the real
MCP over `httptest` with a per-role seeded user and a minted `fd_` key. Every
new tool gets:

1. a member cannot see it in `tools/list` and cannot call it (admin tools only)
2. an admin gets the expected shape back
3. write tools assert the resulting DB state, not just the tool result

`run_hunt` asserts exactly one `pipeline_jobs` row `kind='hunt'` with the round
id, mirroring the assertions already in `hunt_test.go`. No live Meta calls in
tests — the daemon side is already covered by the Python suite.

## Verification before done

Unit and integration green is not sufficient for this feature: it spans the Go
API, Postgres, and the VM daemons. Done means one live round driven end to end
from Claude Code against prod — `run_hunt` with a competitor name, `hunt_status`
polled to `done`, `ad_creatives` returning URLs, and one creative actually
downloaded through `/api/media`.

Open item to verify during implementation, not assumed: whether `GET
/api/media/*key` accepts a personal `fd_` key or only `AGF_API_TOKEN`. If it
accepts only the shared token, `ad_creatives` needs the media route to admit
`fd_` keys before the download step works.

## Explicitly out of scope

- A DB-backed settings table for the global `AGF_*` knobs. Per-keyword and
  per-round control covers the need; global knobs would need restart semantics
  per knob for little gain.
- Any write tool for members. Every write here steers a shared engine.
- Binary payloads in MCP results. URLs plus curl is the download path.
