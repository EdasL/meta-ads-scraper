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

## 5. Probe results (2026-07-02, from `discovery.sweep_runs` + `candidates`)

The worker already re-sweeps a **fixed ~301-keyword set every ~2h** — that IS the
controlled re-sweep probe, running for 3+ weeks (308 logged sweeps). So Q1/Q2 are
answered from existing data; no proxy spend needed.

1. **Rested vs restless — RESTED-dominated, and the set is dry.** New-unique per keyword:
   **1.43 (Jun 10, fresh set) → 0.30 (Jun 11) → 0.002–0.016 (Jun 12 onward, flatlined
   for 3 weeks).** Re-sweeping the same keywords crashed yield ~100× within two days and
   it never recovered = textbook rested depletion. Because the set is fixed, any
   `candidates_new` IS restless replenishment (new ads on old keywords) — that runs at
   **~1–6 new per full sweep (~0.01/kw)**: real but negligible. ⇒ Under rested decay
   myopic-greedy/RAW-UCB is well-founded; keep the tiny restless trickle by dropping dry
   keywords to a **slow re-check, never hard-delete** — do NOT need the mixed-setting
   worst-case machinery, but keep the ε-floor for the frontier lane.
2. **Proxy waste is the headline.** Last 7 days: **69,066 SERP requests → 215 new
   candidates = 321 requests per new find**, with **66,501 repeat-pulls** re-confirming
   known ads. ~99.7% of proxy budget re-confirms the corpus. The ~2h re-sweep cadence on
   a set that replenishes ~1–6/sweep is pure waste — **cut cadence to daily (slower for
   dry keywords) and redirect that budget to explore/frontier.** Quick independent win,
   shippable before the bandit.
3. **Overflow — low.** 3.96 pages/keyword = every keyword paged to the
   `AGF_SWEEP_PAGES=4` max, but per-keyword candidate counts are small ⇒ most keywords
   are NOT truncated ⇒ Chao2 recapture is broadly valid. (True page-4-full rate still
   wants a one-shot `page_info` probe; minor.)
4. **Chao2 on the current pool: ~92% of the REACHABLE slice already mined**
   (S_obs=3,517; Q1=410 singletons, Q2=268 doubletons → Chao2≈3,831 → 91.8% discovered).
   This is the vocabulary-bias trap made concrete: the number looks reassuring but only
   measures the box we're already in — 92%-done of a pool blind to unexplored basins.
   It is the strongest possible evidence that the fix is **new arms (Layer 0), not better
   scheduling of the existing pool.**

**Bottom line the probe settles:** better scheduling of the current keywords is
rearranging deck chairs — the pool is 92% exhausted and replenishes at a trickle. The
two real levers are (1) **Layer 0 external-frame minting** to add wet arms, and (2)
**stop the 2h re-sweep** to stop burning ~99.7% of the budget on repeats. Build those
first; the RAW-UCB scheduler (Layer 1) then allocates the freed budget across the new
arms.

## 5b. The external frame is already built — but the mechanism is WRONG (2026-07-02)

The worker runs a `release_mint` stage daily (`AGF_RELEASE_MINT=1`,
`src/agf/discovery/release_mint.py`) intended as the Layer-0 external frame. **It
produces ~nothing: 7 candidates all-time (6 kept) vs 2,317 from `niche_term`;
`candidates_new: 0` every run for weeks** despite scraping 40 listings/run.

Bounded live probe (2026-07-02, 10 in-scope new-releases) found the mechanism is
structurally incapable, not mistuned:
- It takes each new-release app's **name** and looks it up in **Meta typeahead** to
  find that app's own advertiser page, then scrapes that page's ads.
- **6/10 app names returned 0 typeahead pages** — brand-new apps have no Meta page
  (they just launched; most don't run ads yet).
- **The 4 that matched were name-collisions with unrelated brands**: fitness app
  "Impulse Mode" → a modeling agency; "Family-Flow" → a florist + a psychiatry clinic;
  "Ponta Negra" → a sushi restaurant.
- **8 listings scraped → 15 ads → 0 candidates** (10 linked to a florist website
  `non_app`, 5 `no_link`). Also: iTunes `newapplications` RSS is a global grab-bag —
  many seeds are non-English / non-commercial ('전월세 임대 계산기', personal names).

**This deviates from what the research (§0, §1a) and the 2026-06-18 doc actually
specify.** The correct Layer-0 mechanism is **keyword minting**, not page-matching:
take new-release listings' **title/subtitle/description → extract head terms →
feed those as SEARCH keywords** into the normal sweep (breadth entry terms), plus the
**category taxonomy** as fixed coverage seeds. New apps don't need to advertise for
this to work — the minted *keyword* finds whoever advertises in that concept space.
Dedup minted keywords against the existing pool with MMR (Layer 2) so they're
genuinely new basins. ⇒ Don't wire coverage targets onto `release_mint`; replace its
mechanism with keyword-minting, then steer THAT with the category-coverage alarm.

## 6. Still open (need a live probe or build)

- **Frame correlation c1/c2** — how uncorrelated are category/new-release-minted keywords
  from flywheel keywords in practice? Determines whether Layer 0 is a valid second pool
  for capture-recapture size estimates. (Measurable once Layer 0 mints its first batch.)
- **Reward normalization** — new-unique per page vs per query vs novelty-distance-weighted,
  so the bandit doesn't starve rare high-novelty keywords. (Decide when coding Layer 1.)
- **True overflow rate** — one-shot check of `page_info.has_next_page` on page 4 across a
  keyword sample, to bound the Chao2 rank-bias blind spot precisely.

## Sources (all peer-reviewed primaries; full list in the research transcript)
Madhavan et al. *Google's Deep-Web Crawl*, PVLDB 2008 · Ntoulas et al. JCDL 2005 ·
Wang/Lu et al. *Selecting Queries from Sample*, WIAS 2012 · Levine/Crammer/Mannor
*Rotting Bandits*, NeurIPS 2017 · Seznec et al. *FEWA*, AISTATS 2019 · Seznec et al.
*single RAW-UCB for rested+restless*, AISTATS 2020 · Garivier & Moulines *D-UCB/SW-UCB*,
ALT 2011 · Broder et al. *Estimating Corpus Size via Queries*, CIKM 2006 · Lu et al.
*Estimating deep web size by capture-recapture*, Information Retrieval 2010 ·
Chao/Colwell/Gotelli, Methods in Ecology & Evolution 2017 · Carbonell & Goldstein
*MMR* 1998 · Khuller/Moss *Budgeted Max Coverage*.
