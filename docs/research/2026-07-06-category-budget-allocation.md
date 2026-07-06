# Category-level budget allocation under structurally-empty strata — research-backed

**Date:** 2026-07-06
**Question:** how should the 40-slot/day keyword-mint budget be split across store
categories when yield is heterogeneous and some categories (News/Weather/Shopping) may
be *structurally empty on Meta* rather than merely under-sampled?
**Method:** deep-research harness — 5 angles → 25 sources → 110 claims → 25 verified
(3-vote adversarial, 2/3 refutes to kill). **15 confirmed, 3 refuted, 7 unverified**
(verifier agents hit API-529 on the entire hierarchical-Bayesian cluster — see §5).
**Relationship to prior work:** this is one level *up* from
[2026-07-02 novel-discovery-algorithm](2026-07-02-novel-discovery-algorithm.md) (which
covered the KEYWORD level: RAW-UCB, Chao2, MMR). It validates the shape of the
[2026-07-06 yield-weighted mint budget](../superpowers/specs/2026-07-06-yield-weighted-mint-budget-design.md)
we just shipped and tells us where it's under- vs over-engineered.

---

## 0. The one-line verdict on what we shipped

**Our exploit-heavy split is directionally right; the *fixed* 25% explore lane is the
part the literature does not support.** Greedy-on-yield is empirically the strongest
policy when there are many arms, precisely because it sheds zero-mean arms fast — a
dedicated lane that forces budget into coverage-deficit categories is the exact
over-exploration of bad arms that greedy beats. But the same theorems assume *many*
arms drawn from a prior; our ~2-dozen *fixed* categories violate that, so the answer is
**"make the explore lane a gated trickle that shrinks as evidence of emptiness
accumulates," not "delete exploration."** Two verified tools do that gating cheaply: the
**Rule of Three** (upper bound → abandon) and **singleton/turnover tracking** (lower
bound → keep probing).

---

## 1. Empty vs under-sampled — the two bounds you need (CONFIRMED)

The core statistical trap: a category with 0 finds is ambiguous. Resolving it needs
**two different bounds pointing opposite directions.**

### (a) Rule of Three — the ABANDON signal (upper bound). Vote 3-0.
After `n` dry sweeps with zero finds, the true per-sweep yield rate is **< 3/n at 95%
confidence** (solving (1−p)^n = 0.05). For News/Weather/Shopping: 3/70 ≈ **4.3%**.
- Sources: Jovanović & Levy 1997 (*The American Statistician*); Tuyl et al. 2009 (*Int.
  Statistical Review*). Accurate for n ≳ 20–30.
- **This is the only verified tool that licenses abandonment** — abandonment logically
  needs an *upper* bound on yield, and the richness estimators below are *lower* bounds.
- Caveat: sweeps aren't perfectly i.i.d. (keyword overlap, non-stationary ad supply),
  so 3/n is a defensible **heuristic gate**, not a guarantee. Ideal for an integer
  allocation rule.

### (b) Good-Turing / Chao richness — the KEEP-PROBING signal (lower bound). Vote 3-0.
Chao1 = S_obs + f1²/(2·f2), using only singletons (f1, apps seen exactly once) and
doubletons (f2). **As f1 → 0 the estimate collapses to S_obs = near-exhausted.** Rare
classes "contain nearly all the information about undetected richness"; abundant classes
contain almost none. A singleton-only Good-Turing variant exists (Chiu et al. 2017, MEE)
needing just the species list + singleton count.
- **Directly actionable:** track the *singleton/turnover rate of new kept apps per
  category*. Still minting singletons → undetected finds remain → keep probing. Rare
  signal collapsed → near-exhausted.
- Sources: Chao 1984; Chiu et al. 2017 (10.1111/2041-210X.12768).

### (c) The guardrail against our own bug. Vote 3-0.
Singleton estimators are **asymptotic LOWER bounds** that "underestimate true richness
for heterogeneous populations and small samples (1–5% of the population)" (Chao & Chiu
2014, *Biometrics*; *Briefings in Bioinformatics* 2025, bbaf158). **⇒ A zero-yield
category under LIGHT sampling cannot be declared empty from a small budget alone.** This
is exactly the failure the shipped fix guards against — but it cuts both ways: it argues
for "keep a probe alive," which is *why the explore lane shouldn't be deleted*, only
gated. Also: "no single richness estimator is universally best — it depends on abundance
composition" (3-0), so don't over-invest in one estimator.

