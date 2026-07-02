# Sustaining novel discovery on Meta Ads — the algorithm (research-backed)

**Date:** 2026-07-02
**Question:** what published, evidence-backed algorithm keeps a black-box, query-only
sweep (Meta Ads Library keyword search) *finding new apps* instead of converging?
**Method:** deep-research harness, 5 angles → 23 sources → 108 claims → 25 verified
(3-vote adversarial, need 2/3 refutes to kill). 23 confirmed, 2 refuted.
**Relationship to [2026-06-18 keyword-seed design](2026-06-18-keyword-seed-design.md):**
this validates most of that doc and refines the bandit + saturation layers with theory.

---

## 0. The one finding that reframes everything

**The flywheel's convergence is a *documented property of the method*, not a bug.**
Google's production deep-web crawl (Madhavan et al., PVLDB 2008) ran the exact same
loop — seed query → harvest → extract new keywords via TF-IDF → re-query — and
**probing self-terminated for 70% of forms** because "no new keywords can be extracted
after a few iterations." A corpus-mined keyword loop is a *focused crawler*; it obeys
topical locality and can only deepen basins it already sits in. Our 1006→114 kept/week
and 6.3% promote rate are that same saturation.

**Corollary (information-theoretic, not a tuning knob):** every query-based metric
suffers **query-vocabulary bias** (Broder et al., CIKM 2006) — it only sees the slice
the current pool can reach. So an entropy/HHI saturation alarm computed *from the
existing keyword pool is structurally blind to basins the pool never queries.* That is
literally why our current diversity metrics don't fire when we need to explore.

⇒ Breadth can only come from an **external fixed frame** (store category taxonomy +
new-releases + typeahead). The flywheel handles depth; the frame handles breadth; the
two must be separate subsystems. This is the same conclusion the 2026-06-18 doc reached
by intuition — now it has a proof and a production precedent.

---

## 1. Four sub-problems → four verified answers

### (a) Query selection — maximize NEW-unique harvest per budget
- **Frame:** budgeted set-cover / p-partial cover, NP-hard, cost = Σ result-set sizes to
  minimize overlap (Wang/Lu 2012). Greedy "new-docs-per-cost" is the tractable policy
  (Ntoulas JCDL 2005: >90% of PubMed in <100 adaptive queries vs 42.5% from a 16k-word
  dictionary in 200). **Corpus-mined > external word lists** — but see §0 for the ceiling.
- **Breadth beats depth:** Google's top 10k forms = 50% of results, top 100k = 85%;
  impact tracked *number of sources surfaced*, not depth per source. Spend budget reaching
  new basins, not re-paging saturated keywords.
- **Meta caveat:** the set-cover papers assume each query returns its FULL match set.
  Meta paginates top-k (~4 pages, impressions-desc). For high-volume keywords we only see
  a truncated slice — accounting and recapture math must be restricted to *rare,
  low-overflow* keywords.

### (b) Budget allocation under DECAYING yield — non-stationary bandits
- **Exact model = rotting bandits** (Levine/Crammer/Mannor, NeurIPS 2017): an arm's
  expected reward is non-increasing in # pulls (**rested** — depletion from re-paging)
  and/or in wall-clock (**restless** — corpus turnover as new ads post). Under *known
  rested* decay, **myopic greedy is provably optimal** (Lemma 2.1) — a greedy watermark
  scheduler needs no lookahead.
- **Reward signal = recent-window average of new-unique yield, NOT lifetime average**
  (FEWA, Seznec AISTATS 2019): "the older a sample, the less representative." Anytime,
  no parametric decay model, O(√KT) regret.
- **Recommended target = RAW-UCB** (Seznec AISTATS 2020): one parameter-light UCB index,
  **near-minimax in BOTH rested and restless with no prior knowledge of which applies,
  no change-detector, no discount/window tuning.** Index per arm = **min over window
  sizes h of [ mean(last h pulls) + c(h, δ) ]** — valid because under monotone decay every
  windowed average upper-bounds the arm's next value. EFF-RAW-UCB keeps it O(K log t).
- **Impossibility guardrail** (Seznec 2020, Prop. 1): if arms decay by *both* mechanisms
  at once, **no policy is sublinear worst-case** (≥⌊T/8⌋ regret). Meta keywords decay from
  both our harvesting *and* ad turnover ⇒ RAW-UCB is a strong heuristic, **not** a
  guaranteed optimizer — it MUST sit behind a forced-exploration floor. This is the
  theoretical justification for the 2026-06-18 doc's ε-greedy 60/25/15 split.
- **Weaker fallback:** Discounted-UCB / Sliding-Window-UCB (Garivier & Moulines, ALT 2011)
  are theorem-backed but for *abrupt* piecewise-stationary switches, not smooth decay —
  simpler to code, worse fit than RAW-UCB.

