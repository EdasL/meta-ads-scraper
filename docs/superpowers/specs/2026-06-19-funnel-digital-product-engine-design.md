# Funnel pipeline → digital-product engine (payoff classifier + app bridge)

**Date:** 2026-06-19
**Status:** approved (scope locked with user)

## Problem (measured this session)

The funnel pipeline already discovers + renders 260 confirmed funnels, but:

1. **The app bridge is broken.** `discovery.funnel_candidates.store_id` is populated on
   **0 / 260** confirmed rows — even the 69 `kind='install'` ones. Root cause (verified):
   `funnel_confirm.classify_funnel` detects a `store` signal in the *rendered* HTML
   (funnel_confirm.py:44) but returns `store_id=None`; it only reads `store_id` from the
   deeplink resolver, never extracts it from the rendered DOM. So every web2app funnel
   whose store link appears only on the landing page loses its app.
2. **No digital/physical distinction.** The pool mixes web2app + web-subscription apps
   (BetterMe, Bioma, reverse.health) with physical-product (Shopify/supplements) and
   service/booking (Calendly/real-estate) funnels. The user wants **digital only**.

Rendered probe (`scripts/probe_funnel_render_reserve.py`) confirmed the digital-vs-physical
signals are all present in the rendered DOM, and that a strict app-store reserve is ~21
across the 260 (the rest of the value is web_sub classification + excluding physical).

**Not a directory problem.** Step-0 verification found there is **no funnel directory**:
funnels reach the user only as (a) the per-app `funnel_url` "Quiz link" in the matview,
sourced from `public.funnels` (app-centric, already excludes store/social/MMP via
migration 138 — a *separate* table from `discovery.funnel_candidates`), and (b) a single
**"confirmed funnels" count** in the dashboard (`metrics_repo.go:110`). So "filter the
directory" is moot; "exclude physical" becomes a property of the classifier + the count.

## Scope (locked)

IN: payoff classifier on confirmed funnels; fix the app bridge (DOM store_id extraction +
confirm-time bridge); exclude physical/service/leadgen from bridging; the dashboard
"confirmed funnels" count becomes digital-only (`app`+`web_sub`); backfill the existing 260.

OUT (explicit fast-follow): surfacing `web_sub` (app-less) products as first-class entities
in the app-centric feed — needs its own product decision. This increment only **classifies
and tags** them.

## Design

### Data: migration `139_funnel_payoff_kind.sql`
Add `payoff_kind TEXT` to `discovery.funnel_candidates` (nullable; NULL = not yet
classified). One of: `app | web_sub | physical | service | leadgen`. No index needed
(low-cardinality, small table).

### Unit 1 — payoff signatures + classifier (`agf/discovery/firehose_fingerprint.py`)
Extend `SIGNATURES` with two categories (keep checkout/pixel/mmp/store):
- `physical`: `cdn.shopify`, `myshopify`, `/cart`, `add-to-cart`, `addtocart`,
  `/products/`, `/collections/`
- `service`: `calendly.com`, `acuityscheduling`, `as.me/`, `squareup.com/appointments`,
  `book a call`, `free consultation`, `schedule a call`, `get a quote`

Add `_has_billing_language(html) -> bool`: matches recurring-billing copy —
`cancel anytime`, `per month`, `/mo`, `a month`, `billed`, `subscription`,
`day trial`, `then $`. (Case-insensitive.)

Add `classify_payoff(html, *, store_id=None) -> str` with this PRECEDENCE (documented,
order matters — Shopify also loads Stripe, so physical must beat checkout):
```
sigs = detect_signatures(html)            # now includes physical, service
if store_id:               return "app"        # a real store id was extracted
if "physical" in sigs:     return "physical"
if "service"  in sigs:     return "service"
if "store" in sigs or "mmp" in sigs: return "app"   # app handoff, id unresolved
if "checkout" in sigs and ("pixel" in sigs or _has_billing_language(html)):
    return "web_sub"
return "leadgen"
```

### Unit 2 — store-id extraction from rendered DOM (`agf/discovery/funnel_classify.py`)
Add `store_ref_from_html(html) -> tuple[str, str] | None` → `("ios", "<digits>")` from
`apps.apple.com|itunes.apple.com/.../id(\d+)`, or `("android", "<pkg>")` from
`play.google.com/store/apps/details?...id=(<pkg>)`. First match wins; None if absent.
Pure, unit-tested. (Lives in funnel_classify — already the funnel pure-helpers module.)