---

## 2. The budget-split policy — greedy beats optimism when arms are many (CONFIRMED)

### (a) Greedy's "free exploration." Vote 3-0.
Bayati, Hamidi, Johari & Khosravi (NeurIPS 2020, arXiv:2002.10121): in the many-armed
regime, **subsampled-greedy empirically beats SS-UCB, UCB, and Thompson sampling.** With
many near-optimal arms, greedy quickly discards sub-optimal (incl. zero-mean) arms via
"free exploration," while UCB/TS *systematically over-explore bad arms*.
- **Maps exactly to our observed failure:** the old deficit lane put 100% of budget into
  0-yield categories = textbook over-exploration of bad arms.
- **Caveat (the important one):** this dominance is proven only under a **many-near-
  optimal-arms prior (k ≥ √T)**. It argues for *exploit-heavy weighting*, NOT zero
  exploration — with a small fixed arm set, greedy can suffer *incomplete learning*.

### (b) Reward-to-cost, and cap the active set. Vote 3-0.
Li & Xia (AAAI 2017, RCB-I) + Bayati et al. 2020: under a budget with far more arms than
affordable pulls, the right per-arm quantity is **expected-reward / expected-cost (yield
per unit spend)**, and you should **subsample ~√T arms** and run a finite bandit over
only those, rather than spread probes over all arms.
- **SCOPE — this is a KEYWORD-lane prescription, not a category one.** The arms≫budget
  premise holds for keywords (~40 minted/day from a huge pool) but NOT for ~2-dozen
  fixed categories. So "cap and subsample" belongs one level down; category allocation
  is a small-fixed-arm problem where these theorems don't transfer.

---

## 3. What this means for the shipped code (the actionable part)

Current shipped rule (`coverage.py::mint_budget`): 75% exploit ∝ `kept_recent` (30-day
windowed keeper yield) + **fixed 25%** explore ∝ coverage deficit, eligible-only.

**Keep:**
- Exploit lane ∝ recent yield. This IS greedy-on-yield (§2a) — the strongest verified
  policy. ✓
- Eligibility restriction (only categories with listing material). ✓
- A nonzero explore lane at the small fixed-category level — §1c and §2a's incomplete-
  learning caveat both say don't drop it to zero. ✓

**Change (highest-leverage, both cheap):**
1. **Replace the fixed 25% with a Rule-of-Three gate per category.** A category with a
   dry-sweep count `n` gets its explore allocation **capped/zeroed once 3/n falls below
   a yield floor** (e.g. drop it from the explore lane once n ≳ 45 → 3/n < ~6.7%). This
   directly kills the "budget into proven deserts" failure with a one-line bound instead
   of a flat share. Freed slots roll to the exploit lane (the mechanism already exists).
2. **Feed the KEEP side from singleton/turnover, not raw deficit.** Instead of "how far
   below K is `found`," gate explore eligibility on *is this category still producing
   singletons / new-unique kept apps?* (§1b). A deficit category that is genuinely
   frontier keeps its probe; a deficit category that is a desert (f1 collapsed, or never
   started) loses it. This is the "frontier vs desert" test the shipped lesson asked for,
   now with a cited mechanism.

**Net:** the two-lane split stays, but the explore lane stops being a flat 25% tax and
becomes an evidence-gated trickle — Rule-of-Three trims deserts from the top, singleton-
rate keeps true frontiers alive. No new subsystem; both are per-category scalars
computable from `discovery.candidates` (`times_seen`, `first_seen_at`, `dry_streak`).

**Do NOT:** replace the split with a single UCB/optimistic-prior index over categories.
§2a shows optimism over-explores bad arms in exactly our regime; the verified direction
is greedy-heavy, not a unified optimistic index. (Open Q4 below — the minimum
exploration to avoid incomplete learning at ~2 dozen arms is unquantified.)

---

## 4. REFUTED — do not build on these

1. **Blind-spot mass B_n(τ) via Good-Turing** as an empty-vs-undersampled per-state
   measure (arXiv:2604.05057). Killed 0-3.
2. **"A single probe per category cannot decide empty vs under-sampled without repeated
   same-stratum probing"** (ZINB identifiability, S0378375823000678). Killed 1-2 — so
   repeated same-category probing is NOT proven necessary; a single daily probe + the
   accumulating dry-sweep count is defensible.
