# Meta Ad Library & hidden-web scraping techniques — domain-specific, research-backed

**Date:** 2026-07-06
**Question:** what do practitioners and the peer-reviewed hidden-web-crawling literature
say specifically about scraping the Meta Ad Library / keyword-queried indexes to keep
finding NEW emerging apps instead of re-finding the corpus?
**Method:** deep-research harness — 5 domain angles → 20 sources → 84 claims → 25 verified
(3-vote adversarial, 2/3 to kill). **22 confirmed, 3 refuted, 0 unverified.** All sources
are hidden-web-crawling / ad-library-specific (not the ecology/bandit transplants of the
allocation doc).
**Relationship to prior docs:** complements
[2026-07-02 novel-discovery-algorithm](2026-07-02-novel-discovery-algorithm.md) (deep-web
theory, RAW-UCB, Chao2) and [2026-07-06 category-budget-allocation](2026-07-06-category-budget-allocation.md)
(allocation math). This one is the *scraping technique* layer — how to pick and shape the
actual queries. Do not re-derive the scheduler/Chao2 from those docs.

---

## 0. The finding that indicts our flywheel directly

**Selecting the next keyword by its frequency/popularity in the already-found corpus is
provably the wrong policy — and it is exactly what our flywheel does.** (VLDB 2011,
arXiv:1104.3213; SmartCrawl, SIGMOD 2019.) Two independent mechanisms, both 3-0 confirmed:

1. **Semantic subset bias.** "Corpus-driven keyword query expansion approaches return
   popular words in the results as expanded queries… the expanded queries may correspond
   to a subset of possible query semantics, and thus miss relevant results." Popular-term
   mining re-queries the dense center of what we already have → re-converges on the corpus.
2. **Truncation makes frequency a lie.** Under a top-k impressions-desc cap (our
   `AGF_SWEEP_PAGES=4`), estimating a query's benefit by local frequency is "ineffective
   due to (i) the impact of records absent from the hidden DB, and (ii) the top-k
   constraint." The highest-frequency keywords have their true match set *most* clipped,
   so frequency systematically **overestimates** their new-record gain.

Our `gem_copy` / `funnel_copy` flywheel mines popular n-grams from found ads → that IS
frequency-based selection. This is the mechanism behind the "flywheel converges / 92%
Chao2-exhausted" symptom, now with a domain-specific citation, not just the Google
deep-web analogy.

---

## 1. The prescribed replacement — budgeted weighted greedy set-cover (CONFIRMED 3-0)

Model query selection as **budgeted greedy set-cover** where each query's benefit is the
**estimated number of NEW distinct records it adds (harvest rate)**, not raw frequency.
Iteratively issue the query whose result-union covers the most not-yet-covered records.

- **Weight the greedy.** "For deep web crawling, neither the sizes of documents nor the
  document frequencies of the queries is distributed uniformly — they follow the power
  law"; introducing weights into greedy "consistently outperforms its un-weighted
  version" across corpora (Wang & Lu, ADMA 2009). Plain set-cover under-performs because
  it assumes uniformity.
- Sources: SmartCrawl (SIGMOD 2019); Wu et al. (ICDE 2006, "query harvest rate"); Wang &
  Lu (ADMA 2009).
- **Maps to us:** replace the flywheel's "mine popular terms" with "rank candidate
  keywords by estimated new-unique yield per sweep." We already log `new_candidates_total`
  and `dry_streak` per keyword — that is the harvest-rate signal; the change is to *select
  on it* rather than on n-gram popularity.

---

## 2. Score keyword redundancy OFFLINE before spending Meta quota (CONFIRMED 3-0)

High-coverage queries can be learned from a **small SAMPLE of the corpus** instead of
incrementally from all matched docs — and **overlap is predictable**: "queries with a low
overlapping rate in samples will also result in a low overlapping rate in the original
database" (Wang, Lu et al., WIAS 2012). Larger samples help; query-pool size barely
matters.

- **Directly actionable:** our local `discovery.candidates` DB **is** the sample. Before
  a keyword ever costs a Meta request, estimate its redundancy — how much of its likely
  result set we already hold — and drop high-overlap keywords. This pre-filters the
  re-finding that currently burns ~99.7% of proxy budget (per the 07-02 probe) *without
  spending a single sweep*.

---

## 3. Two structural obstacles must be co-handled: top-k truncation + query quota (CONFIRMED 3-0)

