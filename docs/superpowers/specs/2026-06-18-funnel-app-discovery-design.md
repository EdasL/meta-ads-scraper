# Funnel-app discovery — find more of them

**Date:** 2026-06-18
**Status:** approved (apps-only scope)
**Goal:** the directory surfaces too few funnel apps. Increase the count of feed
apps that show a real "Quiz" / funnel link, without expanding scope to app-less
web funnels (the `no_app` veto stays).

## Diagnosis (live `agf` DB, 2026-06-18)

| Stage | Count |
|---|---|
| Feed apps total | 676 |
| Feed apps showing a funnel_url (Quiz link) | **59** |
| `public.funnels` rows | 357 (254 `kind=NULL`, 89 `unknown`, 14 `subscribe`) |
| Distinct advertisers with ≥1 funnel | 100 |
| Feed apps with an off-store ad link but **no funnel row** | **68** (~1,165 ad links) |

Two of the originally-suspected levers turned out to be non-issues:

- **Matview suppression is correct.** The 18 feed apps that have a funnel row but
  no Quiz link all point to social (`instagram.com`/`facebook.com`) or store
  (`apps.apple.com`, `chromewebstore`, `appstore-download.com`) URLs. The matview
  regex correctly excludes them. (Those are legacy `kind=NULL` rows written before
  the write-gate existed.) No change.
- **The 23 vetoed funnel advertisers** are out of scope (app-less / incumbent).

### Root cause

The read and write gates disagree.

- **Read (matview migration 129):** ungated — any off-store/social/MMP web page the
  ad resolves to IS the funnel. "Show the link, let the human judge."
- **Write (`run_enrich.py:186`):** still gated — `public.funnels` only gets a row
  when `classify_funnel` returns `confirmed AND kind IN (subscribe, unknown)`.

So real web-to-app funnels that the strict classifier can't positively confirm are
never written, and the ungated matview has nothing to show. Concrete example:
`https://10mf.siiites.com/?pid=w2a_meta&af_adset=…&af_ad=…` (siiites = funnel
builder, `w2a_meta` = web-to-app, AppsFlyer params) is a textbook funnel but lives
on path `/`, trips no multi-step quiz, and is dropped at write time.

## Fix

### 1. Align the enrich write-gate with the read-gate (root cause)

In `enrich_gem`'s `web` branch: record the funnel row for any **probeable web
landing that shows web-to-app intent**, not only classifier-confirmed ones. Intent =
classifier confirmed it, OR the ad's `link_url`/resolved `final_url` carries a
funnel/web-to-app marker (`w2a`, `web2app`, `af_ad`, `af_adset`, `af_channel`,
`pid=`, `utm_`, `deep_link`, `campaign`) OR has a deep (non-root) path. A bare brand
homepage with no markers and no quiz is still skipped (avoids flooding the column
with non-funnels). Screenshot stays gated on a confirmed funnel, so cost is
unchanged. `kind`/`quiz_steps` are still stored from the probe for transparency.

### 2. Ongoing funnel-repair worker stage

Feed apps below the enrich/alert threshold are never probed, so their funnel ads
never get a row. Add a bounded worker stage (mirror of `media_repair`): each cycle,
take a few feed apps that have an active off-store/social/MMP ad but no funnel row,
and re-run the funnel resolve+probe+write for them. Bounded + isolated: failures
never abort the cycle; `AGF_FUNNEL_REPAIR_PER_CYCLE=0` disables.

### 3. One-time drain

Re-enrich the existing 68 feed apps so the backlog clears immediately rather than
draining a few per cycle.

## Out of scope

- App-less web funnels (the `no_app` veto stays — apps-only scope).
- The funnel-phrase production sweep (already runs daily in the worker).
- Quality tightening of which web pages count (the directive is "show it, human
  judges"); revisit only if the column gets noisy.

## Verification

- New unit tests: a w2a landing the probe can't confirm IS recorded; a bare
  homepage with no funnel intent is NOT.
- Full `pytest` suite green with `AGF_TEST_DB_URL` set (DB tests skip silently
  otherwise).
- Live: re-run the drain against `agf`, confirm feed funnel-link count rises from 59.