### (c) Batch diversity — don't re-mine one cluster per sweep
- **Validated by Google's production step** (Madhavan 2008, §5.1): cluster candidate
  keywords by result-page content signature (top-25 TF-IDF words), keep **one per
  cluster**, then admit a further keyword only if its result page adds **≥k new content**
  vs the union already selected (start k=5, relax). That is MMR / submodular-coverage in
  practice — grounds the internal doc's "MMR keyword selection." Classic backing: MMR
  (Carbonell & Goldstein 1998), submodular greedy 1−1/e (Khuller/Moss budgeted max-cover;
  Leskovec CELF). No effect size reported for the specific step; treat as sound-but-unablated.

### (d) Saturation / coverage of an unknown-size corpus
- **Feasible from recapture data alone** (our `times_seen` IS recapture data):
  - **Chao2 unseen-richness lower bound** uses ONLY singletons Q1 (seen once) + doubletons
    Q2 (seen twice) — frequent items carry ~no info about the unseen (Chao/Colwell/Gotelli,
    MEE 2017).
  - **Two-pool capture-recapture size** |D| = |D_A||D_B| / |D_AB| for *uncorrelated* pools
    (Broder CIKM 2006), correlation-bracketed. Expect ±25%-class accuracy — a **denominator,
    not a precise count**.
- **Two mandatory bias corrections** (Lu et al., Information Retrieval 2010):
  1. **Heterogeneous capture** (random queries ≠ random docs) — negative bias, correct via
     CV/γ of capture probability (Chao & Lee 1992).
  2. **Rank/overflow bias** — top-k truncation is NOT corrected by the estimator; valid
     "only for ranked sources with low overflowing density." ⇒ compute the denominator
     **only from rare keywords swept to exhaustion**, never from high-volume overflowing ones.
- **The blind spot (§0):** these estimate only the *reachable* slice. Detecting *new*
  basins requires deliberately new, uncorrelated pools (the external frame) — no
  pool-internal number can do it.
- **Most actionable per-arm alarm:** novelty-rate = singletons / returned for a keyword
  (Good-Turing p0 = N1/N). When it decays below ~0.05 the keyword is locally saturated →
  reallocate its budget. This is a per-arm signal, immune to the global-pool blind spot.

---

## 2. REFUTED — do not implement (each killed 0-3)