### Unit 3 — deeplink MMP host list (`agf/extract/deeplink.py`)
`_MMP_HOSTS` is missing the most common hosts. Add: `onelink.me`, `app.adjust.com`,
`adjust.com`, `branch.io`, `bnc.lt`. (Keeps the existing app.link/onelink.to/apple.co/
go.link/adj.st/lnk.to.) Pure change; unit-test the resolve picks up an onelink.me wrapper.

### Unit 4 — classify_funnel returns store_id + payoff_kind (`agf/discovery/funnel_confirm.py`)
`classify_funnel(html, resolved, funnel_host=None, quiz_steps=None, funnel_url=None)` now:
1. Resolve store ref: `store, store_id = (resolved.kind, resolved.store_id)` if resolved is
   a store hit; else fall back to `store_ref_from_html(html)`. **This is the bridge fix** —
   the rendered DOM's store link is now captured.
2. Compute `payoff_kind = classify_payoff(html, store_id=store_id)`.
3. Keep the existing `kind`/`status`/`drop_reason` confirmation logic unchanged (install/
   subscribe/unknown/dropped), but the `store`/`store_id` it returns now come from step 1.
4. Return `(kind, status, store, store_id, drop_reason, payoff_kind)` (6-tuple).

### Unit 5 — persist payoff_kind (`agf/db/funnel_candidates.py`)
`mark_funnel_confirmed(..., payoff_kind=None)` writes the new column.
Add `_new_rows` to also select `page_id, page_name` (needed to build the bridge candidate).

### Unit 6 — confirm-time app bridge (`agf/discovery/funnel_confirm.py`)
In `run_funnel_confirm`, after `mark_funnel_confirmed`, when `payoff_kind == "app"` AND
`store_id` is present, build a `discovery.candidates` row and `upsert_sweep_candidate`
(same as the sweep-time bridge in funnel_sweep.py:121-128):
```
{page_id, page_name, page_like_count(None ok), store, store_id,
 link_url=landing_url, cta_type=None, ad_start_date=None,
 source_keyword=source_keyword, technique="funnel_confirm"}
```
Count it in stats as `bridged_app_new`/`bridged_app_seen`. Physical/service/leadgen are
NOT bridged. (run_triage then resolves the app under the normal app vetoes → feed.)

### Unit 7 — dashboard count digital-only (`backend/repositories/metrics_repo.go`)
`SELECT count(*) ... WHERE status='confirmed'` →
`WHERE status='confirmed' AND payoff_kind IN ('app','web_sub')`. Rebuild + redeploy API.

### Unit 8 — backfill (`scripts/backfill_funnel_payoff.py`)
Re-render each existing confirmed funnel (`render_landing_html`, direct, iOS UA, bounded +
resumable: skip rows where `payoff_kind IS NOT NULL`), run units 2+1 to set `payoff_kind`
(+ `store`/`store_id` when found), and bridge the `app` ones via unit 6's path. Double duty:
classifies the 260 AND retroactively recovers the ~21-app reserve. Idempotent.

## Testing
- Pure units (1,2,3): RED/GREEN unit tests — classify_payoff precedence (incl. Shopify+Stripe
  → physical; BetterMe-style checkout+billing, no store → web_sub; store link → app),
  `store_ref_from_html` (ios/android/none), deeplink onelink.me resolve.
- DB units (4,5,6): pg-backed tests on the agf_it scratch DB (AGF_TEST_DB_URL) — a confirmed
  funnel with a store link in its HTML gets `payoff_kind='app'`, `store_id` set, AND a
  `discovery.candidates` row; a Shopify funnel gets `physical` and NO bridge.
- Go (7): integration count test asserts physical funnels don't inflate the metric.
- Live verify (controller): apply 139, run backfill on a sample, query
  `payoff_kind` distribution + new `discovery.candidates` rows with `technique='funnel_confirm'`,
  rebuild API + confirm the count, restart the worker. Full suite to 0 failed.

## Risks / notes
- Migration 139 free as of 2026-06-19; a parallel session could grab it — re-verify at apply.
- Backfill re-renders 260 pages (~slow, ~minutes); resumable so it can run in the background.
- `classify_payoff` precedence is a heuristic; the backfill's distribution print is the
  validation that it splits sensibly before we trust the digital-only count.
- Keep DB tests under `AGF_TEST_DB_URL` (silent skip otherwise). Commit units separately.
