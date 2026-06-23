# Web-sub funnel feed surfacing — design

**Date:** 2026-06-23
**Status:** Approved design, pre-implementation
**Author:** brainstorming session (founder + Claude)

## Problem

The funnel pipeline confirms digital subscription funnels and classifies their
payoff (`discovery.funnel_candidates.payoff_kind`, migration 139). There are
currently **22 confirmed `web_sub` funnels** — digital subscription products
sold web-to-subscribe rather than via an app store. None of them appear in the
Discover feed.

Investigation (2026-06-23) established the gap precisely:

- These 22 pages have a `page_id` (captured from the funnel ad) but **none are
  in `public.advertisers`** (`page_in_advertisers = 0`) and **none have a
  scraped ad corpus** (`pages_with_ads = 0`). They entered only through the
  funnel keyword sweep, were confirmed by probing their landing page, and were
  never enriched as advertisers.
- The Discover feed (`frontend.emerging_scaling_apps`) is built from
  `adtech.advertiser_scores` — so anything in the feed is a scored advertiser.
  Web-sub funnels never reached that pipeline.
- The earlier hypothesis that web-sub funnels overlap with the 67 `no_app`-vetoed
  advertisers was **false** — those 67 are physical/DTC/service/hardware brands
  (Olay, CeraVe, Graza, Natural Cycles devices, Tempo, BACtrack, PHD Weight
  Loss, BetterHelp) and are correctly excluded. The web-sub set is disjoint.

Of the 22 confirmed web_sub funnels, **8 are emerging** (`page_like_count <
5000`): Sleep Tight (`trysleeptight.app`, 0 likes), Bossed AI (`app.bossed.ai`,
1), Emma Collins / formula.care variants (122), Ultiself (628), Facet, Anna's
IF (`ifasting.best.me`), Olivia Harper. The rest are incumbents (Marisa Peer
472k, Acquisition Ace 185k, Prose 79k, Stephen Cabral 74k, Learn Polish 61k,
Osteo Yoga 46k, Swimply 43k) that the existing incumbent vetoes will drop once
scored.

## Goal

Surface confirmed `web_sub` funnels in the **main Discover feed**, enriched to
look like real entries — their own icon and name/description sourced from the
web (not an app store) — tagged with a **"Web only"** badge. They are ranked by
the same GemScore as apps so they sort fairly, with the emerging filter handled
automatically by the existing incumbent vetoes.

## Key facts that make this cheap

The existing pipeline already tolerates app-less advertisers:

- **Enrich** (`src/agf/enrich_full/run_enrich.py`, `enrich_gem`): only does an
  app-store lookup when `target.kind in ("ios","android") and target.store_id`.
  Otherwise it skips the store lookup and just scrapes the Meta ad corpus. An
  app-less web-sub advertiser enriches fine (ads only).
- **GemScore** (`src/agf/scoring/gemscore.py:65-80`): `gem_score(sv, ur, fi, d,
  w, floor=0.5)` imputes any axis passed as **`None`** to the cohort-neutral
  floor (0.5), not 0. So a web-sub entry with no Demand (no installs) and no
  User-Reviews scores on SV/FI/W with those axes neutral — provided the scoring
  path passes `None`, not a computed `0`.
- **Triage** (`src/agf/discovery/triage.py:22`): NULL/unknown fields always
  pass, so app-less candidates survive triage.
- **Incumbent vetoes** (`src/agf/scoring/vetoes.py`): `incumbent_audience` /
  `established_traction` already drop high-like / high-traction pages, so no new
  like-count ceiling is needed — the emerging tier falls out automatically.

The new code is: the bridge stage, the web-sub marker, the `no_app` veto
exception, the scoring axis handling, the web-identity fetch + storage, the
matview COALESCE, and the UI badge.

## Data flow

```
discovery.funnel_candidates (payoff_kind='web_sub', status='confirmed')
        │  ① web-sub bridge stage (NEW)
        ▼
  scrape the page's Meta ad listing by page_id → persist advertiser + ads,
  tagged web-sub; fetch OG/favicon identity from landing_url
        │  ② enrich (EXISTING — skips app lookup when no store_id, scrapes ads)
        ▼
  ③ score (EXISTING — GemScore imputes Demand/User-Reviews to neutral floor)
        │     + no_app veto exception for web-sub (NEW)
        │     + incumbent vetoes still apply → drop Marisa Peer (472k) etc.
        ▼
  ④ matview → Discover feed, COALESCE web icon/name/desc + "Web only" badge (NEW)
```

## Components

### 1. Web-sub bridge stage
`src/agf/discovery/websub_bridge.py` (new), wired into the worker cycle in
`src/agf/workers/main.py`.

For each `funnel_candidates` row where `payoff_kind='web_sub' AND
status='confirmed'` whose `page_id` is not yet in `advertisers`:

1. Scrape that page's Meta ad listing by `page_id` (reuse the existing
   advertiser ad-scrape path used by the sweep/refresh), persist advertiser +
   ads.
2. Set `advertisers.web_payoff_kind = 'web_sub'`.
3. Fetch web identity from the funnel's `landing_url` (see §4) and store it.

