# Adaptive-depth sweep + never-drop keyword lifecycle — design spec

**Date:** 2026-07-07
**Status:** approved design (founder), grounded by a live prod deep-sweep probe.
**Author:** ship-increment

## Problem

The discovery sweep caps every keyword at a flat 2 pages (`AGF_SWEEP_PAGES`) and
**permanently retires** a keyword after 5 dry sweeps (`record_keyword_sweep`). A live
prod deep-sweep of 6 keywords that were retired this way found **27 net-new small-brand
app/funnel candidates on pages 3–15** (~4.5/keyword; ≈ mid-teens eventual score≥60 gems),
while **pages 1–2 yielded 0 net-new** — those keywords were fully mined at the 2-page cap
and every new gem came from page 3+. Broad keywords ("tv remote", "football matches")
keep yielding net-new past page 15; narrow keywords ("sleep tracker app", "portraits with
ai") return <15 ads total with `has_next=false` (genuinely empty — deeper = zero). Results
are relevancy-ordered small brands at every depth (~90% under the 10k-likes veto), **not**
an incumbent wall. **1,582 keywords are currently retired on dry_streak**, many once
productive. Found-at-depth is logged nowhere.

Two defects: (1) the flat shallow cap discards deep tail gems on broad keywords; (2)
terminal retirement throws keywords away forever even though the result top refreshes and
the phrase's ad inventory grows over time.

## Design

Two coupled changes, plus depth logging and a backlog migration.

### 1. Adaptive-depth sweep (replaces the flat page cap)

`run_sweep` drives paging itself instead of handing a fixed page count to the extractor.
Per keyword, page from 1 via cursor, upsert each page's rows, count net-new for that page,
and **stop when any of**:
- `has_next == false` (Meta has no more) — narrow keywords stop at page 1 automatically;
- **P consecutive pages with 0 net-new** (`AGF_SWEEP_PATIENCE_PAGES`, default **3**) — the
  patience window; large enough to clear the typical ~2-page already-mined prefix and reach
  the page-3 onset of net-new, small enough to bound cost;
- total pages ≥ **safety cap** (`AGF_SWEEP_MAX_DEPTH`, default **15**).

Broad keywords page deep while productive; narrow keywords stop immediately. No global
page-count guess. Depth logging (below) lets us tune P/cap from real data later.

**Extractor support (no `search_ads` rewrite).** `search_ads` (`src/agf/extract/search.py`)
already paginates via cursor and returns `(records, last_cursor, has_next)`; the facade
`Extractor.search` currently discards the cursor/has_next and returns a flat list.
- Add `cursor: str | None = None` param to `search_ads` to **seed** the first request
  (it already threads `cursor` internally; this exposes the start).
- Add a facade method `Extractor.search_page(query, ..., cursor=None, max_pages=1) ->
  (records, cursor, has_next)` (mirroring the existing `search_listing` precedent at
  `facade.py:97` which already returns `(records, has_next)`). Keep the old `search()` for
  non-sweep callers.
- Mirror `search_page` in `FakeExtractor` (`src/agf/extract/fake.py`) so tests can script
  per-page results + has_next + cursor.

`run_sweep` loop replaces the single `extractor.search(max_pages=pages)` call: iterate
`search_page` passing the returned cursor, upsert each page (tracking `was_new` per page for
the patience counter), accumulate `candidates_new`, honor the stop rule. `serp_requests` =
actual pages fetched (no longer an upper-bound guess). Preserves existing per-keyword delay
and error/circuit-breaker handling.

### 2. Never-drop keyword lifecycle (`active` ↔ `resting`)

Replace terminal retirement with a two-state cycle. Add a `resting` status; a keyword is
never dropped once it has produced.

- **active → resting** when `dry_streak >= K` (`AGF_FLYWHEEL_REST_DRY_STREAK`, default **3**;
  each sweep is now deep, so a dry sweep is more conclusive than under the old 2-page cap).
  On rest: `status='resting'`, `rest_count += 1`, `next_eligible_at = now() +
  cooldown(rest_count)`.
- **cooldown(n)** = `min(base * 2^n, cap)` days, `base=7` (`AGF_FLYWHEEL_REST_COOLDOWN_DAYS`),
  `cap=28` (`AGF_FLYWHEEL_REST_COOLDOWN_MAX_DAYS`): 7 → 14 → 28 → 28…
- **wake:** `live_keywords` also selects `status='resting' AND next_eligible_at <= now()`.
  The woken keyword gets a fresh adaptive pass from page 1 (top refreshed, inventory grown).
- **resting → active** on a woken sweep that yields net-new: `status='active'`,
  `dry_streak=0`, `rest_count=0`.
- **resting → resting** (still dry on wake): `rest_count += 1`, longer cooldown. Never
  terminal-drop.

Trial-stage behavior is unchanged (`trial → active|retired` on promotion); a keyword that
**never** promoted stays `retired` (we only keep alive keywords that have proven they
produce). This scopes "never drop" to proven producers, not unproven trials.

### 3. Found-at-depth logging

Add `first_seen_depth INT` (1-based page index) to `discovery.candidates`. `run_sweep`
passes the current page index into the `row` dict; `upsert_sweep_candidate` sets it on
INSERT and `LEAST(existing, new)` on conflict (record the shallowest depth a candidate was
ever seen at). Nullable — old rows stay null. Purpose: learn the real depth curve to tune P
and the safety cap, and to answer "where do gems live" without another live scrape.

### 4. Backlog re-activation (migration)

`UPDATE discovery.dynamic_keywords SET status='resting', next_eligible_at=now(),
rest_count=0 WHERE status='retired' AND new_candidates_total > 0`. This wakes the ~proven
subset of the 1,582 retired keywords immediately (broad ones will yield on the deep pass;
narrow ones re-rest cheaply). Keywords that never produced (`new_candidates_total = 0`) stay
retired.

## Schema (migration `147_adaptive_sweep_lifecycle.sql`)

- `discovery.dynamic_keywords`: add `next_eligible_at TIMESTAMPTZ NULL`, `rest_count INT NOT
  NULL DEFAULT 0`; extend the `status` CHECK constraint to include `'resting'` (drop+recreate
  the constraint from `136_dynamic_keywords_taste.sql`).
- `discovery.candidates`: add `first_seen_depth INT NULL`.
- Backfill UPDATE (§4).
- Next number is **147** (highest is `146`).

## Files touched

- `db/schema/147_adaptive_sweep_lifecycle.sql` (new).
- `src/agf/extract/search.py` — `cursor` param on `search_ads`.
- `src/agf/extract/facade.py` — new `search_page` method.
- `src/agf/extract/fake.py` — mirror `search_page`.
- `src/agf/discovery/sweep.py` — adaptive-depth paging loop in `run_sweep`; thread page index
  into the candidate row.
- `src/agf/db/candidates.py` — `first_seen_depth` in `upsert_sweep_candidate` (INSERT +
  LEAST on conflict).
- `src/agf/db/dynamic_keywords.py` — `record_keyword_sweep` two-state lifecycle;
  `live_keywords` wake clause; `cooldown()` helper + new env knobs.
- Tests: `tests/discovery/test_sweep.py` (adaptive depth: broad keeps paging, narrow stops at
  page 1, patience-window early stop, depth recorded), plus a new
  `tests/discovery/test_dynamic_keywords_lifecycle.py` (active→resting, cooldown backoff,
  wake eligibility, resting→active on yield, never-drop). Fake extractor scripts per-page
  results + has_next.

## Env knobs (all defaulted; tunable)

`AGF_SWEEP_PATIENCE_PAGES=3`, `AGF_SWEEP_MAX_DEPTH=15`, `AGF_FLYWHEEL_REST_DRY_STREAK=3`,
`AGF_FLYWHEEL_REST_COOLDOWN_DAYS=7`, `AGF_FLYWHEEL_REST_COOLDOWN_MAX_DAYS=28`. The old
`AGF_SWEEP_PAGES` becomes the *initial* page batch / floor, and `AGF_FLYWHEEL_RETIRE_DRY_STREAK`
is superseded by the rest threshold (keep the env read for back-compat, unused).

## Non-goals / decisions

- No cursor caching across sweeps (re-fetching the top each wake is intentional — that's how
  the refreshed top gets re-checked; it also dedupes cheaply downstream).
- No change to trial promotion or to the veto/scoring.
- Resting applies to proven producers only (trial-failed keywords stay retired).
- Patience window may rarely miss a deep gem after a ≥P dry gap; accepted — the wake cycle
  gives another pass, and depth logging will tune P.

## Verification bar

- Full suite green (`AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`, docker
  container `agf_test_pg`; 0 failed, skips ≠ pass).
- Live one-off in the prod worker container (VM `agf-worker`, europe-west1-b, peon-489013;
  `docker compose exec -T worker`): a broad dry keyword pages past 2 and surfaces net-new; a
  narrow keyword stops at page 1 (no runaway depth); a resting keyword with
  `next_eligible_at<=now()` is woken and swept; `first_seen_depth` populated. This is a
  DB+scraper+lifecycle change — mock greens do not satisfy the bar.
