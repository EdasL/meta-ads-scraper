# Crowding penalty — design spec (2026-07-10)

## Why (measured, not vibes)

Product thesis (Edas, 2026-07-10): find interesting/useful apps, not saturated
niches. No pipeline stage encodes "saturated" — all gates are size gates, and the
keyword flywheel promotes on raw candidate volume, which rewards clone-rich niches.

Measurement (1,137 rated apps × frontend.app_embeddings, mean cos-sim to 10 NN):
like-rate by crowding quartile = 7.7% / 11.6% / 8.8% / **2.8%** — the most-crowded
quartile (mean-10NN-sim ≥ ~0.80) is ~4σ worse, dislike 84%. Apps with 11+ near-clones:
0/8 liked. The crowded TAIL is poison; extreme novelty is NOT better than "novel-ish"
→ build a **tail penalty**, not a linear novelty boost.

## What ships (scope locked with Edas)

1. **Crowding score** stored per embedding (candidates + apps), recomputed each cycle.
2. **Keyword trial promotion** counts only non-crowded yield.
3. **Enrich priority** demotes crowded candidates.
4. **Go review queue** demotes crowded gems at serve time.
5. **One-off audit** rests existing ACTIVE keywords whose finds are mostly crowded
   (kills "calorie tracking" et al. on day one. KNOWN GAP, semi-by-design: the
   resting→active revive path still runs on raw yield — a rested offender that keeps
   finding clones wakes and re-activates after its cooldown. Serve-time demotion
   bounds the damage; durable lifecycle gating is a follow-up. Re-run the audit
   periodically meanwhile.)

Out of scope: Discover "foryou" sort (still reads stale preference_scores — known
separate issue), GemScore formula changes, funnel keywords.

## Definitions

- **crowding(v)** = mean cosine similarity of embedding v to its k nearest neighbors
  in the kept-candidate embedding corpus (self excluded). k = `AGF_CROWDING_K` (10).
  Fewer than k neighbors available → use what's there; empty/one-row corpus → NULL.
- **crowded** = crowding ≥ `AGF_CROWDING_TAIL` (0.80 — the measured top-quartile edge).
- **Degrade OPEN everywhere**: missing embedding / NULL crowding = NOT crowded.
  The penalty must never starve discovery or empty the review deck.

## Components

### A. Migration 150 — `db/schema/150_crowding.sql`
```sql
ALTER TABLE discovery.candidate_embeddings ADD COLUMN IF NOT EXISTS crowding real;
ALTER TABLE frontend.app_embeddings       ADD COLUMN IF NOT EXISTS crowding real;
```
(No index needed — always read row-wise alongside the embedding. NO bare `%` anywhere.)

### B. Pure module — `src/agf/discovery/crowding.py`
```python
def crowding_scores(vectors: np.ndarray, corpus: np.ndarray, k: int = 10) -> np.ndarray:
    """mean cos-sim of each row in `vectors` to its k NN in `corpus` (self-matches
    excluded by masking exact-duplicate max-sim columns is NOT enough — callers pass
    corpus WITHOUT the row itself, or use leave-one-out below)."""

def crowding_scores_self(corpus: np.ndarray, k: int = 10) -> np.ndarray:
    """leave-one-out variant: crowding of every corpus row vs the rest (diagonal
    masked). Used by the cycle recompute."""
```
Normalize rows; matmul; top-k mean. 3.4k×3.4k×768 ≈ 2 s numpy — fine per cycle.

### C. Cycle recompute — wire into the two embed stages in `workers/main.py`
- After `embed_candidates` (step 2c): load ALL kept-candidate embeddings, compute
  `crowding_scores_self`, UPDATE `discovery.candidate_embeddings.crowding` in one
  batched statement. Full recompute every cycle (crowding drifts as corpus grows;
  recompute is cheaper than staleness bookkeeping). This IS the backfill — first
  deployed cycle populates all 3,351.
- After `embed_apps` (step 2a): same for `frontend.app_embeddings` (1,949 rows).
- All numpy work OUTSIDE any write transaction (lesson: never hold a txn over bulk
  compute); the UPDATE is its own short txn.