Bounded per cycle via `AGF_WEBSUB_BRIDGE_PER_CYCLE` (default 10), mirroring the
other per-cycle stage caps. Idempotent: a page already an advertiser is skipped.

### 2. Web-sub marker
A boolean/text column **on `advertisers`** — `web_payoff_kind` (nullable text,
NULL for normal app advertisers, `'web_sub'` for bridged web-sub pages). Chosen
over a join on `funnel_candidates.payoff_kind` because the `no_app` veto runs in
Python (`vetoes.py`) where a SQL join is awkward; a column read is clean in both
the veto and the matview. One migration.

### 3. `no_app` veto exception
`src/agf/scoring/vetoes.py` — `no_app(fv)` returns `False` (do not veto) when the
advertiser's `web_payoff_kind` is set. The feature vector `fv` must carry
`web_payoff_kind` (threaded from `run_scoring.py`). All other vetoes
(`incumbent_audience`, `established_traction`, `game`, `incumbent_ad_volume`,
`relaunch`) still apply.

### 4. Scoring axis handling
`src/agf/scoring/run_scoring.py` — for web-sub advertisers
(`web_payoff_kind is not None`), pass `d=None, ur=None` into `gem_score(...)`
(not a computed `0`) so the neutral 0.5 floor applies and the entry ranks on
SV (scaling velocity from ad running-days), FI (funnel intent), and W
(whitespace).

### 5. Web identity (icon + info)
Folded into the bridge stage. From the funnel's `landing_url`, fetch the page
and parse OG/meta tags (reuse the funnel-pipeline page fetch):

- `og:title` → `web_name` (fallback to `page_name`)
- `og:description` / `<meta name=description>` → `web_description`
- `og:image` / favicon → `web_icon_url`
- store `web_landing_url` = the confirmed funnel landing URL

Stored as nullable columns **on `advertisers`**: `web_name`, `web_description`,
`web_icon_url`, `web_landing_url` (plus `web_payoff_kind` from §2). Parser
degrades gracefully when tags are missing (falls back to `page_name`, null
description, null/last-resort favicon-by-domain).

### 6. Matview + UI badge
- `frontend.emerging_scaling_apps` (new migration): include web-sub advertisers;
  `COALESCE` app name/icon/description with the `web_*` columns so a web-sub row
  renders with its favicon + OG copy. Add a `web_only` boolean (true when
  `web_payoff_kind` is set).
- `GemTable` (web): when `web_only`, render the favicon in the icon slot and a
  small **"Web only"** badge next to the name; app-only columns (installs,
  reviews) show "—". The Quiz/Funnel link points at `web_landing_url`. Copy
  stays plain per the Field Desk voice rule (no marketing fluff).

## Testing

Full suite must stay green; DB tests via
`AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it python -m pytest -q`.

**Unit**
- `no_app` veto returns `False` for a web-sub advertiser, `True` otherwise.
- `gem_score` with `d=None, ur=None` imputes the 0.5 floor (no zeroing); a
  web-sub with strong SV scores well, not ~0.
- OG/favicon parser extracts name/desc/icon and degrades gracefully on missing
  tags.

**DB / integration**
- Bridge upserts an advertiser tagged `web_payoff_kind='web_sub'` from a
  confirmed web_sub funnel; re-run is idempotent (no dupes).
- Matview surfaces a web-sub row with `web_only=true` and COALESCE'd identity.
- Incumbent veto still drops a high-like (`page_like_count` large) web_sub page.

**Live end-to-end** (real-deps gate)
- Run the bridge against the real DB for the 22 confirmed web_sub pages; confirm
  advertisers + ads land, scores compute, and the emerging tier (Sleep Tight,
  Bossed AI, Ultiself, Emma Collins, Facet) appears in the feed while Marisa
  Peer / Acquisition Ace / Prose are vetoed.

## Non-goals (YAGNI)

- No separate web-sub feed or page — they go in the main Discover feed.
- No like-count ceiling — reuse the existing incumbent vetoes.
- No payoff classes beyond `web_sub` — physical / leadgen / service stay
  excluded.
- No historical growth backfill — ad-velocity accrues going forward like any
  new advertiser.
- No changes to funnel-pipeline discovery itself.

## Affected files

- `src/agf/discovery/websub_bridge.py` (new)
- `src/agf/workers/main.py` (wire stage + `AGF_WEBSUB_BRIDGE_PER_CYCLE`)
- `src/agf/scoring/vetoes.py` (no_app exception)
- `src/agf/scoring/run_scoring.py` (thread `web_payoff_kind`; pass `d/ur=None`)
- `src/agf/scoring/features.py` (carry `web_payoff_kind` on the feature vector)
- migration: `advertisers.web_payoff_kind`, `web_name`, `web_description`,
  `web_icon_url`, `web_landing_url`
- migration: `frontend.emerging_scaling_apps` rebuild (COALESCE identity +
  `web_only`)
- `web/` `GemTable` + the API/matview adapter (surface `web_only`, web identity)
- tests across the above