1. **Broder Lemma-1 "unbiased single-pool size" estimator** (weight each returned doc by
   1/#pool-terms-matching, |A|·avg = |D_A|). Refuted — not a valid shortcut to a
   single-pool size without the recapture step.
2. **Closed-form coverage P = 1 − OR^(−2.1)** from overlap ratio alone (OR = total
   retrieved incl. repeats / distinct). Refuted — overlap alone does not yield saturation;
   use Chao2 + capture-recapture with the §1(d) corrections instead.

---

## 3. The algorithm (implementable spec)

Four layers. Layers 0 + 3 are the anti-collapse breadth engine (external, taste-blind
by construction). Layers 1 + 2 make within-frame spend efficient.

```
────────────────────────────────────────────────────────────────────────
LAYER 0 — EXTERNAL FRAME  (breaks vocabulary bias; the diversity backbone)
────────────────────────────────────────────────────────────────────────
state:  coverage[category] = # distinct kept apps found, per in-scope store category
        target K per category (from 2026-06-18 §3 scope list)
each cycle:
  for every category below target:
    mint fresh keywords from  store new-releases(category)  +  typeahead(category name, a–z)
    → these are UNCORRELATED with flywheel keywords by construction
  (zero Meta cost for the store-side minting; Apple/Google endpoints)
gate:   category-frame keywords enter Layer 1 as new arms with optimistic prior

────────────────────────────────────────────────────────────────────────
LAYER 1 — SCHEDULER  (rotting bandit over active keyword arms)
────────────────────────────────────────────────────────────────────────
arm:        one keyword
reward r_t: NEW-UNIQUE advertisers found on sweep t, normalized PER PAGE
            (per-page, not per-query, so rare high-novelty-density keywords
             aren't starved by high-volume-but-saturated ones — open Q4)
history:    keep recent per-sweep r_t (ring buffer), NOT a lifetime counter

index (RAW-UCB, per arm a):
    UCB(a) = min over h in {1..H} of [ mean(last h rewards of a) + c(h, δ_t) ]
    c(h,δ) = sqrt( α · σ² · ln(1/δ_t) / h ),   α≈1.4 (experiments), δ_t=1/t²
new arm:    UCB = +∞ (forced first pull), then falls in naturally

budget split each cycle (forced-exploration FLOOR — mixed setting is unlearnable):
    ~60% EXPLOIT   pull top-UCB active arms
    ~25% EXPLORE   round-robin one sweep into every below-target Layer-0 category
    ~15% FRONTIER  typeahead / app-graph expand the lowest-coverage basin
    (ε high while broadening, decay toward ~0.15 steady state)

retire:     when an arm's novelty-rate (§Layer 3) < 0.05 for N consecutive pulls
            → drop to a slow re-check cadence (do NOT hard-delete: restless
              re-inflation is real — new advertisers enter old keywords)

────────────────────────────────────────────────────────────────────────
LAYER 2 — BATCH DIVERSITY GATE  (before dispatching a sweep batch)
────────────────────────────────────────────────────────────────────────
given the arms Layer 1 wants to pull this cycle:
  cluster them by keyword-embedding (768d Gemini) OR result-page signature
  keep one per cluster
  admit the next only if it adds ≥k new content vs the union already admitted
  (MMR / submodular-coverage; k≈5 relaxing)
⇒ consecutive sweeps can't pool in one topic cluster

────────────────────────────────────────────────────────────────────────
LAYER 3 — SATURATION METERS  (two SEPARATE denominators — never conflate)
────────────────────────────────────────────────────────────────────────
per-arm (actionable):   novelty_rate(a) = singletons_a / returned_a   (Good-Turing)
                        drives Layer-1 retire + explore trigger
reachable-slice size:   Chao2 on rare-keyword recapture (uniques Q1, doubletons Q2)
                        + capture-recapture |D|=|D_A||D_B|/|D_AB| across two
                        UNCORRELATED pools (flywheel pool vs category-frame pool)
                        with CV/γ heterogeneity correction; RARE keywords only
                        (overflow bias voids high-volume keywords)
breadth (the real one): category-frame coverage = #categories ≥ K target / total
                        ← the ONLY meter that can see unexplored basins
alarm:   push EXPLORE when  category-coverage < target  OR  global novelty-rate → 0
         (NOT when pool-internal HHI rises — that number is blind, per §0)
```

**Taste-blindness** is preserved throughout: every reward is *new-unique yield /
coverage*, never user preference. Keyword minting from new-releases is the human-gated
external frame. Nothing here biases the scraper toward likes.

---

## 4. What to build first (impact order)

1. **Layer 0 + Layer 3 breadth meter** — the external frame + category-coverage alarm.
   This is the actual fix for convergence; without it the bandit just optimizes a
   shrinking basin. (Renews the deferred half of the 2026-06-18 flywheel work.)
2. **Layer 1 reward change** — swap lifetime keyword counters for a recent-window
   new-unique-per-page signal. Cheap, high-leverage; makes decay visible.
3. **Layer 1 RAW-UCB scheduler** behind the ε-floor. Replaces the flat promote/retire rule.
4. **Layer 2 MMR gate** — reuse the existing 768d embeddings.
5. **Layer 3 Chao2 size denominator** — nice-to-have instrumentation; gate on rare-keyword
   recapture only.

## 5. Open questions to settle with a live probe (before coding the bandit)

1. **Rested vs restless dominance?** Re-sweep a fixed keyword set daily, measure
   new-unique vs repeat capture. If mostly rested → greedy/FEWA suffices. If mixed →
   the ε-floor is mandatory (theory branches sharply here).
2. **Overflow rate?** What fraction of swept keywords hit the ~4-page truncation? Sets
   which keywords are usable for Chao2 and how big the rank-bias blind spot is.
3. **Frame correlation?** How uncorrelated are category-minted keywords from
   flywheel-minted ones in practice (measure c1/c2)? Determines whether Layer 0 is a
   valid second pool for capture-recapture.
4. **Reward normalization?** new-unique per page vs per query vs novelty-distance-weighted
   — which keeps rare high-novelty keywords from being starved?

## Sources (all peer-reviewed primaries; full list in the research transcript)
Madhavan et al. *Google's Deep-Web Crawl*, PVLDB 2008 · Ntoulas et al. JCDL 2005 ·
Wang/Lu et al. *Selecting Queries from Sample*, WIAS 2012 · Levine/Crammer/Mannor
*Rotting Bandits*, NeurIPS 2017 · Seznec et al. *FEWA*, AISTATS 2019 · Seznec et al.
*single RAW-UCB for rested+restless*, AISTATS 2020 · Garivier & Moulines *D-UCB/SW-UCB*,
ALT 2011 · Broder et al. *Estimating Corpus Size via Queries*, CIKM 2006 · Lu et al.
*Estimating deep web size by capture-recapture*, Information Retrieval 2010 ·
Chao/Colwell/Gotelli, Methods in Ecology & Evolution 2017 · Carbonell & Goldstein
*MMR* 1998 · Khuller/Moss *Budgeted Max Coverage*.