### D. Promotion on uncrowded yield — `src/agf/db/dynamic_keywords.py`
- New query `crowded_candidate_count(conn, keyword) -> int`: candidates with
  `source_keyword = keyword` joined to `candidate_embeddings` where
  `crowding >= tail`. (Dropped-at-triage candidates have no embedding → not crowded
  → degrade open; today's behavior preserved for them.)
- Trial verdict changes from
  `new_candidates_total >= PROMOTE_MIN_NEW` to
  `new_candidates_total - crowded_candidate_count >= PROMOTE_MIN_NEW`.
  Applied in BOTH verdict paths (`record_keyword_sweep`, `apply_verdicts`).
- Threshold value PROMOTE_MIN_NEW unchanged — the metric tightens, the bar doesn't.
- Actives/resting: lifecycle untouched this increment (audit script covers actives).

### E. Enrich priority — `src/agf/discovery/triage_prior.py` + caller
- `prioritize(..., crowding_by_id=None, tail=0.80, penalty=0.5)`: score becomes
  `cos(emb, direction) - (penalty if crowding_by_id.get(a, 0) >= tail else 0)`.
  Cosine ∈ [-1,1], so penalty 0.5 pushes a crowded candidate behind ~everything
  with positive taste signal without -inf'ing it. Explore lane stays blind (it's
  the escape hatch by design).
- Caller in `workers/main.py` already fetches embeddings for the kept queue —
  extend that SELECT to also return `crowding`, pass the dict through.

### F. Go review queue — `backend/`
- `repositories/pref_embeddings.go` (or embeddings_repo.go — wherever the review
  deck's embedding/score read lives): select `crowding` alongside the embedding.
- `internal/reco/content_model.go` scoring path: after preference score is computed,
  apply `if crowding >= tail { score *= (1 - CrowdingPenalty) }` with
  tail/penalty from env (`AGF_CROWDING_TAIL` default 0.80,
  `AGF_CROWDING_PENALTY_GO` default 0.5). NULL crowding → no penalty.
- Fixture test: given equal pref scores, crowded gem ranks below uncrowded;
  NULL-crowding unaffected; penalty=0 disables cleanly.

### G. One-off audit — `scripts/audit_crowded_keywords.py`
- For each ACTIVE dynamic keyword: kept_n = kept candidates with crowding NOT NULL;
  crowded_share = crowded/kept_n. If `kept_n >= 3 AND crowded_share >= 0.6` →
  offender.
- Default = dry-run report (keyword, kept_n, crowded_share, sample app names).
  `--apply` rests offenders: `status='resting', rest_count=rest_count+1,
  next_eligible_at = now() + cooldown` (reuse `cooldown_days`; use `mod()`-free SQL).
- Run once after the first crowding-populated cycle; keep the script for re-runs.

## Env knobs (all optional, defaults in code)
`AGF_CROWDING_K=10` · `AGF_CROWDING_TAIL=0.80` · `AGF_CROWDING_PENALTY=0.5` (python
enrich) · `AGF_CROWDING_PENALTY_GO=0.5` (review queue).

## Test plan
- Pure: crowding math (known-geometry fixtures; leave-one-out masks self; k > corpus).
- DB: recompute stage populates + is idempotent; promotion verdict with crowded /
  uncrowded / no-embedding fixtures (RED first on the old behavior);
  crowded_candidate_count join correctness.
- Pure: prioritize penalty ordering + degrade-open + explore lane untouched.
- Go: scorer demotion fixture test (golden ordering).
- Script: audit dry-run vs --apply on seeded keywords (sentinel-scoped, shared DB safe).
- Migration: applies to a FRESH db (placeholder-safe lint already exists).
- Full suite to 0 failed. `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`.

## Live verify (the real gate)
1. Deploy worker image; let one cycle run embed stages → `SELECT count(*) FROM
   discovery.candidate_embeddings WHERE crowding IS NOT NULL` ≈ 3,351.
2. Sanity: known clone ("GLP-1 & Calorie Tracker" advertiser) crowding ≥ 0.80;
   known novel (Preflop Wizard) < 0.80.
3. Audit dry-run: offenders list must contain the known trawling nets
   ("calorie tracking" expected); eyeball, then `--apply`; verify rests recorded.
4. Go deploy; review queue request (token + X-User-Email) — crowded gem's serve
   order drops vs yesterday; NULL-crowding gems unaffected.
5. Watch minted-trial promotion over next days (uncrowded bar) — note in memory.

## Rollout order
Migration 150 → worker (compute + promotion + enrich penalty are all safe with
NULL crowding before first recompute) → audit after first populated cycle →
Go API last (reads may see NULL until worker cycle runs; NULL = no penalty = safe).
