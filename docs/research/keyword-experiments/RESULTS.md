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
| A | category-balanced LLM, 2/cat, no suffix | 64 | 22% Education | 44% | 87% | 0.98 | 59% |
| B | A + `app` suffix (suffix A/B test) | 64 | **13.8% H&F** | **36%** | **91%** | **1.25** | 47% |
| C | B, dead terms rephrased to common wording | 64 | **18.6% H&F** | **39%** | **91.1%** | **2.14** | **18.8%** |

**Winner: C** — dominates the baseline on every axis (evenness 91 vs 76, top-genre
19 vs 26, yield 2.14 vs 1.3, dead 19 vs 32). Loop converged: A→B→C each improved;
remaining dead terms are funnel-advertised niches the funnel pipeline already
catches, so further chasing would broaden toward incumbents.

## Findings

- **A — diversity up, yield down.** Equal per-category coverage raised evenness
  76→87% and cut top-genre 26→22%, but dead-kw rose 32→59%: many balanced
  niches return funnel/web ads (no direct store link) or nothing in 2 shallow
  pages. Concentration shifted to Education (cross-cutting AI-tutor/learning
  terms pile there). Core tension confirmed: **balance vs. yield**.
- **B — the `app` suffix wins on every axis.** vs A: yield 0.98→1.25 (+28%),
  top-genre 22→14%, evenness 87→91%, dead 59→47%. The suffix acts as an
  app-intent filter, pulling real app advertisers across MORE categories instead
  of funnel/web ads concentrated in a few. **This contradicts the spec's
  "suffix optional" relaxation** — for the app seed the suffix does real work.
  B beats the production baseline decisively on bias (top-genre 14% vs 26%,
  evenness 91% vs 76%) at comparable yield. **Current leader.**
- **B's dead terms split two ways** (informs C + architecture):
  - *9 got 0 ads* (no Meta inventory for that exact wording) → over-specific
    phrasing, fixable by rephrasing (Technique C).
  - *~21 got ads but 0 store-links* — known app niches (ai math solver,
    menopause tracker, manifestation journal) that advertise via FUNNELS. The
    direct-app keyword sweep structurally can't catch these, but the
    **category-agnostic funnel-phrase pipeline already does**. So the app seed
    and the funnel seed are complementary by advertising style — the app seed's
    "dead rate" overstates true system waste.
- **C (testing) — is the no-inventory half fixable?** Rephrase dead terms to
  common ad wording, keep B's productive terms. If diversity holds and dead-rate
  drops, C is the shippable seed.

## Technique queue

- [x] A — category-balanced, no suffix
- [x] B — category-balanced, `app` suffix
- [x] C — B with dead terms rephrased to common wording — **WINNER, converged**

## Winning recipe → ship

**Category-balanced frame + `app` suffix + common (not over-specific) ad wording.**
Next step (implementation): scale the C recipe from 2/category to the spec's
~8-10/category, generated with this recipe, validated with one more bake-off run.

Spec changes this experiment forces:
1. **`app` suffix is NOT optional** — it raises yield (+28% A→B) AND diversity.
   Revert the "suffix optional" relaxation in
   `docs/superpowers/specs/2026-06-19-category-balanced-keyword-seed-design.md`.
2. **Generation rule: prefer common ad wording over over-specific phrasing**
   (B→C: dead 47→19%, yield 1.25→2.14). "pickleball app" not "pickleball score
   tracker app".
3. **App-seed and funnel-seed are complementary by advertising style.** Don't
   force the app keyword seed to catch funnel-advertised niches — that's the
   funnel pipeline's job. The app seed's "dead rate" overstates true waste.

### Flywheel fit (qualitative)
The flywheel mines the ad copy of FOUND apps, so a category-diverse base (C: 137
apps / 20 genres) gives it diverse basins to deepen — directly breaking the
topical-locality trap the 2026-06-18 research doc identified (a wellness-skewed
base could only deepen wellness). Quantitative promote-rate needs days of live
flywheel runs to confirm.
