# Add a gem from an Ad Library link — design

Date: 2026-08-06
Status: approved (entry point + veto handling confirmed by the founder)

## The ask

Paste a Meta Ad Library link for an advertiser into Field Desk and have that
advertiser scraped, triaged, enriched, scored and surfaced as a gem — without
waiting for a keyword sweep to stumble across it.

Two founder decisions, already made:

1. **Entry point** — a button in the Discover header.
2. **Veto handling** — a pasted page is added and surfaced even when triage or
   scoring would veto it; the reason stays visible rather than the page
   disappearing.

## What already exists (this feature is mostly wiring)

| Need | Existing code |
| --- | --- |
| Pull one page's US ads | `Extractor.search(search_type="page", view_all_page_id=…, countries=["US"])` — `discovery/bridge.py` |
| Full ad set + 16-week series for one advertiser | `discovery.keeper_history.refresh_one_advertiser` |
| Ads → app candidates | `run_bridge`'s `classify_url` loop — `discovery/bridge.py` |
| Store-listing triage scoped to my own candidates | `run_triage(conn, …, only_ids=[…])` — `discovery/triage.py` |
| Advertiser upsert keyed on page id | `repo.upsert_advertiser` (`ON CONFLICT (page_id)`) |
| Enrich + score + matview refresh for ONE advertiser | `enrich_full/enrich_one.run_job` with `_default_enrich` / `_default_score` |
| Job queue + daemon + one-shot subprocess contract | `public.pipeline_jobs`, `keeper_daemon.py`, `keeper_one.py` |
| Enqueue-and-poll API shape | `POST/GET /api/enrich/:advertiser_id` — `enrich_controller.go` |
| Surfacing a vetoed-but-wanted advertiser | matview `WHERE (s.vetoed = FALSE OR s.veto_reason = 'longevity_below_floor')` + `incubating` flag — `db/schema/125_…sql` |

Nothing here needs a new daemon, a new queue, or a new scrape primitive.

## Architecture

```
Discover header button
        │  POST /api/gems/add {url}
        ▼
Go API ── parse page id ── upsert_advertiser(page_id) ── enqueue pipeline_jobs
        │                                                  kind='add_page'
        │  returns {advertiser_id, job_id, already_known}
        ▼
Web polls GET /api/gems/add/:job_id
                                    ▲
                                    │ status + phase + result
        keeper daemon (existing) ───┘
              claims kind='add_page' at top priority
              runs: python -m agf.discovery.add_page_one
                      1. refresh_one_advertiser  → ads + series + page_name
                      2. ads → app candidates    (bridge's classify_url loop)
                      3. run_triage(only_ids=…)  → keep/veto verdict recorded
                      4. mark advertisers.manual_add = TRUE
                      5. enrich_one.run_job(…)   → enrich + score + matview refresh
```

### 1. Link parsing (Go, `helpers/adlibrary.go`)

`ParsePageID(raw string) (pageID string, err error)` accepts:

- `https://www.facebook.com/ads/library/?…&view_all_page_id=123456…` — the
  primary form (Ad Library's "See all ads from this advertiser" URL). Query
  param only; any other params ignored.
- A bare numeric page id (`123456789`), so a copied id works too.
- `https://www.facebook.com/ads/library/?id=<ad_archive_id>` — a single-ad
  permalink. Resolved by DB lookup: `public.ads.external_ad_id → advertiser_id →
  page_id`. If we have never seen that ad, this returns a specific error (below)
  rather than a Meta call — `AdLibraryAdDetailsV2Query` does not return a page id
  (verified in `extract/ad_details.py`: `AdDetails` carries only reach + active),
  so resolving an unseen ad id would need new scraping work that this increment
  does not take on.

Everything else returns a typed error the UI renders verbatim. Error cases and
their messages (final copy runs through the humanizer + impeccable pass):

- not a facebook.com/ads/library URL → tell them what to paste
- an ads/library URL with neither `view_all_page_id` nor `id` (e.g. a keyword
  search URL) → tell them to open one advertiser first
- an `id=` link for an ad we do not have → tell them to click through to the
  advertiser and copy that URL instead

Parsing is a pure function with no DB access except the `id=` branch, which
takes a repo lookup as a dependency so it stays unit-testable.

### 2. API (`gems_controller.go`, routes in `routers/routers.go`)

`POST /api/gems/add` — token-guarded (`middlewares.RequireToken()`, same as
every other write).

Body `{"url": "..."}`. Flow:

1. `ParsePageID` → 400 with the typed message on failure.
2. Upsert `public.advertisers` on the page id. A brand-new page gets a row with
   `page_name` NULL — the scrape fills it in. An already-known page returns its
   existing id.
3. Insert one `pipeline_jobs` row, `kind='add_page'`, `advertiser_id=<id>`.
   Guard: if a `queued`/`running` `add_page` job already exists for this
   advertiser, return that job instead of creating a second one.
4. `201 {advertiser_id, job_id, status, already_known}`.

`GET /api/gems/add/:job_id` — poll. Returns `{status, phase, advertiser_id,
page_name, icon_url, ad_count, app, triage_note, error}`. `status` is the
`pipeline_jobs` status (`queued|running|done|error`); `phase` is a short label
the job writes as it advances (see below). On `done` the payload carries enough
to render a result card without a second request.

Idempotency: pasting a page already in the corpus re-scrapes it and returns
`already_known: true`. That is the useful behavior — it doubles as a manual
refresh for a page you care about.

### 3. Worker: `kind='add_page'`

**Consumer — the existing keeper daemon**, not a new service. `keeper_daemon.KINDS`
becomes `("add_page", "keeper_history", "corpus_refresh")`: a founder-initiated,
someone-is-watching job outranks both background lanes. `run_keeper_subprocess`
branches on kind to pick the CLI module. This deliberately avoids adding a
seventh service to `ops/prod/docker-compose.yml` — the class of bug that shipped
a missing `enrich` service to prod on 2026-08-05.

**Body — `src/agf/discovery/add_page_one.py`**, a one-shot CLI mirroring
`keeper_one.py`'s contract exactly: `--advertiser-id N --job-id M`, own terminal
status on every exit path, SIGALRM budget, `logging.crawl_log` marker
(`entity_type='add_page'`) on failure so a bad page cannot hot-loop.

Steps, each writing its `phase` label to the job row so the UI can narrate:

1. `phase='pulling ads'` — `refresh_one_advertiser(engine, extractor,
   advertiser_id, page_id, flip_vanished=False)`. For a brand-new page this is
   the initial pull; it also fills in `page_name` and `page_like_count`.
   If the page has zero ads, stop here with a `done` status and an explicit
   "this page is not running any US ads" result — not an error.
2. `phase='reading the ads'` — emit app candidates from the persisted ads using
   `classify_url`, the same loop as `run_bridge`. Web-only ads (no store id)
   emit no app candidate; the advertiser still stands and the existing web-funnel
   path handles it downstream.
3. `phase='checking the store listing'` — `run_triage(conn, lookup_ios=…,
   lookup_android=…, only_ids=<this page's candidate ids>)`. Scoped, so a paste
   never queues behind the global `status='new'` backlog. The verdict (kept, or
   vetoed with a reason) is recorded, not acted on as a rejection.
4. `phase='adding'` — set `public.advertisers.manual_add = TRUE`.
5. `phase='enriching'` — `enrich_one.run_job(engine, advertiser_id=…,
   job_id=…, enrich_call=_default_enrich, score_call=_default_score,
   status_table='pipeline_jobs')`. `_default_score` already scores this one
   advertiser and calls `refresh_emerging_matview`, so the gem is live when the
   job reports done — no waiting on the `*/15` compute cycle.

Budget: SIGALRM at `AGF_ADD_PAGE_TIMEOUT_SEC` (default 600), with the daemon's
subprocess hard-kill padded above it, following `keeper_one`'s pattern.

### 4. Force-keep (migration 165)

Two separate vetoes exist and both must be handled, or a pasted page silently
vanishes:

- **Triage veto** (`triage.triage_decision`) — candidate-level. Handled by step 3
  recording the verdict without dropping the candidate.
- **Scoring veto** (`run_scoring.score_all` → `adtech.advertiser_scores.vetoed`)
  — advertiser-level, and the matview filters on it. Handled by the migration.

Migration 165:

- `ALTER TABLE public.advertisers ADD COLUMN manual_add BOOLEAN NOT NULL DEFAULT FALSE`
- Recreate `frontend.emerging_scaling_apps` with the WHERE clause extended to
  `WHERE (s.vetoed = FALSE OR s.veto_reason = 'longevity_below_floor' OR a.manual_add)`,
  and expose `a.manual_add` plus the existing `s.veto_reason` as matview columns
  so the UI can render the flag.

This mirrors the `incubating` precedent exactly: surfaced, marked, reason
visible. The veto thresholds themselves are untouched, so the automated sweeps
keep filtering as they do today.

Gem detail and the Discover feed render a small marker on a manually-added
advertiser that carries a veto reason — e.g. "Added by you · no app store
listing found". A manually-added advertiser that passes triage cleanly gets no
marker; it is just a gem.

### 5. UI (`web/src/app/discover`, `web/src/components/discover`)

A first-class button in the Discover header, sized like the surface's existing
primary control — not a text link. Opens a small paste field inline.

States: idle → submitting → running (rendering the job's `phase` label as it
advances) → done (result card: icon, name, active ad count, the app if one was
found, the triage note if flagged, and a link straight to the gem page) or error
(the typed parser message, or the job's error).

Polling reuses the enrich-status cadence already in the codebase. The panel
survives a navigation away and back by keying on the job id in local state, the
same way the enrich refresh does.

All strings — button label, placeholder, phase labels, the three parser errors,
the flag marker, the empty "no US ads" result — go through the humanizer skill
and an impeccable pass before they ship. They are the user-facing surface of
this feature and the project's copy rule is plain, clear, professional.

## Testing

**Go unit** — `ParsePageID` over every accepted form (canonical URL, extra
params, URL-encoded, bare id, `id=` with a known ad, `id=` with an unknown ad)
and every rejected form (wrong host, ads/library search URL, garbage, empty).

**Go integration** — `POST /api/gems/add` creates exactly one advertiser row and
one `add_page` job; a second paste of the same URL returns the same advertiser
with `already_known: true` and does not create a second queued job; an unparseable
URL is a 400 that writes nothing; `GET` returns the job's status.

**Python** — `add_page_one` against `extract/fake.py` (its `page_ads` map is
already keyed by page id): a brand-new page becomes an advertiser with ads and a
kept candidate; a page whose app would be vetoed still ends `manual_add=TRUE`
with the veto reason recorded; a page with zero ads finishes `done` with the
empty result, not `error`; a scrape failure writes the terminal error status
**and** the crawl_log marker.

**Migration** — the recreated matview returns a manual-add advertiser whose
score row is `vetoed=TRUE`, and still excludes a non-manual vetoed advertiser.

**Live e2e (required — this spans API, DB, browser and a subprocess)** — paste a
real Ad Library link on prod, watch the phases advance, confirm the gem page
renders. Per CLAUDE.md, mock greens do not satisfy this gate.

## Deliberately out of scope

- Resolving an unseen single-ad `id=` link to its page (needs new scraping work;
  the error message points at the right link instead).
- Resolving a `facebook.com/<pagename>` vanity URL via Meta's typeahead.
- Bulk paste / CSV import.
- Any change to the veto thresholds themselves.

## Deploy order

1. Apply migration 165 (adds the column, recreates the matview).
2. Deploy the worker image and recreate the `keeper` container — `KINDS` is
   read at import, and `docker restart` reuses creation-time env.
3. Deploy the API.
4. Deploy web.

Step 2 recreates a container rather than restarting it, per the docker-env
lesson. No new compose service is introduced, so there is nothing to `up -d`
separately.
