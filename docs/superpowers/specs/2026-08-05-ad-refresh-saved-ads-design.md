# On-demand ad re-scrape + saved ads — design (2026-08-05)

Two features, one increment:

- **A. Fresh ads on demand** — the dossier's "Refresh data" button actually
  re-scrapes the gem's Meta Ad Library listing (today it only re-enriches the
  ads already stored; new ads never appear, vanished ads never flip).
- **B. Saved ads** — individual ads get a save heart like gems do, and a
  place to browse them on /saved.

## Measured baseline (verified live in-repo 2026-08-05)

- `enrich_gem` (src/agf/enrich_full/run_enrich.py:257) only `SELECT`s
  `public.ads` — no listing fetch. The Refresh-data button therefore never
  surfaces new ads.
- The per-advertiser scrape primitive exists and is battle-tested:
  `refresh_one_advertiser(engine, extractor, advertiser_id, page_id, *,
  hist_pages=12, weeks=16, flip_vanished=False)`
  (src/agf/discovery/keeper_history.py:85) — pulls the listing via
  `extractor.search_listing(page_id=…)`, `upsert_ad` + `ensure_text_creative`
  per ad (idempotent on `ads.external_ad_id` UNIQUE), refreshes 16 weekly
  `app_advertiser_metric` rows, and with `flip_vanished=True` sets
  `is_active=false` on ads absent from the fresh listing.
- Saved-gems template: `frontend.saved_gems` (migr 102/114) with denormalized
  snapshot columns, PK (user_email, advertiser_id); Go controller takes a
  client-sent snapshot body (`SavedGemIn`); proxy allowlist in
  `web/src/app/backend/[...path]/route.ts` gates each top-level resource.
- Highest migration in-tree: 163. Next free: **164**.
- `ads.external_ad_id` (Meta ad_archive_id, TEXT, UNIQUE) is the only ad id
  that reaches the browser (`Creative.external_ad_id`) and survives
  re-scrapes — it is the save key. `ads.id` / `creatives.id` are not exposed
  and creatives dedupe by sha256, so neither is usable.

## A. Fresh ads inside the existing enrich flow

Decision: **no new button, endpoint, or job kind.** The existing chain
(POST /api/enrich/:advertiser_id → `public.enrich_jobs` → VM `enrich`
queue_daemon → `enrich_one` subprocess) gains a scrape phase. Fewest
elements; the existing control becomes honest.

Mechanism, in `run_enrich.py` at the top of the enrich flow (exact seam
chosen in the plan — before the `public.ads` SELECT):

1. Look up the advertiser's `page_id`. If NULL, skip the phase (current
   behavior, e.g. web-only funnels).
2. Build the Meta extractor (the `enrich` compose service runs the worker
   image + env, so `AGF_PROXIES` etc. are present — plan verifies with a
   grep of ops compose files) and call
   `refresh_one_advertiser(…, hist_pages=AGF_ENRICH_ADS_PAGES (default 12),
   flip_vanished=True)`.