Meta imposes both a **top-k cap per keyword** (impressions-desc pagination) and a
**query-rate quota**. These are the exact pair modern hidden-DB crawling co-optimizes:
SmartCrawl budgets around the quota; VLDB 2012 (arXiv:1208.0075) extracts all tuples from
a partially-answering (truncated) interface. Designing for one alone is insufficient.

- **The transferable tactic — narrowing.** VLDB 2012 defeats truncation by issuing
  **more-specific sub-queries** that fall below the top-k cap. On a form interface this is
  predicate-narrowing; on Meta's free-text interface the analog is **compound/longer-tail
  keywords** to un-truncate a saturated broad keyword ("weather app" → "aurora forecast
  app", "marine weather app"). This is precisely what our LLM category-minting produces —
  which reframes minting as *anti-truncation query narrowing*, not just breadth.
- **Caveat:** the VLDB-2012 exact algorithm assumes recursive predicate narrowing a pure
  free-text interface may not support far enough. Open question: does narrowing actually
  drop a broad Meta keyword below its cap? → live probe (`scripts/probe_pagination_depth.py`).

---

## 4. Force breadth: one expanded query per result-cluster (CONFIRMED 3-0)

The concrete counter-recipe to popular-term expansion: **cluster the current result set,
mint exactly ONE expanded query per cluster**, so each reformulation targets a distinct
semantic region instead of reinforcing the dominant one (Liu, Natarajan, Chen, VLDB 2011).

- **Maps to us:** this is the domain-specific justification for the **MMR keyword gate**
  already specced at the keyword level in the 07-02 doc, AND for making category-minting
  cluster-aware — mint per semantic cluster of found ads, not per raw popular term. Native
  context is query suggestion (APX-hard there), but the cluster→one-query mechanism
  transfers cleanly.

---

## 5. Ground the LLM minting in found ads — CSQE beats zero-shot minting (CONFIRMED 3-0)

For emerging niches the LLM can't know, **corpus-steered expansion (pseudo-relevance
feedback) beats pure LLM keyword generation.** CSQE (EACL 2024, arXiv:2402.18031) has the
LLM identify pivotal sentences in *actually-retrieved* documents and expands from those
corpus-originated terms; on post-cutoff content the LLM cannot know, pure LLM-knowledge
expansion "is not able to bring improvements while CSQE leads to remarkable improvements."
Zero-grounding LLM expansion is useful "only for sparse evidence" and "risks hallucination
or drift" (QE survey, arXiv:2509.07794).

- **Maps to us:** our `release_keywords.py` mints from the App Store new-release *listing*
  (name+description) — that IS a corpus grounding, which is the right instinct. The upgrade
  is to also ground in the **found Meta ad copy** of nearby emerging apps (pivotal-term
  extraction), rather than lean on the model's priors for the niche vocabulary. Reserve
  zero-grounding minting for categories where we have no corpus evidence yet.
- **Refuted (0-3):** the "use two heterogeneous LLMs + a third to refine" multi-LLM
  expansion claim did not survive — don't build a multi-model minting pipeline.

---

## 6. Adjacent formalizations to layer in (CONFIRMED, but medium where noted)

- **RL query selection (3-0).** "Learning to crawl deep web" (Information Systems 2013):
  crawler = agent, DB = environment, pick each query by a learned **Q-value** approximating
  future new-record reward. A learned-policy alternative to static greedy — connects to
  (don't duplicate) the 07-02 RAW-UCB scheduler; the contribution here is the
  agent/environment/Q-value framing specific to *query submission*.
- **Urn-overlap coverage meter (medium, 3-0 for the core claim).** arXiv:2603.15416 (2026):
  absolute crawl coverage estimable from **record overlap between successive crawls** via a
  simple urn process fit by linear regression. Complements Chao2 as a saturation meter.
  ⚠️ The stronger "no external ground-truth needed" variant was **refuted 1-2** — treat it
  as needing some anchoring, not a pure self-contained estimator.
- **Greedy siphoning / 90% coverage (medium, split 2-1).** Barbosa & Freire: greedy
  keyword construction can hit >90% coverage AND a constructed query can be cached and
  reused to refresh an index. **Use the greedy-construction idea; reject the
  freeze-and-refresh conclusion** — cache-and-reuse assumes a *static* corpus, and our
  emerging-app corpus grows daily, so a frozen query systematically misses new entrants
  (the exact problem we have). The 90% number does not transfer to a truncated ranked index.

---

## 7. The Ad Library is a biased, incomplete sample — "not found" ≠ "doesn't exist" (CONFIRMED 3-0)

Facebook's political-ad classifier "misses 61% more ads than it detects worldwide, and 55%
of U.S. detected ads are in fact non-political" (Le Pochat/Edelson et al., USENIX Security
2022, Distinguished Paper; 4.2M political + 29.6M non-political ads, 215k advertisers). The
political subset is the best-studied proxy for Ad Library completeness.

