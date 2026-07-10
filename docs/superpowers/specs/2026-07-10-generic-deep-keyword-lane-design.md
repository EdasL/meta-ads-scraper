# Generic deep keyword lane — design

**Date:** 2026-07-10 · **Status:** approved by founder (chat)

## Goal

Add a weekly deep-sweep lane of ~25 static *copy phrases* — generic terms that
literally appear inside app-install ad copy — paged to Meta's real depth ceiling.
Two payoffs per pass:

1. **Direct discovery** — a firehose of app advertisers the niche-noun taxonomy
   never matches (search matches ad copy text + page name, not app categories).
2. **Keyword harvest** — the collected ad copy is a large mining corpus for
   `mine_phrases` (its home domain), minting new trial keywords for future sweeps.

## Evidence (measured 2026-07-10)

- Live probe: the keyword `app` served **80 pages (788 ads, 405 unique
  advertisers) with `has_next` still true** — unique advertisers still climbing
  ~45 per 10 pages at the cap. Research (07-07 enumeration doc) puts the cursor
  death near ~1,500 results, so a generic term is good for roughly 150 pages.
- Depth yield on the live adaptive sweep (since 07-08) is **flat, not decaying**:
  pages 11–15 still produce ~30 candidates per depth level at ~70% keep rate.
  Depth is not junk; today's caps are cutting off live yield.
- Founder research (Reddit r/PPC + r/dropshipping passes): Ad Library keyword
  search matches **copy text**, so copy phrases ("AI headshots", "wish I knew
  sooner") beat category nouns; quiz openers are the funnel lane's axis and are
  already deep-swept weekly.

## Seed list

`GENERIC_DEEP_TERMS` in `taxonomy.py`, new layer tag `generic_deep`. Quiz-funnel
openers are EXCLUDED (already in `FUNNEL_PHRASES`). Four groups:

- **Ultra-generic firehose** (KEYWORD_UNORDERED): `app`, `ai`
- **App-install CTAs** (KEYWORD_EXACT_PHRASE): `download the app`, `get the app`,
  `on the App Store`, `start your free trial`, `3-day free trial`
- **AI copy phrases** (KEYWORD_EXACT_PHRASE): `AI headshots`, `AI photo generator`,
  `AI companion`, `AI girlfriend`, `talk to AI`, `your personal AI`, `AI powered`,
  `birth chart`, `your zodiac`
- **Intent/emotion hooks** (KEYWORD_EXACT_PHRASE): `does this actually work`,
  `wish I knew sooner`, `my biggest regret`, `what no one tells you about`,
  `stopped using`, `thousands switched`, `customers are obsessed`

~23 terms × ≤150 pages ≈ ≤3.5k SERP requests per weekly pass — the same order
as the funnel deep pass (28 × 100), and the proxy pool sits ~90% idle.

## Mechanism

**One new weekly worker stage** (`generic_deep` pass), the app-side sibling of
the funnel deep sweep, reusing `run_sweep` — no bespoke paging code.

- **Paging:** each term swept to exhaustion (`has_next` false / cursor death)
  under a safety cap `AGF_GENERIC_DEEP_PAGES` (default 150). Patience is
  effectively disabled for this lane (`patience_pages` set very high): a harvest
  lane must not stop on candidate-dry pages because the copy corpus and funnel
  folds still accrue. Per-term heartbeat (`run_sweep` already beats per keyword;
  a 150-page term is ~7 min, well inside the 7,500 s watchdog).
- **Candidates:** flow through the normal fold into `discovery.candidates`
  tagged `technique='generic_deep'` — keeper yield by source is measurable with
  the existing pattern. Web rejects auto-fold into `discovery.funnel_candidates`
  (5M funnel ceiling applies). Note the synergy with today's removal of the 10k
  page-likes drop: generic SERPs are full of big pages that now flow through.
- **Copy collection:** `run_sweep` gains an optional `collect` callback invoked
  per raw `AdRecord` in `_process_page`. The deep stage accumulates
  `(page_id, body_text)` pairs — exactly `mine_phrases`' corpus shape.
- **Mint chain (unchanged, new source):** after the pass:
  `mine_phrases(corpus, exclude=static|tried, min_advertisers, limit)` →
  `drop_token_supersets` → embedding basin gate (`_gate_keywords`) →
  `add_trial_keywords(conn, kws, source='generic_deep')`. Capped at
  `AGF_GENERIC_DEEP_MINT_SLOTS` (default 25) per weekly pass and bounded by
  trial-pool room, so it cannot crowd out the gem-copy lane.
- **Weekly gate:** mirror the funnel pattern — the pass records its stats into
  the run row and the due-check is "a FINISHED run in the last
  `AGF_GENERIC_DEEP_EVERY_DAYS` (default 7) days carried a generic-deep stats
  object" (same `jsonb_typeof(stats->…) = 'object'` shape as
  `funnel_deep_ran_within_days`; exact table — `discovery.sweep_runs` stats or a
  sibling marker — resolved at plan time from the code). A failed pass simply
  retries next day, like the funnel deep lane.
- **Ceiling logging:** per term, log pages fetched / ads / whether the cap or
  Meta exhaustion stopped it (stderr, same as the funnel deep pass) so the real
  per-term cursor ceiling becomes known and the 150 cap can be tuned.

## Env knobs

- `AGF_GENERIC_DEEP` — enable the stage (default 1; 0 disables)
- `AGF_GENERIC_DEEP_PAGES` — per-term page cap (default 150)
- `AGF_GENERIC_DEEP_EVERY_DAYS` — cadence (default 7; 0 disables)
- `AGF_GENERIC_DEEP_MINT_SLOTS` — max trial keywords minted per pass (default 25)

## Testing

- Unit: seed-list shape (no overlap with `FUNNEL_PHRASES`/`NICHE_TERMS`; search
  types as specified); `run_sweep` collect-callback receives every raw ad;
  stage mints through the full chain with a `FakeExtractor` scripted corpus;
  weekly gate true/false; mint slots + trial-room bounds respected.
- DB tests on the rolled-back `agf_it` engine (sentinel page_ids), as usual.
- Live e2e (before "done"): one-off pass in the deployed container over 2–3
  terms with a reduced page cap; verify candidates land tagged `generic_deep`,
  minted trials appear with `source='generic_deep'`, ceiling log lines print.

## Out of scope / follow-ups

- Raising the general sweep cap `AGF_SWEEP_MAX_DEPTH` 15→25 (flat-yield data
  supports it; one env-var change, decided separately).
- The "click through to the advertiser's page → see all ads" workflow from the
  research is already owned by enrich/corpus-refresh (`view_all_page_id`).
- No changes to the funnel deep lane or quiz phrases.