3. **Fail-soft + budgeted**: the phase runs under
   `asyncio.wait_for(AGF_ENRICH_ADS_TIMEOUT, default 120s)`; any exception
   or timeout logs and falls through to enriching the stored ads. A Meta
   outage must never fail the enrich job (it didn't before this change).
4. Then the existing enrich proceeds and now reads the fresh rows (recency
   ordering picks up the new ads naturally).

Timeout ledger (constants are cross-referenced between codebases — do not
drift): Go `enrichTimeout` 540s ≥ queue pickup 120s + enrich `--timeout`
300s + margin. The scrape phase lives INSIDE the 300s enrich budget; 120s
scrape cap + ~110s median enrich fits. No constant changes.

Races: a concurrent `corpus_refresh` job on the same advertiser is benign —
both paths upsert on `external_ad_id` and `flip_vanished` only touches ads
absent from its own fresh listing.

UI: unchanged. `RefreshDataButton` already refetches the dossier on
completion, so new ads appear when the job finishes.

## B. Saved ads

### Schema — `db/schema/164_saved_ads.sql`

```sql
CREATE TABLE IF NOT EXISTS frontend.saved_ads (
    user_email      TEXT NOT NULL,
    external_ad_id  TEXT NOT NULL,
    advertiser_id   BIGINT,          -- for "open gem" nav; no FK cascade so the
                                     -- saved ad survives advertiser pruning
    page_name       TEXT,
    app_name        TEXT,
    title           TEXT,
    body_text       TEXT,
    media_type      TEXT,            -- Creative.type, drives card rendering
    media_s3_key    TEXT,            -- our stored media key; the client derives
                                     -- the display URL via the media proxy
                                     -- (Meta CDN URLs expire, so no URL column)
    link_url        TEXT,
    cta_text        TEXT,
    ad_started_on   DATE,
    ad_active       BOOLEAN,
    saved_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_email, external_ad_id)
);
CREATE INDEX IF NOT EXISTS saved_ads_user_saved_idx
    ON frontend.saved_ads (user_email, saved_at DESC);
```

Snapshot columns are denormalized (saved_gems precedent): a saved ad still
renders if the underlying ad row is pruned or re-imported. Client sends the
snapshot in the save body (`SavedAdIn`), exactly like `SavedGemIn`.

### Go API (mirror of saved_gems: model + repo + controller + routes)

- `GET  /api/saved-ads?user_email=…` → list, newest first
- `POST /api/saved-ads/:external_ad_id` (RequireToken, body = SavedAdIn) → upsert
- `POST /api/saved-ads/:external_ad_id/remove` (RequireToken) → delete
- `external_ad_id` is TEXT — no Atoi; validate non-empty.
- Proxy allowlist: add `"saved-ads"` to `ALLOWED_RESOURCES`.

### Frontend

- **Store**: `web/src/lib/savedAds.ts` — small module-level store like
  saved.ts but flat (rows + Set of external_ad_id, subscribe/getSnapshot,
  optimistic save/unsave with revert on API error). No folders, no likes —
  ads have no rating system.
- **Heart on ad cards**: dossier `EvidenceReel` `AdCard` gets the same heart
  control as gem cards (same size/visual as the FolderPicker heart variant),
  rendered only when `c.external_ad_id` is present. Press toggles saved.
  No picker menu. aria-labels: "Save ad" / "Remove from saved".
  Review deck stays untouched (same call as the gems rollout).
- **/saved**: a pinned "Ads" tile in `FolderTiles` next to Liked
  (client-derived count), navigating to `?folder=ads` (history.replaceState,
  same as Liked). That view renders the saved-ads grid — same
  `repeat(auto-fill, minmax(230px,1fr))` grid, cards reusing the AdCard
  rendering (media/title/body) plus page name linking to the gem dossier and
  an unsave heart. Empty state: "No saved ads yet. Save one with the heart
  on a gem's ads."

### Copy (humanizer-audited at draft time)

- Tile label: `Ads`
- Empty state: `No saved ads yet. Save one with the heart on a gem's ads.`
- Heart aria-labels: `Save ad` / `Remove from saved` (existing string reused)
- Error toast on failed save (matches gems): `Couldn't save.`

## Out of scope (v1, deliberate)

- Folders / multi-membership for ads (template exists in
  `saved_group_members` if wanted later).
- Hearts on the review deck.
- Hosted-MCP exposure of saved ads.
- Raising the dossier's TOP_ADS display cap.

## Verification gates

- Python: pytest unit coverage for the scrape phase (page_id-less skip,
  fail-soft on scrape error, wait_for budget) with a faked extractor; full
  suite to 0 failed on AGF_TEST_DB_URL.
- Go: integration tests for saved-ads list/upsert/remove + ownership scoping
  on agf_it; full `go test ./...` to 0 failed.
- Web: lint + build; save/unsave + tile flow driven in a browser against the
  local stack (harness per saved-board memory: API on 8010, web on 3200).
- Live (post-deploy): trigger Refresh data on a prod gem with a page_id and
  confirm `public.ads` gains/updates rows for that advertiser and the job
  completes inside its deadline; save an ad and read it back via
  `/api/saved-ads`.
