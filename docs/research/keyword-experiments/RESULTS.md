# Keyword-seed bake-off — running results

Goal: find the keyword seed that finds emerging apps well (yield) **and** isn't
biased to a category (diversity) **and** feeds the flywheel. Harness:
`scripts/keyword_seed_experiment.py` (bounded live sweep, no DB writes). Metrics
on the *genres of app-store-linked advertisers found* (incumbent-vetoed).

Diversity is comparable across rows; yield/dead-rate are a **pre-triage proxy**
(app-store-link + incumbent veto only), so they read lower than the post-triage
baseline — compare techniques to each other, not to the baseline's absolute yield.

| # | Technique | kw | top-genre share | top-3 | evenness | yield/kw | dead-kw |
|---|---|---|---|---|---|---|---|
| — | **baseline** niche_term (270, post-triage) | 539 | 26% H&F | 48% | 76% | 1.3 kept | 32% |
| A | category-balanced LLM, 2/cat, no suffix | 64 | 22% Education | 44% | **87%** | 0.98 | **59%** |
| B | A + `app` suffix (suffix A/B test) | 64 | _running_ | | | | |

## Findings

- **A — diversity up, yield down.** Equal per-category coverage raised evenness
  76→87% and cut top-genre 26→22%, but dead-kw rose 32→59%: many balanced
  niches return funnel/web ads (no direct store link) or nothing in 2 shallow
  pages. Concentration shifted to Education (cross-cutting AI-tutor/learning
  terms pile there). Core tension confirmed: **balance vs. yield**.
- **B (testing) — does the `app` suffix recover yield?** A's dead terms were
  mostly bare phrases; the production seed is all `app`-suffixed at 32% dead.
  B isolates the suffix effect → informs the spec's "suffix optional" call.

## Technique queue

- [x] A — category-balanced, no suffix
- [ ] B — category-balanced, `app` suffix  ← running
- [ ] C — autocomplete/store-grounded terms (cut dead-rate by grounding in real apps)
- [ ] D — yield-weighted balance (keep diversity, drop categories with no app supply)
