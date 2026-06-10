# Breadth-first ad-library discovery — design

**Date:** 2026-06-10
**Status:** approved direction (Approach A), pending user spec review
**Decisions locked by user:** ad-library discovery only (no app-store-first net for now) ·
US-only · apps-only (ads whose destination is an app store; funnels deferred to v2) ·
triage bar = existing user-set thresholds · daily cron, ~300-keyword taxonomy ·
page-likes ceiling = `INCUMBENT_PAGE_LIKES_MAX` (10k), NOT the old 500k sweep veto.

## Problem

Discovery today is depth-heavy and breadth-starved: keyword sweeps feed barely-filtered
candidates straight into enrichment (~25–30 proxy requests each, `AGF_ENRICH_LIMIT=10`
per sweep), so most of the proxy budget deepens a handful of candidates instead of
widening the pool. State lives in `candidates.json` files, so a daily run would
re-discover and re-triage the same candidates forever.

Goal: maximize the number of distinct candidate apps found per proxy dollar, spend
near-zero on the keep/drop decision, and deep-enrich only keepers.

## Architecture

```
taxonomy (~300 narrow US app keywords)
   │  SWEEP — proxy; 1–2 SERP pages/keyword; inline drops
   ▼
discovery.candidates   (status: new → kept | dropped; deduped across days)
   │  TRIAGE — zero proxy; batch store lookups; thresholds
   ▼
tracked pool (existing advertisers/apps rows → daily velocity accrual)
   │  ENRICH — proxy; keepers only; budget-bounded
   ▼
scoring + vetoes → frontend feed (unchanged)
```

Each stage is strictly cheaper than the next and gates it. Stage state is persisted, so
runs are resumable and the pool compounds daily.

## Stage 1 — Sweep (proxy, wide and shallow)

- Module: `src/agf/discovery/sweep.py`, productionized from `scripts/discover_experiment.py`,
  using the existing hardened `Extractor` facade (pacing, jitter, backoff, rotate-on-1675004).
- Input: expanded taxonomy (`src/agf/discovery/taxonomy.py`) — ~250–350 narrow keywords.
  **The funnel-phrase layer is removed from the sweep** (apps-only); its budget goes to
  more store-fragment and niche-app keywords (probes showed narrow keywords are what
  surface small apps).
- Per keyword: max 2 SERP pages (`AGF_SWEEP_PAGES`, default 2), US-only
  (`AGF_SWEEP_COUNTRIES` pinned to `US`; the EU Firehose-B probe path is dropped from
  the daily flow).
- Uses ONLY fields already on the SERP result (no extra requests): `page_id`, `page_name`,
  `page_like_count`, `link_url`, `cta_type`, `start_date`, creative URLs.
- Inline drops, in order:
  1. destination not an app-store link (apps-only) — store-link classification reused
     from the existing discovery code;
  2. `SOCIAL_HOSTS` destinations;
  3. `page_like_count > INCUMBENT_PAGE_LIKES_MAX` (imported from
     `src/agf/scoring/vetoes.py` — single source of truth, currently 10_000).
- Survivors upserted into `discovery.candidates`. Re-seeing an existing candidate bumps
  `last_seen_at` (and `times_seen`); it never resets status or re-triages a `dropped` row.

## Stage 2 — Triage (free, no proxy)

- Module: `src/agf/discovery/triage.py`.
- Input: candidates with status `new`.
- Lookups (both direct, proxy-less): iTunes Lookup batched 200 ids/request (~20 req/min
  pace); Google Play via the existing Node sidecar. US storefront (`country=us`).
- Keep rules (all must pass), pure functions over lookup results:
  - US store listing exists (dead/missing → `dropped: no_us_listing`);
  - not a game (store genre; reuses existing `is_game` classification) → `dropped: game`;
  - Android: `installs < 100_000` → else `dropped: installs_over_threshold`;
  - iOS: `rating_count < 5_000` → else `dropped: ratings_over_threshold`.
  Thresholds imported from the same user-set constants scoring uses (vetoes module) —
  never redefined locally. `release_date` is recorded but NOT a gate (an older app that
  just started scaling ads must still get in).
- Keepers: status `kept`, promoted via existing `upsert_advertiser()` + `upsert_app()`
  into the tracked pool (`kept_advertiser_id` recorded); daily velocity accrual picks
  them up through the existing worker (`refresh_apps`, ad snapshots).
- Lookup failures (network/5xx): candidate stays `new` and retries next run.

## Stage 3 — Enrich (proxy, deep, keepers only)

