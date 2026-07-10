# Crowding penalty — implementation plan (2026-07-10)

Spec: `docs/superpowers/specs/2026-07-10-crowding-penalty-design.md`. Read it first.
TDD per task: write the RED test, watch it fail, implement, GREEN, commit that task
alone with explicit paths. Test DB: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`
(scope all DB fixtures to sentinel ids — the DB is shared). Full suite must stay 0 failed.
Plan code is illustrative — typecheck/lint after transcribing and expect to adapt.

## T1 — pure crowding module

New `src/agf/discovery/crowding.py`:

```python
"""Embedding-space crowding: mean cosine similarity to the k nearest corpus
neighbors. High crowding = the app sits in a clone cluster (measured 2026-07-10:
top-quartile crowding ≥0.80 → like-rate 2.8% vs 8-12%). Pure numpy, no DB."""
from __future__ import annotations
import numpy as np

def _normalize(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n

def crowding_scores(vectors: np.ndarray, corpus: np.ndarray, k: int = 10) -> np.ndarray:
    """Crowding of each row of `vectors` vs `corpus` (which must NOT contain the row
    itself). corpus with 0 rows -> all NaN. k capped at corpus size."""

def crowding_scores_self(corpus: np.ndarray, k: int = 10) -> np.ndarray:
    """Leave-one-out: crowding of every corpus row vs the others (diagonal masked
    to -inf before top-k). corpus of 0/1 rows -> all NaN."""
```

Tests `tests/discovery/test_crowding.py` (pure, no DB): identical vectors → crowding
≈1.0; orthogonal one-hot vectors → ≈0.0; k > corpus size uses available; leave-one-out
excludes self (a duplicate pair scores 1.0 against each other, not against itself);
0/1-row corpus → NaN; zero-vector row doesn't crash.

## T2 — migration 150 + cycle recompute

`db/schema/150_crowding.sql` exactly as in spec §A (two ADD COLUMN IF NOT EXISTS; no
bare `%` — the placeholder lint will catch it).

New `refresh_crowding(conn, *, table, k, tail_unused=None)` — put it in
`src/agf/discovery/crowding_refresh.py` (DB glue kept out of the pure module):
- SELECT id/advertiser_id + embedding from the table
  (`discovery.candidate_embeddings` keyed by candidate_id joined to
  `discovery.candidates.status='kept'`; `frontend.app_embeddings` keyed by
  advertiser_id, all rows),
- numpy `crowding_scores_self` OUTSIDE any txn,
- one batched UPDATE (executemany / VALUES join), NaN → NULL.

Wire in `src/agf/workers/main.py`:
- after `embed_apps` (~line 753) → refresh `frontend.app_embeddings`,
- after `embed_candidates` (~line 779) → refresh `discovery.candidate_embeddings`.
Both inside the existing best-effort try blocks of those stages (a crowding failure
must never kill the cycle) and printed to stats
(`crowding_refreshed=<n>`). Full recompute per cycle IS the backfill.

DB tests `tests/discovery/test_crowding_refresh.py`: seed 3 sentinel embeddings
(two near-identical, one orthogonal) → refresh → crowding populated, clone pair high,
loner low/NULL-safe; idempotent on re-run; rows outside sentinel untouched.

## T3 — promotion on uncrowded yield

`src/agf/db/dynamic_keywords.py`:

```python
def crowded_candidate_count(conn, keyword: str, *, tail: float) -> int:
    return conn.execute(text(
        "SELECT count(*) FROM discovery.candidates c "
        "JOIN discovery.candidate_embeddings e ON e.candidate_id = c.id "
        "WHERE c.source_keyword = :kw AND e.crowding >= :tail"),
        {"kw": keyword, "tail": tail}).scalar_one()
```

- Env accessor `crowding_tail()` → `AGF_CROWDING_TAIL` default 0.80 (module pattern
  matches the existing `_env_int` accessors).
- In BOTH trial-verdict paths (`record_keyword_sweep` and `apply_verdicts`), the
  promote condition becomes
  `row.new_candidates_total - crowded_candidate_count(conn, row.keyword, tail=crowding_tail()) >= promote_min_new()`
  (fetch `keyword` in the RETURNING/SELECT — it isn't currently returned everywhere).
  Retire/rest logic untouched. No embeddings ⇒ count 0 ⇒ today's behavior (degrade open).

DB tests `tests/db/test_dynamic_keywords_crowding.py` (RED first against old logic):
trial with 5 new candidates all crowded → NOT promoted (retired at threshold);
5 new, 0 crowded → promoted; 5 new, no embeddings at all → promoted (degrade open);
apply_verdicts path exercised too.

## T4 — enrich-priority penalty

`src/agf/discovery/triage_prior.py`: `prioritize(fifo_order, emb_by_id, direction,
explore_frac=0.25, crowding_by_id=None, tail=0.80, penalty=0.5)`; inside `score(a)`
subtract `penalty` when `crowding_by_id and (crowding_by_id.get(a) or 0) >= tail`.
Explore lane untouched.

`src/agf/workers/main.py` `taste_prioritized_kept` (def near line 85's import; find by
content): extend the embedding fetch to also select `crowding` and pass
`crowding_by_id` + env-driven tail/penalty (`AGF_CROWDING_PENALTY` default 0.5).

Pure tests in `tests/discovery/test_triage_prior.py` (extend existing file): equal-taste
crowded vs uncrowded → uncrowded first; crowding_by_id=None → byte-identical to old
output; explore slots unchanged; missing id in dict → no penalty.

## T5 — Go review-queue penalty

- `backend/repositories/pref_embeddings.go` (`prefEmbeddings`, ~line 52): SELECT adds
  `crowding` (nullable float8 → `sql.NullFloat64`/pointer map alongside the vectors —
  return a second map or a struct; pick what the call site makes cleanest).
- Scoring call site (controller/service that ranks the review deck via
  `ContentModel.Score`/`PLike` — trace from `backend/controllers/preference_controller.go`):
  after computing the ranking score, apply
  `if crowding != nil && *crowding >= tail { score *= (1 - penalty) }`,
  tail from `AGF_CROWDING_TAIL` (0.80), penalty from `AGF_CROWDING_PENALTY_GO` (0.5),
  parsed once at construction like existing env reads.
- Go tests (same package as the ranking logic): fixture of three gems with equal
  pref score — crowded ranks last; nil crowding unaffected; penalty=0 disables.
- `go build ./... && go vet ./... && go test ./...` green.

## T6 — audit script

`scripts/audit_crowded_keywords.py` (mirror the structure of existing scripts/, argparse):
- For each `discovery.dynamic_keywords` row with `status='active'` (kind='app'):
  `kept_n` = its source_keyword candidates with status='kept' AND crowding NOT NULL;
  `crowded_share` = crowded(≥tail)/kept_n.
- Offender: `kept_n >= 3 AND crowded_share >= 0.6` (both argparse-overridable).
- Default dry-run prints a report line per offender (keyword, kept_n, share, top 3
  crowded app page_names); `--apply` sets
  `status='resting', dry_streak=0, rest_count=rest_count+1,
  next_eligible_at=now()+make_interval(days => :cd)` with `cd=cooldown_days(rest_count+1)`
  computed in Python (NOT SQL `%`/interval literals — the migration lint lesson applies
  to any executed SQL).
- DB tests `tests/scripts/test_audit_crowded_keywords.py`: sentinel keywords, one
  offender one clean; dry-run mutates nothing; --apply rests only the offender.

## T7 — controller-run finish (no subagent)

Whole-branch opus review → fix blockers → full suite bare (own Bash call) → clean-HEAD
worktree Cloud Build → VM compose roll (compose is hand-edited on the VM — edit BOTH) →
migration 150 applies on prod → one worker cycle → live checks per spec §Live verify
(counts, GLP-1 vs Preflop Wizard sanity, audit dry-run then --apply with Edas-visible
report, Go deploy + review-queue probe) → memory + handoff update.

## Task order & concurrency

T1 → T2 → {T3, T4 sequential (both touch main.py/dynamic_keywords ecosystems)} →
T5 (Go, disjoint tree — may run while T6 python task is under review, but NEVER two
python implementers at once) → T6 → T7. Reviewer+next-implementer overlap allowed;
implementer+implementer NOT.
