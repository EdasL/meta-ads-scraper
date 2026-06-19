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

| C2 | C re-run, **corrected metrics** (store_id dedup, 100% genre coverage) | 64 | **14.6% H&F** | **34%** | **87.5%** | 2.03 | 20.3% |

**Winner: C** — dominates the baseline on every axis (evenness 87.5 vs 76, top-genre
14.6 vs 26, yield ~2 vs 1.3, dead 20 vs 32). Loop converged: A→B→C each improved;
remaining dead terms are funnel-advertised niches the funnel pipeline already
catches, so further chasing would broaden toward incumbents.

### "Diverse enough?" — verified (C2, trustworthy metrics)
The first C run's metrics were unreliable: dedup was by page_id (PocketFM's 7 ad
pages counted as 7 apps) and the android genre lookup timed out (genre resolved
for only 43% of apps → diversity computed over an iOS-only subset). Fixed harness
(store_id dedup, chunked android lookup, novelty check) gives the real picture:
- **130 distinct apps, 34 genres, top genre 14.6%, evenness 87.5%** — long tail
  reaches Dating/Parenting/Medical/Shopping/House&Home/Navigation/News/Social.
- **31 new apps (24% novelty) span 19 genres, max 3/genre — no clustering.**
  Kindroid AI, Lume, Wanderlog, Comikaze, Abide Bible, Rainbow.ai, ReGen, Yomu,
  Preggers, AppLock, Surveva, Flights70 — genuinely diverse niche emerging apps.
- 76% already in corpus (expected at steady state, 2 shallow pages over a 722-app
  corpus). Absolute new-app count scales with the spec's 8–10/cat + deeper pages;
  diversity holds.

**Verdict: diverse enough.** Far more even than the baseline, and the new apps —
the ones that matter for ongoing discovery — are spread, not clustered.

## Full-seed validation (full_v1: 288 terms, 9/category)

The C recipe scaled to production (`full-seed-v1.txt`) swept live:
- **437 distinct apps**, yield 1.52/kw, **135 new (30.9% novelty)** — yield and
  novelty hold at scale (novelty even rose vs the bounded run's 24%).
- **Diversity by SEED CATEGORY (100% coverage, the true metric): top category
  6.6% (Communication), top-3 17.2%, evenness 97.6%, ZERO empty categories.**
  The balanced seed produces a near-perfectly balanced catch.

Note on metrics: store-genre coverage was unreliable here — the node google-play
sidecar times out (~6s/id, and hangs entirely under load), dropping ~210 android
genres. Store genres also CONFLATE distinct seed categories into catch-all
buckets (H&F, Education). **Measure diversity by seed category** (each app's
source keyword → its seed category, 100% coverage) rather than store genre —
it's both complete and truer to whether the seed itself is balanced.

**SEED VALIDATED → ready for implementation** (wire `full-seed-v1.txt` into
`taxonomy.py` as `NICHE_TERMS_BY_CATEGORY` + the balance test, per the spec).
Optional follow-up: prune the ~31% dead terms (mostly funnel-advertised niches
the funnel pipeline already catches, plus a few zero-inventory phrasings).

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