- Existing enrichment logic (`scripts/enrich_candidates.py` → worker step), fed only by
  `kept` candidates not yet enriched, oldest-first.
- The fixed `AGF_ENRICH_LIMIT=10` cap is replaced by a per-run proxy-request budget
  (`AGF_DISCOVERY_ENRICH_BUDGET`, default ~600 requests ≈ 20–25 keepers/day at current
  depth). Budget exhausted → remaining keepers wait for tomorrow's run.
- Depth knobs unchanged for v1 (`HIST_PAGES=12`); early-exit-on-pattern is a later
  optimization, not in scope.

## Data model (one migration)

`db/schema/112_discovery_candidates.sql` (111 is the current highest):

```sql
CREATE SCHEMA IF NOT EXISTS discovery;

CREATE TABLE discovery.candidates (
  id                 BIGSERIAL PRIMARY KEY,
  page_id            TEXT NOT NULL,
  page_name          TEXT,
  page_like_count    INTEGER,
  store              TEXT NOT NULL CHECK (store IN ('ios','android')),
  store_id           TEXT NOT NULL,
  link_url           TEXT,
  cta_type           TEXT,
  ad_start_date      DATE,
  source_keyword     TEXT,
  technique          TEXT,
  times_seen         INTEGER NOT NULL DEFAULT 1,
  first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  status             TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','kept','dropped')),
  drop_reason        TEXT,
  triaged_at         TIMESTAMPTZ,
  kept_advertiser_id BIGINT REFERENCES public.advertisers(id),
  UNIQUE (page_id, store, store_id)
);
CREATE INDEX ON discovery.candidates (status);

CREATE TABLE discovery.sweep_runs (
  id                 BIGSERIAL PRIMARY KEY,
  started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at        TIMESTAMPTZ,
  keywords_swept     INTEGER NOT NULL DEFAULT 0,
  serp_requests      INTEGER NOT NULL DEFAULT 0,
  candidates_new     INTEGER NOT NULL DEFAULT 0,
  candidates_seen    INTEGER NOT NULL DEFAULT 0,
  triaged_kept       INTEGER NOT NULL DEFAULT 0,
  triaged_dropped    INTEGER NOT NULL DEFAULT 0,
  drop_reasons       JSONB,
  enrich_requests    INTEGER NOT NULL DEFAULT 0,
  enriched           INTEGER NOT NULL DEFAULT 0,
  error              TEXT
);
```

One ad can surface multiple store links over time; `(page_id, store, store_id)`
uniqueness means an advertiser with both an iOS and Android listing is two candidate
rows that promote to one advertiser + two apps (existing upsert semantics handle this).

## Scheduling

Daily discovery step in the existing worker loop (`src/agf/workers/main.py`), same
≤1×/day pattern as `refresh_apps`: sweep → triage → enrich, in that order, one
`sweep_runs` row per day. Stages run sequentially; a stage failure records `error` but
lets cheaper downstream work that doesn't depend on it proceed (triage is retried for
leftover `new` rows next run regardless).

## Error handling

- Meta throttle (1675004): existing backoff + proxy-rotate; if the sweep exhausts
  retries/budget mid-taxonomy, it stops gracefully, records progress, and triage still
  runs (free). Tomorrow's sweep continues from a shuffled keyword order so the same tail
  isn't starved every day.
- Store lookup failures: candidate stays `new`; permanent absences become
  `dropped: no_us_listing`.
- Every drop records a reason — the gate is auditable (`SELECT drop_reason, count(*)`),
  so "is triage too tight?" is a query, not a debate.

## Testing

- Unit: triage keep/drop rules as pure functions (fixture lookup payloads, threshold
  edges); SERP→candidate parsing and inline drops (fixture GraphQL JSON).
- DB-backed: candidate upsert/dedup semantics (re-seen bumps, dropped stays dropped),
  promotion wiring — scoped to sentinel ids (shared-DB test-pollution lesson).
- Live e2e smoke (required before "done", per repo lesson — fakes have missed assembly
  breaks 4×): one narrow keyword swept through sweep→triage against real Meta + real
  iTunes/Play + real DB; assert candidate rows, statuses, and a promoted keeper.

## Out of scope (explicit)

- Funnel candidates and funnel→app resolution (v2; the candidates table leaves room —
  a future `store='funnel'` variant or sibling table).
- App-store-first discovery net (parked by user decision).
- Early-exit enrichment pagination; multi-country sweeps; taxonomy auto-generation
  beyond the hand-expanded ~300 keywords.
