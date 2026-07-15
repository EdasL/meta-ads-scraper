# Hunt term strategy — why niche hunts miss competitors, and what to do about it

Date: 2026-07-15. Trigger: the "running apps for women" hunts (hunt 3 + hunt 5, rounds
7–18) produced 154 finds, 5 judged relevant, 0 women-specific. Research = code audit of
the term/competitor machinery + a live Meta Ad Library probe of the 12 named competitors
(raw output: probe archived from scratchpad `probe_women_out.json`; DB dumps ditto).

## What a term actually queries

A hunt sweep term hits Meta keyword search as `KEYWORD_UNORDERED`
(`extract/facade.py:120`, `extract/search.py` `build_search_variables`): the term is an
**unordered bag of tokens matched against ad creative text + page name**. Not a phrase,
not a category. Consequences, all live-verified:

- **"app" is a junk magnet.** Every failing hunt term contained the token "app".
  Pocket FM / ReelShort / novel / chat installers keyword-stuff "app" across their
  creatives and outrank everything by reach. "running app for women" → Temu, Shoptemu,
  Pocket FM in the top 5; 0 fitness advertisers.
- **Brand names return nothing** ("Femmi app", "Joggo app", "Movana" → 0 results):
  keyword search matches ad body text, and advertisers don't write their own brand name
  into copy. Brands belong in the competitor lane, never in terms.
- **Terms without "app", using domain/practitioner or benefit vocabulary, reach real
  advertisers with ~0 junk**: "womens running coach" → 17 pages, 6 on-domain, 0 junk;
  "run walk beginner plan" → a real fitness app (The Conqueror Challenges) at
  position [0]; "lose weight running plan" → V Shred present.
- **Target apps advertise gender-neutrally.** Joggo (the only real women-adjacent
  running app with a substantial ad history) writes "from your very first run",
  "5K or your first marathon", "beginner-friendly plan" — never "women". Women-qualified
  terms structurally EXCLUDE the target; this is why they scored 0 relevant even at
  page depth 8–12 (depth confounder tested and debunked 2026-07-15).

## Peon's term generation works against the niche

`backend/helpers/gemini.go:203` (one-shot) and `:463` (agent v3):

- The prompt says *"Prefer a few broad head terms over hyper-specific long-tail"* (:497)
  — exactly the style that drags in drama incumbents.
- Style guidance is "queries a user would type (e.g. 'sleep stories app')" — i.e.
  category descriptors WITH the "app" token.
- **No junk-density feedback loop**: Peon sees `RoundSummaries` (counts) and
  `TriedTerms` (`gemini.go:190-191`) but never per-term triage/relevance/junk rates, so
  it cannot learn that a term returned 80% drama.

## Competitor-pull lane: three code bugs + one market reality

Lane code: `discovery/hunt.py:194-224`. Live probe of all 12 named competitors:

1. **`competitors[:3]` cap** (`hunt.py:203`): only the first 3 names are ever attempted;
   9 of 12 were silently never pulled.
2. **Blind `pages[0]` resolution** (`hunt.py:212`): typeahead matches page NAME ranked
   by likes, so big irrelevant pages outrank small target apps. Live: Femmi →
   "Femmira Fashion", SheRuns → "Kickstarter Home Runs", Wild AI → "Aidan Wilderman"
   (a person); the REAL Femmi / Wild AI / Gazelle Running pages exist in typeahead but
   at positions [3]/[4] or only under a name variant.
3. **Fake telemetry**: `stop='listing'`, `pages=1` are hardcoded literals
   (`hunt.py:222-224`), masking all of the above.
4. **Market reality**: even correctly-resolved pages (Joggo, FitrWoman, Movana, RunBabe)
   currently run **0 active US ads** (Joggo: 30 ads, all inactive). Fixing resolution
   improves precision but won't make found>0 for this niche today under an
   active-US-only pull. Levers: include inactive ads and/or non-US countries for
   competitor pulls (these are small EU/AU apps).

## Recommendations (ranked)

1. **Term style rules in the Peon prompt** (cheap, immediate):
   - Ban the token "app" (and "application") in generated terms.
   - Replace "prefer broad head terms" with: benefit/ad-copy language and
     practitioner/domain vocabulary ("womens running coach", "run walk beginner plan",
     "couch to 5k plan"), 2–4 content words, no brand names.
   - For gendered/niche intents: generate mostly NEUTRAL benefit terms and let the
     relevance judge + triage do the filtering — the qualified terms exclude the targets.
2. **Per-term outcome feedback to Peon**: extend RoundSummaries with per-term
   found/new/kept/relevant/junk-share so the agent can drop colonized terms and mutate
   toward what worked. (The junk share is computable from hunt_results today.)
3. **Fix the competitor lane** (highest ceiling — it's the enumerate-then-iterate
   primitive we already own):
   - Lift the `[:3]` cap.
   - Disambiguate typeahead: scan top-N results, fuzzy-match the entered name, prefer
     pages whose ads carry app-store links; verify before pulling.
   - Emit real stop/pages telemetry.
   - Pull with country scope beyond US and include inactive ads for competitor pulls
     (a competitor's PAST creatives still teach vocabulary + confirm identity).
   - Longer term per docs/research/2026-07-07: resolve app → developer domain
     (app-ads.txt / store listing) → page, instead of name typeahead.
4. **Store-first seed for niche hunts** (missing primitive): the repo has no
   query→apps store search (`enrich_full/appstore.py` has only autocomplete, lookup,
   similar, developer). Building it would let a hunt find the competitor APPS first
   (store search "running women"), then resolve each to a Meta page — keyword terms
   become the fallback, not the primary.