3. **Pandora's-box closed-form abandonment threshold μ\* = argmax Γ(µ)** as the stopping
   rule (Microsoft MMAB). Killed 0-3. Our stopping rule is Rule-of-Three, not a
   distribution-derived μ\*.

---

## 5. UNVERIFIED — the hierarchical-Bayesian cluster (re-research before building)

**All 7 unverified claims lost their 3 verifier votes to API-529 infra errors, not to
refutation** — they are neither confirmed nor killed. They form one coherent, directly-
relevant body: **hierarchical / partial-pooling models that borrow strength across
strata** — the natural fit for judging one category's zero against the whole table at a
*small fixed* category set (exactly the regime §2's bandit theorems abandon us in).

- **Gamma-Poisson Shrinker (GPS/MGPS, DuMouchel)** — pharmacovigilance empirical-Bayes;
  fits one gamma-mixture prior across all cells, each posterior shaped by its own counts
  AND the whole table. Shrinks small counts so sporadic single events aren't mistaken
  for signal. (openEBGM, RJ-2017-063.)
- **Zero-inflated Poisson/NB (ZIP/ZINB)** — explicit per-stratum mixing weight for
  "structural zero" vs "sampling zero" (Sim in Medicine 10.1002/sim.8745). The one ZINB
  claim that *did* get votes came in split 1-1 (medium): mechanism textbook-correct, but
  the separation is a probabilistic posterior, unstable under all-zero sparsity, needs
  partial pooling.
- **Continuous local-global shrinkage priors** for quasi-sparse Poisson rates
  (PMC5793680) — a global τ learns overall sparsity, adapts where ZIP/ZINB can't.
- **Retain zero-count strata as informative** (arXiv:2605.27889) — excluding zeros
  biases the pooling; a category's zero IS evidence of a low mean.

**Recommendation:** treat GPS/partial-pooling as *promising-but-unconfirmed*. It is the
most likely principled answer for the small fixed category set, but re-run the verify
pass (the workflow can resume from cache — only the 7 errored claims re-run) before
speccing anything on it. The §3 Rule-of-Three + singleton gate is the ship-now change;
the hierarchical model is a heavier follow-up only if the cheap gates prove insufficient.

---

## 6. Open questions (from the synthesis)

1. **Small-fixed-set policy.** The many-armed greedy/subsampling theorems don't apply at
   ~2-dozen categories. GPS/partial-pooling (§5) is the likely fit — re-research after
   the verifier infra failure.
2. **Non-stationarity of "empty."** A category empty today (emerging apps there don't
   advertise on Meta *now*) may become fertile later. Abandonment should be a **soft
   decay / periodic re-probe**, not a permanent Rule-of-Three cutoff. (Mirrors the
   keyword-level "drop to slow re-check, never hard-delete" from the 2026-07-02 doc.)
3. **The concrete integer rule.** Combine into one 40-slot/day allocation: exploit =
   greedy on yield-per-slot with a floor; explore = deficit ∝ but per-category Rule-of-
   Three gate zeroes a stratum once its dry-sweep count crosses threshold. Tune the
   floor + threshold on backtest.
4. **Minimum exploration at ~2 dozen arms.** Greedy's dominance needs the many-arm
   prior our fixed set violates; what's the least exploration that avoids incomplete
   learning? Unquantified by the sources.

## Sources (all peer-reviewed primaries)
Chao 1984 · Chiu/Chao et al. 2017 (MEE 10.1111/2041-210X.12768) · Chao & Chiu 2014
(*Biometrics*) · *Briefings in Bioinformatics* 2025 (bbaf158) · Jovanović & Levy 1997
(*The American Statistician*) · Tuyl et al. 2009 (*Int. Stat. Review*) · Li & Xia 2017
(AAAI, Infinitely Many-Armed Bandits w/ Budget) · Bayati/Hamidi/Johari/Khosravi 2020
(NeurIPS, arXiv:2002.10121) · Koopman 1957 (*Operations Research*, search-effort) ·
DuMouchel GPS / openEBGM (RJ-2017-063) [unverified] · ZIP/ZINB (sim.8745,
S0378375823000678) [unverified] · local-global shrinkage (PMC5793680) [unverified].
Full transcript: workflow wf_d62801e9-5a3.
