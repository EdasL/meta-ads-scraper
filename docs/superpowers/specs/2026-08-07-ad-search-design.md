# Ad search — an MCP research lane for Meta ads

Design, 2026-08-07.

## Problem

The only on-demand Meta scrape reachable from MCP is `run_hunt`. Hunt exists to
find *apps and funnels*: `classify_candidate` reads each ad, keeps the
advertiser (page → store id), and **discards the ad itself**. What survives is
then triaged against the app stores, vetoed, relevance-judged, enrich-enqueued,
and its terms minted as keywords.

For ad research the ad *is* the artifact. The founder wants to point Claude at a
niche, get back the ads other people are actually running, and reason about them
directly — which visuals hook, which offers repeat, what a winner looks like in
this category. Hunt's vetting is not just unnecessary there, it removes the
payload: an ad from a page with no app store listing (a web2app funnel, a DTC
brand, a competitor's brand campaign) is often the most instructive one.

The scraper already returns everything needed. `AdRecord` (src/agf/parse.py)
carries `body_text`, `cta_type`, `cta_text`, `link_url`, `is_active`,
`start_date`, `end_date`, `collation_count`, `page_like_count`, `image_urls`,
`video_urls`. Hunt throws all of it away except the page identity.

## What this builds

A second lane that keeps the ads and skips the vetting, exposed as two MCP
tools that mirror the `run_hunt` / `hunt_status` pair.

### Scope decisions

- **New sibling, not a change to hunt.** Hunt keeps its app/funnel job
  unchanged. No shared code path is modified; the new executor reuses the
  extractor primitives (`search_page`, `typeahead`, `search_listing`) directly.
- **Zero side effects on the discovery corpus.** An ad search writes only its
  own two tables. It must not write `discovery.candidates`,
  `public.advertisers`, `discovery.funnel_candidates`, `discovery.dynamic_keywords`,
  or `public.pipeline_jobs` kind='enrich'. This is a testable invariant, not a
  convention (see Testing).
- **No media mirroring.** The tool returns Meta's own `image_urls` /
  `video_urls`. `download_creative`'s `_default_fetch` is a bare
  `httpx.get(follow_redirects=True)` with no proxy — media, unlike the GraphQL
  API, needs no residential egress, so the MCP client fetches the bytes itself
  and extracts video frames locally. This removes GCS writes, an ffmpeg
  dependency in the worker, and the `ads`/`creatives` tables from the design.
  Cost: fbcdn URLs are signed and expire (see Staleness).

## Surface

Both tools are admin-only, matching every other engine tool, and re-check
`h.u.Role != "admin"` in the handler (defense in depth, per tools_engine.go).

### `find_ads`

| field | type | meaning |
|---|---|---|
| `terms` | []string | keyword searches, US, `KEYWORD_UNORDERED` |
| `competitors` | []string | brand/app names; typeahead-resolved, pulled globally |
| `pages` | int | pages per term, 1–20, default 5 |
| `listing_pages` | int | pages per competitor listing, 1–30, default 10 |
| `intent` | string | free text, recorded on the search row; no LLM judge reads it |

Reuses `helpers.NormalizeHuntTerms` / `helpers.TrimHuntCompetitors` and the
`MaxHuntTerms` (15) / `MaxHuntCompetitors` (10) caps from
backend/helpers/gemini.go — one place defines what a legal scrape request is.
Both helpers return non-nil empty slices, which is also what keeps the
nil-slice → JSON `null` trap (fixed in 0781ef1) from recurring here.

Empty after normalization → error. Over cap → error, never silent truncation:
a scrape is real spend and dropping requested terms hides the cost decision.

Returns `{search_id, status: "queued", note}`.

### `ad_search_status`

| field | type | meaning |
|---|---|---|
| `search_id` | int | required |
| `min_days` | int | only ads running at least N days, default 0 |
| `media` | string | `any` (default), `video`, `image` |
| `per_advertiser` | int | max ads per page_id, 1–50, default 3 |
| `limit` / `offset` | int | default 25, max 100 (media URL arrays are bulky) |

`limit` gets its own clamp, not the shared `clampLimit` (50/200): an ad row
carries two URL arrays and a body, so 200 of them is a far bigger response than
200 gem rows.

Returns status, the progress event feed, and the ranked ads. Each ad carries:
`ad_archive_id`, `page_name`, `page_id`, `source_term`, `days_running`,
`is_active`, `collation_count`, `page_like_count`, `cta_text`, `link_url`,
`body_text`, `image_urls`, `video_urls`, and a `library_url` permalink
(`https://www.facebook.com/ads/library/?id=<ad_archive_id>`) so a human can open
the ad as Meta renders it.

Paging note when `len(results) == limit`, same wording discipline as
`hunt_status`.

## Ranking

Read-time, so the same scrape can be re-cut with different knobs without paying
for another one. `days_running` is computed, not stored:

```sql
GREATEST(0, EXTRACT(day FROM (COALESCE(end_date, now()) - start_date))::int)
```

The per-advertiser cap and the sort are one window pass:

```sql
ROW_NUMBER() OVER (PARTITION BY page_id ORDER BY days_running DESC, id) <= :per_advertiser
ORDER BY days_running DESC
```

`min_days` and `media` filter **before** the window function, so the
per-advertiser cap counts only ads that qualify. Filtering after would let an
advertiser's three longest-running image ads consume its whole quota and then
be dropped by `media: video`, returning nothing from a page that has video ads.

Rationale: longevity is the strongest free evidence an ad works — nobody pays to
run a loser for 90 days — and the per-advertiser cap stops one big spender from
owning the page. Rows with a NULL `start_date` sort last (`days_running` = 0).

## Data

Migration `db/schema/166_ad_searches.sql`. No bare `%` anywhere in the file
(apply_migrations is placeholder-sensitive).

**`frontend.ad_searches`** — id, created_by text NOT NULL, terms jsonb NOT NULL
DEFAULT '[]', competitors jsonb NOT NULL DEFAULT '[]', settings jsonb NOT NULL
DEFAULT '{}', intent text, status text NOT NULL DEFAULT 'queued'
(queued|running|done|error), progress jsonb NOT NULL DEFAULT '{}', error text,
created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz.

**`frontend.ad_search_results`** — id, search_id FK → ad_searches ON DELETE
CASCADE, ad_archive_id text NOT NULL, page_id text, page_name text, source_term
text, is_active bool, start_date timestamptz, end_date timestamptz,
collation_count int, page_like_count bigint, cta_type text, cta_text text,
link_url text, body_text text, image_urls jsonb NOT NULL DEFAULT '[]',
video_urls jsonb NOT NULL DEFAULT '[]', first_seen timestamptz NOT NULL DEFAULT
now(). `UNIQUE (search_id, ad_archive_id)`, index on `(search_id)`.

`start_date`/`end_date` arrive from Meta as unix seconds and are stored as
timestamptz (`to_timestamp`), so `days_running` is plain SQL date math.

The same ad found by two terms upserts once; `source_term` keeps the first
term that found it (`ON CONFLICT DO UPDATE` refreshes the volatile fields —
`is_active`, `end_date`, `collation_count`, media URLs — and leaves
`source_term` alone).

## Execution

`find_ads` inserts the `ad_searches` row and the `pipeline_jobs` row in **one
transaction**, exactly as `InsertHuntRoundTx` does, with `kind='ad_search'` and
`search_id` in the `advertiser_id` column. The partial unique index on
`(kind, advertiser_id) WHERE status IN ('queued','running')` dedups; the
search_id namespace is per-kind so it cannot collide with hunt round ids.

**The hunt daemon gains a second lane** rather than becoming a ninth VM
service. The VM's compose ships inside the image and the deploy script's
service list is stale (memory 2026-08-06), so a new service is a deploy hazard
for no benefit: the ad_search lane wants the same proxy pool, the same image,
and the same process. A separate thread lane means an ad search is claimed
immediately instead of queueing behind a 90-minute hunt.

`src/agf/discovery/ad_search.py` — the executor:

1. Competitors first (phase 0), reusing hunt's fuzzy typeahead disambiguation:
   rank the top 5 typeahead rows by `difflib` similarity to the entered name
   with likes as tiebreak, try the best 3 until one yields ads. Meta's typeahead
   ranks big pages first, so blind `[0]` picks look-alikes (live finding
   2026-07-15). Competitor pulls are global (`HUNT_COMPETITOR_COUNTRIES`);
   term sweeps are US-only.
2. Then each term, paging to `pages` with the existing 2s `HUNT_PAGE_DELAY`.
3. Every `AdRecord` from every page upserts into `ad_search_results` in that
   page's own short transaction — durable the moment it lands, no transaction
   held across a network call.
4. Progress events written the same way hunt writes them (whole-blob flush
   under a lock, capped event list).

No `classify_candidate`, no `run_triage`, no `judge_round`, no `enqueue_enrich`,
no `add_trial_keywords`, no `fold_web_funnel`.

`src/agf/discovery/ad_search_one.py` — the subprocess entry point, mirroring
`hunt_one`: one SIGALRM scrape budget (there is no phase-5 finish to protect),
terminal job status stamped on every path, partial results kept on timeout.
Lease > subprocess timeout > SIGALRM budget, the invariant hunt_daemon already
documents.

## Errors and partials

Per-term isolation, as in hunt: an `ExtractError` on one term records an event
and moves to the next, it does not fail the search. A search that times out mid
scrape is marked `done` with `progress.partial = true` and keeps everything
already committed — every page is its own transaction, so there is nothing to
roll back. Only a non-timeout failure marks the search `error`.

The daemon's existing "still running after the runner returned" backstop
applies unchanged: if the subprocess dies without stamping status, the daemon
stamps `error` on both the job and the search.

## Staleness

fbcdn URLs are signed and expire in hours. `ad_search_status` computes the age
of the search and, past 6 hours, returns a note saying the media URLs may have
expired and that re-running `find_ads` refreshes them. The ad text, dates and
counts stay valid regardless — only the media links rot.

This is the accepted cost of not mirroring bytes. If it becomes a real problem,
the fix is a later increment that pipes results through the existing
`download_creative` path; nothing here forecloses it.

## Cost

3 terms × 5 pages = 15 fetches at 2s spacing ≈ 1–2 minutes, roughly 450 ads
before dedup. The default `pages: 5` balances pool size against wait: Meta does
not sort by longevity, so depth widens what the ranking has to choose from.

## Testing

Python, against `src/agf/extract/fake.py`:

- The executor persists every ad from every page, including ads
  `classify_candidate` would have dropped (no store link, web landing).
- **The zero-side-effects invariant, asserted as a mutation check**: snapshot
  the row counts of `discovery.candidates`, `public.advertisers`,
  `discovery.funnel_candidates`, `discovery.dynamic_keywords` and
  `public.pipeline_jobs WHERE kind='enrich'` before and after a full run, and
  assert each is unchanged. Asserting only that the results table filled would
  pass while the corpus was being polluted.
- A term raising `ExtractError` does not stop the remaining terms.
- Re-running a search over the same ad updates the volatile fields and keeps
  `source_term`.
- Ranking: per-advertiser cap and `days_running` ordering, including the NULL
  `start_date` case.

Go, following `backend/mcpserver/mcpserver_test.go` and
`backend/internal/integration/mcp_test.go`:

- Non-admin is refused by both tools (assert the message, not just `IsError` —
  memory 2026-08-06 on tests that pass for the wrong reason).
- Bounds: over-cap terms/competitors rejected, blank-only input rejected,
  `pages`/`limit` clamped.
- `find_ads` writes both the search row and the job row, and stores `[]` rather
  than `null` for the omitted side.
- `ad_search_status` on an unknown search_id returns "not found".

Live gate (a scrape and an MCP round trip are two real components, so unit
green does not close this):

- One real `find_ads` against prod with one term, `pages: 2`.
- Poll `ad_search_status`; assert ads came back with non-empty media URLs.
- `curl -sI` a returned image URL and a returned video URL from this machine:
  expect 200 and an image/video content-type. This is the one claim the whole
  no-mirroring decision rests on, and it has only been verified by reading
  `_default_fetch`, not by fetching.
- Assert the corpus counts are unchanged in prod, same five tables.

## Naming

`find_ads` / `ad_search_status`. `run_ad_search` would sit more symmetrically
beside `run_hunt`, but `find_ads` reads more clearly in a tool list where the
model is choosing between "find apps" and "find ads".
