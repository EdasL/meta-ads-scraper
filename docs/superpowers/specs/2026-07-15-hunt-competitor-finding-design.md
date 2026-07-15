# Hunt competitor-finding fixes — design

Date: 2026-07-15. Grounding: docs/research/2026-07-15-hunt-term-strategy.md (live
Meta probe + code audit of the women's-running hunts). Founder decisions locked in
chat: capture BOTH apps and web funnels in hunt results; competitor pulls go global;
Meta's official EU (DSA) Ad Library API is a follow-up increment, not this one.

## Problem

Hunts on niche intents fail to surface the real competitors:

1. Peon's prompt pushes broad "<niche> app" terms; the token "app" is a measured
   junk magnet (drama/e-com advertisers keyword-stuff it), and target apps write
   gender-neutral benefit copy, so qualified terms exclude them.
2. Peon never sees per-term junk/relevance outcomes, so it cannot learn.
3. The competitor lane attempts only `competitors[:3]` (hunt.py:203), resolves via
   blind typeahead `pages[0]` (wrong page for Femmi/SheRuns/Wild AI; the real page
   sits at index 3-4), reports hardcoded `stop='listing', pages=1` telemetry, and
   drops every non-store-link ad — Joggo/Femmi are web2app funnels, so even a
   correct resolution yields found=0. Pulls are also US-only while the niche
   advertises in EU/AU.

## 1. Peon term-style rules — backend/helpers/gemini.go

Both `huntSystemInstruction` (:203) and `huntAgentInstruction` (:463):

- Terms must not contain the tokens "app", "apps", "application" (measured junk
  magnet), on top of the existing no-brand-names rule.
- Style: short benefit/practitioner phrases an advertiser would write in ad copy
  ("womens running coach", "run walk beginner plan", "couch to 5k plan");
  2–4 content words.
- Gendered or narrowly-qualified intents: propose mostly NEUTRAL benefit terms
  (measured: the target apps advertise gender-neutrally; the relevance judge does
  the intent filtering).
- REPLACE the "prefer a few broad head terms" paragraph (:497): broad
  "<niche> app" heads are colonized by keyword-stuffing short-drama/e-commerce
  advertisers; prefer specific natural ad-copy phrasing.
- On re-proposals, consult per-term stats from get_round_report and drop or
  mutate terms with zero kept or majority off-target results.
- `cleanHuntTerms` (:299) additionally drops any term containing an "app"-family
  token, so model drift cannot reintroduce them.
- competitor_apps cap 3 → 10 everywhere it is encoded: instruction text
  ("at most 3"), function-declaration description (:692), `capStrings(..., 3)`
  (:712), and the one-shot schema (:238) if it repeats the cap.

## 2. Per-term outcome feedback

- Python executor (`run_hunt_round` phase 5, discovery/hunt.py): after
  `apply_triage_to_results` and the relevance judge finish, aggregate per
  `source_term` from frontend.hunt_results: found / new / kept / relevant /
  off_target. Merge these keys into the existing `progress.terms[<term>]` entries
  (jsonb; new keys alongside pages/found/new/stop). Competitor pulls aggregate
  under their existing `page:<name>` key.
- Go `repositories.RoundReport` (hunt_repo.go:154): surface the new per-term keys
  in the get_round_report tool payload (it already serializes progress.terms).
- `HuntRoundSummaries` (hunt_repo.go:249): extend the one-line summary with
  relevant/off-target counts: "round 3: 12 terms, 40 found, 5 kept, 2 relevant,
  9 off-target". Per-term detail stays behind get_round_report.

## 3. Competitor lane — src/agf/discovery/hunt.py (phase 0)

- Cap `[:3]` → `[:10]` (matches the new Go-side cap).
- Resolution disambiguation (replaces `pages[0]`):
  - Consider the top 5 typeahead rows. Rank by normalized fuzzy name match
    (difflib ratio of lowercased/stripped names) descending, tie-break by
    page_like_count descending.
  - Attempt candidates in rank order: pull the listing; accept the first page
    yielding ≥1 persisted result (app or funnel). At most 3 page attempts per
    competitor name.
  - Progress events name the chosen page: "Pulling ads from 'Joggo'".
- Real telemetry: `term_stats("page:<name>", pages=<listing pages actually
  fetched>, found, new, stop)` with stop ∈ `listing_ok` | `no_match` (typeahead
  empty / nothing acceptable) | `no_results` (pages tried, nothing persisted) |
  `error`. The hardcoded `("listing", 1)` literals go away.
- Global scope: competitor pulls pass a broad country scope. Build-time live
  probe decides: `countries=["ALL"]` if the pagination query accepts it,
  otherwise an explicit broad list (US, GB, CA, AU, DE, FR, ES, IT, NL, SE, PL,
  LT, BR, MX, IN). Term sweeps stay US-only — unchanged.
- Capture BOTH apps and funnels in `_persist_page` (both lanes): mirror the
  nightly sweep's non_app branch (sweep.py:127-140) — `fold_web_funnel` under the
  same FUNNEL_INCUMBENT_PAGE_LIKES_MAX ceiling; persist via `record_web_funnel`
  AND a hunt_results row: store='web', store_id=<landing_domain>,
  candidate_id NULL, page=<depth>, triage='kept' (no app triage applies; the
  relevance judge — which reads kept rows and judges name/ad text vs intent —
  gates what the founder sees). Enrich enqueue must skip store='web' rows.
  (Schema verified: hunt_results.store/store_id are plain TEXT with a
  (round_id, page_id, store, store_id) unique key and candidate_id is already
  nullable (155_hunts.sql:46) — no migration needed.)

## 4. Out of scope (follow-up increments)

- Meta official Ad Library API for EU/DSA data (richer reach + demographics for
  global pulls) — needs API access setup; founder wants this next.
- query→apps store-search seed lane (repo has only autocomplete/lookup/similar).

## Verification gates

- Full Python suite (AGF_TEST_DB_URL=...55433/agf_it) and Go suite to 0 failed,
  run serially, bare commands.
- Live e2e on prod: apply migration 158 (exists on main, NOT applied in prod),
  roll the worker/hunt image, re-run a women's-running round with the 10+
  competitors from hunt 5. Success criteria: Femmi / Joggo / Wild AI resolve to
  their real pages (progress events show the chosen page), competitor pulls
  produce hunt_results rows (app or web), per-term kept/relevant stats appear in
  get_round_report, and Peon's next proposal contains no "app"-token terms.