- **Maps to us — the honest guardrail for the desert problem:** coverage gaps are
  structural and classification-dependent. A category showing zero yield (News/Weather/
  Shopping) may be a *measurement* gap, not a true absence — reinforcing the
  category-allocation doc's rule that abandonment needs the Rule-of-Three **upper bound**
  and a periodic re-probe, never a permanent cutoff. Do not read "we found nothing in
  category X" as "category X is empty."

---

## 8. What to actually change (priority order)

1. **Stop selecting flywheel keywords by n-gram popularity; select by estimated new-unique
   harvest rate** (§0, §1). Highest-leverage — it attacks the convergence mechanism at its
   root. We already persist `new_candidates_total` / `dry_streak` per keyword.
2. **Pre-score keyword redundancy against our own candidate DB before sweeping** (§2). Cuts
   the ~99.7% repeat-pull waste without spending Meta quota. Cheap, offline.
3. **Ground LLM minting in found ad copy (CSQE-style), not just store listings / priors**
   (§5). Improves the arm-factory's hit rate on genuinely novel niches.
4. **Make minting cluster-aware: one keyword per semantic cluster of found ads** (§4).
   Domain justification for the MMR gate; forces breadth.
5. **Probe whether keyword narrowing defeats top-k truncation on Meta** (§3, open Q). If
   yes, long-tail minting doubles as anti-truncation. `scripts/probe_pagination_depth.py`.
6. **Add the urn-overlap saturation meter alongside Chao2** (§6) — instrumentation only.

Items 1–2 are the domain-specific analogues of what the allocation doc found at the
category level (greedy-on-yield, shed dry arms): here the yield signal is *per keyword* and
the redundancy pre-filter is new. Nothing here contradicts the shipped yield-weighted mint
budget — it operates one level down, on *which keywords*, where that operates on *which
categories*.

---

## 9. Gaps this research could NOT close (be honest)

- **No external source quantifies the Ad Library's non-political coverage, GraphQL/typeahead
  behavior, per-keyword pagination depth, or exact rate-limit thresholds.** Those remain
  characterized only by our operational docs + live probes. The budget `b` for a budgeted
  set-cover is knowable only by probing (open Q).
- **Ad-spy tools (AdSpy/BigSpy) internal discovery mechanics: no verified/published source.**
  Their methods are proprietary; no citable technique emerged. Reverse-engineering or vendor
  docs would be needed, not academic literature.
- **Transfer risk:** the strongest algorithmic results are from *static text* databases
  (2006–2013), empirically validated on text corpora, not a ranked, truncated,
  daily-growing ad index. The greedy/harvest-rate *mechanisms* transfer; the specific
  coverage numbers and any cache-and-freeze conclusion do not.
- **Refuted (do not build on):** self-contained no-ground-truth coverage estimator (1-2);
  VLDB-2012 "full coverage in few queries worst-case" (0-3); multi-LLM expansion (0-3).

## Sources (peer-reviewed primaries unless noted)
Liu/Natarajan/Chen VLDB 2011 (arXiv:1104.3213) · Zhao et al. SmartCrawl SIGMOD 2019 ·
Sheng/Zhang/Tao/Jin VLDB 2012 (arXiv:1208.0075) · Wang & Lu ADMA 2009 · Wu et al. ICDE
2006 · Wang/Lu et al. WIAS 2012 (Selecting Queries from Sample) · Zheng et al. Information
Systems 2013 (Learning to crawl deep web) · Barbosa & Freire JIDM 2010 · arXiv:2603.15416
2026 (urn coverage) · Lei Zhao et al. CSQE EACL 2024 (arXiv:2402.18031) · QE survey
(arXiv:2509.07794) · Le Pochat/Edelson et al. USENIX Security 2022 (FB political-ad audit).
Plus practitioner blogs (scrapecreators, minimaxir/facebook-ad-library-scraper, swipekit,
paulcbauer API review) for GraphQL/pagination surface — not independently verified.
Full transcript: workflow wf_b4922f53-9bf.
