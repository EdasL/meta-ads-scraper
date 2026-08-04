# Review Taste + Diversity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the review deck stop re-serving one embedding cone: multi-centroid taste model (both languages), a decayed per-topic cap seeded from recent ratings, and neutral ratings as weak negatives.

**Architecture:** The Python `ContentPreferenceModel` stays canonical; the Go `reco` package is a port pinned by golden-vector parity (tolerance 1e-6). Clustering is deterministic (no RNG): inputs sorted by advertiser_id, farthest-point init from index 0, Lloyd's with first-max ties. The decayed cap needs no new state — recent `app_ratings` rows (which ARE the served-card trail, since the deck is rate-then-advance) pre-seed the existing per-niche cap counter.

**Tech Stack:** Go (backend/), Python+numpy (src/agf/), Postgres test DB.

**Spec:** `docs/superpowers/specs/2026-08-04-review-taste-diversity-design.md`

## Global Constraints

- No schema migrations. `frontend.app_ratings.rated_at` and `frontend.app_topics` already exist.
- Env knobs, all optional: `AGF_NEUTRAL_WEIGHT` (default 0.25), `AGF_TASTE_LIKES_PER_CENTROID` (default 12), `AGF_TASTE_CENTROIDS_MAX` (default 4), `AGF_TASTE_SPLIT_MAX_COS` (default 0.85), `AGF_REVIEW_CAP_HOURS` (default 24; 0 disables seeding).
- STRUCTURE GUARD (added after the 2026-08-04 prod measurement — founder's likes are ONE blob, best-k centroid pair at cosine 0.92): after computing cluster directions, if ANY pair of directions has dot > `AGF_TASTE_SPLIT_MAX_COS`, the split is noise — collapse `dirs` to the single global direction. Both languages, golden-pinned.
- Model version string bumps `content-sim-v1` → `content-sim-v2`.
- `ContentPreferenceModel.direction` (the GLOBAL single like−dislike direction) must keep working — `src/agf/discovery/triage_prior.py` consumes it.
- Go/Python parity tolerance 1e-6 (existing golden test convention).
- Test DB: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`. Python suite: `PYTHONPATH=src .venv/bin/python -m pytest`. Go suite: `cd backend && go test ./...`.
- ONE test consumer of the shared test DB at a time; final zero-failed full-suite runs happen bare, solo, as their own Bash call.
- Commit per task with pathspec (`git add <exact files>` + commit in one Bash call).

## File Structure

- `src/agf/scoring/content_preference.py` — model v2: `_cluster`, weighted negative centroid, multi-direction scoring (Task 1)
- `tests/scoring/test_content_preference_multicentroid.py` — new Python tests (Task 1)
- `scripts/score_content_preferences.py` — pass neutrals (Task 1)
- `backend/internal/reco/content_model.go` — Go port of v2 (Task 2)
- `backend/internal/reco/content_model_test.go` — regenerated goldens + cluster tests (Task 2)
- `backend/repositories/preference_queue.go` — `nicheKeyFrom`, `SeedNicheCounts`, cap-free pass (Task 3)
- `backend/repositories/preference_queue_test.go` — new assemble tests (Task 3)
- `backend/repositories/preference_repo.go` — rated-rows query + neutral wiring + seed counts (Task 4)
- `backend/repositories/preference_repo_test.go` — DB-backed decayed-cap test (Task 4)

---

### Task 1: Python model v2 — deterministic clustering, multi-centroid scoring, neutrals as weak negatives

**Files:**
- Modify: `src/agf/scoring/content_preference.py`
- Modify: `scripts/score_content_preferences.py:33-38`
- Test: `tests/scoring/test_content_preference_multicentroid.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ContentPreferenceModel.fit(liked, disliked, neutral=None, min_likes=MIN_LIKES, neutral_weight=None, likes_per_centroid=None, max_centroids=None)` where liked/disliked/neutral are lists of `(advertiser_id, name, vec)` tuples; `model.version == "content-sim-v2"` when fitted; `model.score(vec)` = max over cluster directions; `model.direction` still the global single unit vector; module function `_cluster(vecs, k, iters=50) -> list[int]`. Task 2's Go port must match these semantics exactly.

- [ ] **Step 1: Write the failing tests**

Create `tests/scoring/test_content_preference_multicentroid.py`:

```python
import numpy as np

from agf.scoring.content_preference import ContentPreferenceModel, _cluster


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def _mk(n, base, name, start_id=0):
    """n near-duplicates of base with a tiny per-item wiggle, ids ascending."""
    out = []
    for i in range(n):
        v = np.asarray(base, float).copy()
        v[2] += 0.05 * i
        out.append((start_id + i, f"{name}{i}", _unit(v)))
    return out


def _two_cluster_likes():
    # Two well-separated like clusters: near e1 and near e2 (dim 4).
    a = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    b = _mk(12, [0.0, 1.0, 0.05, 0.0], "B", start_id=200)
    return a + b


def test_cluster_deterministic_and_separates():
    likes = _two_cluster_likes()
    vecs = [v for _, _, v in likes]
    assign = _cluster(vecs, 2)
    # First 12 in one cluster, last 12 in the other.
    assert len(set(assign[:12])) == 1
    assert len(set(assign[12:])) == 1
    assert assign[0] != assign[12]
    # Deterministic: same input, same result.
    assert assign == _cluster(vecs, 2)


def test_multicentroid_scores_both_clusters_high():
    likes = _two_cluster_likes()
    dislikes = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    m = ContentPreferenceModel.fit(likes, dislikes)
    assert m.version == "content-sim-v2"
    pa = m.p_like(m.score(_unit([1, 0, 0, 0])))
    pb = m.p_like(m.score(_unit([0, 1, 0, 0])))
    # v1 (single mean direction) scores the e2 cluster's probe at ~the midpoint;
    # v2 must score BOTH cluster probes clearly likeable.
    assert pa > 0.8
    assert pb > 0.8
    # Off-taste probe stays low.
    assert m.p_like(m.score(_unit([0, 0, 0, 1]))) < 0.5


def test_fit_order_invariant_after_id_sort():
    likes = _two_cluster_likes()
    dislikes = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    m1 = ContentPreferenceModel.fit(likes, dislikes)
    m2 = ContentPreferenceModel.fit(list(reversed(likes)), dislikes)
    probe = _unit([0.5, 0.5, 0, 0])
    assert abs(m1.score(probe) - m2.score(probe)) < 1e-12


def test_neutrals_pull_negative_centroid():
    likes = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    neut = _mk(6, [0.0, 1.0, 0.05, 0.0], "N", start_id=500)
    m_plain = ContentPreferenceModel.fit(likes, [], likes_per_centroid=12,
                                         max_centroids=1)
    m_neut = ContentPreferenceModel.fit(likes, [], neutral=neut,
                                        likes_per_centroid=12, max_centroids=1,
                                        neutral_weight=0.25)
    probe = _unit([0, 1, 0, 0])   # looks like the neutral'd cluster
    assert m_neut.score(probe) < m_plain.score(probe)


def test_neutrals_excluded_from_platt_labels():
    # Fitting with neutrals must not add 0-labels: same likes/dislikes with and
    # without neutrals of IDENTICAL vectors to the dislikes leaves calibration
    # input size unchanged (we assert indirectly: p_like of a strong like-probe
    # stays high — neutral flooding must not crush calibration).
    likes = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    dis = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    neut = _mk(40, [0.0, 0.9, 0.1, 0.0], "N", start_id=1000)
    m = ContentPreferenceModel.fit(likes, dis, neutral=neut)
    assert m.p_like(m.score(_unit([1, 0, 0, 0]))) > 0.8


def test_k_policy_clamps():
    likes = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    # 12 likes / 12-per-centroid = 1 cluster; behavior = v1 + version bump.
    m = ContentPreferenceModel.fit(likes, [], likes_per_centroid=12,
                                   max_centroids=4)
    assert m.version == "content-sim-v2"
    assert m.direction is not None
    # direction (global) and the single cluster dir agree.
    assert abs(m.score(m.direction) - float(np.dot(m.direction, m.direction))) < 1e-9


def test_structure_guard_collapses_blob():
    # One blob forced into k=4: every split direction nearly coincides, so the
    # guard must fall back to the single global direction (identical scores to
    # an explicit K=1 fit).
    likes = _mk(12, [1.0, 0.0, 0.05, 0.0], "A", start_id=100)
    dis = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    m_multi = ContentPreferenceModel.fit(likes, dis, likes_per_centroid=1,
                                         max_centroids=4, split_max_cos=0.85)
    m_single = ContentPreferenceModel.fit(likes, dis, likes_per_centroid=12,
                                          max_centroids=1)
    for probe in ([1, 0, 0, 0], [0, 1, 0, 0], [0.5, 0.5, 0.5, 0]):
        v = _unit(probe)
        assert abs(m_multi.score(v) - m_single.score(v)) < 1e-12


def test_structure_guard_keeps_real_clusters():
    # Genuinely separated clusters stay split: scores must NOT equal the
    # single-direction fit for a probe aligned with the second cluster.
    likes = _two_cluster_likes()
    dis = _mk(2, [0.0, 0.0, 0.0, 1.0], "D", start_id=900)
    m = ContentPreferenceModel.fit(likes, dis, likes_per_centroid=12,
                                   max_centroids=4, split_max_cos=0.85)
    m1 = ContentPreferenceModel.fit(likes, dis, likes_per_centroid=24,
                                    max_centroids=1)
    probe = _unit([0, 1, 0, 0])
    assert m.score(probe) > m1.score(probe) + 0.05


def test_coldstart_unchanged():
    likes = _mk(3, [1.0, 0.0, 0.05, 0.0], "A")
    m = ContentPreferenceModel.fit(likes, [])
    assert m.version == "coldstart"
    assert m.score([1, 0, 0, 0]) == 0.0
    assert m.direction is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_content_preference_multicentroid.py -v`
Expected: FAIL — `_cluster` import error.

- [ ] **Step 3: Implement model v2**

Rewrite `src/agf/scoring/content_preference.py` (keep `_l2`, `_fit_platt`, `MIN_LIKES` as-is; module docstring updated to describe v2):

```python
"""Per-user similarity-to-likes ranker over content embeddings (v2, multi-centroid).

Likes are clustered (deterministic k-means, no RNG) and each cluster gets its own
direction = normalize(mean(cluster) - dis_c); score(app) = max over cluster
directions of dot(app, dir). dis_c is the weighted mean of disliked plus
AGF_NEUTRAL_WEIGHT x neutral vectors (neutrals shape the negative centroid but are
excluded from Platt calibration). `direction` stays the GLOBAL single
like-minus-dislike unit vector for consumers that need one axis (triage prior).
The Go port (backend/internal/reco) is pinned to this file by golden vectors —
keep the arithmetic order identical.
"""
from __future__ import annotations

import os

import numpy as np

MIN_LIKES = 8


def _env_float(key, default):
    try:
        return float(os.environ.get(key) or default)
    except ValueError:
        return default


def _env_int(key, default):
    try:
        return int(os.environ.get(key) or default)
    except ValueError:
        return default
```

`_cluster` (module-level, after `_fit_platt`):

```python
def _cluster(vecs, k, iters=50):
    """Deterministic k-means over unit vectors. Farthest-point init from index 0
    (next center = the point with the smallest max-similarity to chosen centers,
    first-min wins); Lloyd's assignment by highest dot, first-max wins; centers
    are l2-normalized member means; empty clusters keep their old center. No RNG.
    Returns the per-vector cluster index list. The Go port must match exactly."""
    n = len(vecs)
    k = min(k, n)
    if k <= 1 or n == 0:
        return [0] * n
    V = np.asarray(vecs, float)
    chosen = [0]
    while len(chosen) < k:
        best_i, best_sim = -1, np.inf
        for i in range(n):
            if i in chosen:
                continue
            sim = max(float(V[i] @ V[c]) for c in chosen)
            if sim < best_sim:
                best_sim, best_i = sim, i
        chosen.append(best_i)
    centers = [V[c].copy() for c in chosen]
    assign = None
    for _ in range(iters):
        sims = np.stack([V @ c for c in centers], axis=1)
        new_assign = [int(a) for a in np.argmax(sims, axis=1)]
        if new_assign == assign:
            break
        assign = new_assign
        for j in range(len(centers)):
            idx = [i for i, a in enumerate(assign) if a == j]
            if idx:
                centers[j] = _l2(V[idx].mean(axis=0))
    return assign
```

`ContentPreferenceModel` changes — `__init__` gains `dirs=None`, `fit` gains the new params, `score` uses dirs:

```python
class ContentPreferenceModel:
    def __init__(self, version, direction=None, calib=(1.0, 0.0),
                 liked_vecs=None, liked_names=None, dirs=None):
        self.version = version
        self._dir = direction
        self._a, self._b = calib
        self._liked_vecs = liked_vecs        # np.ndarray (k, dim) or None
        self._liked_names = liked_names or []
        self._dirs = dirs                    # np.ndarray (n_clusters, dim) or None

    @property
    def direction(self):
        """The fitted GLOBAL like−dislike unit vector, or None in cold-start."""
        return self._dir

    @classmethod
    def fit(cls, liked, disliked, neutral=None, min_likes=MIN_LIKES,
            neutral_weight=None, likes_per_centroid=None, max_centroids=None,
            split_max_cos=None):
        if len(liked) < min_likes:
            return cls("coldstart")
        neutral = neutral or []
        w = (_env_float("AGF_NEUTRAL_WEIGHT", 0.25)
             if neutral_weight is None else neutral_weight)
        per_c = (_env_int("AGF_TASTE_LIKES_PER_CENTROID", 12)
                 if likes_per_centroid is None else likes_per_centroid)
        max_c = (_env_int("AGF_TASTE_CENTROIDS_MAX", 4)
                 if max_centroids is None else max_centroids)

        liked = sorted(liked, key=lambda t: t[0])   # id order: parity with Go
        L = np.array([v for _, _, v in liked], float)
        dim = L.shape[1]

        denom = len(disliked) + w * len(neutral)
        dis_c = np.zeros(dim)
        if denom > 0:
            s = np.zeros(dim)
            for _, _, v in disliked:
                s = s + np.asarray(v, float)
            for _, _, v in neutral:
                s = s + w * np.asarray(v, float)
            dis_c = s / denom

        direction = _l2(L.mean(axis=0) - dis_c)

        k = max(1, min(max_c, len(liked) // max(per_c, 1)))
        assign = _cluster(list(L), k)
        dirs = []
        for j in sorted(set(assign)):
            idx = [i for i, a in enumerate(assign) if a == j]
            dirs.append(_l2(L[idx].mean(axis=0) - dis_c))
        # Structure guard: if any two cluster directions nearly coincide, the
        # split is fitting noise (measured 2026-08-04: founder's likes are one
        # blob, best-k centroid pair at cosine 0.92) — fall back to the single
        # global direction until real structure emerges.
        split_max = (_env_float("AGF_TASTE_SPLIT_MAX_COS", 0.85)
                     if split_max_cos is None else split_max_cos)
        if len(dirs) > 1:
            for i in range(len(dirs)):
                for j in range(i + 1, len(dirs)):
                    if float(np.dot(dirs[i], dirs[j])) > split_max:
                        dirs = [direction]
                        break
                if len(dirs) == 1:
                    break
        D = np.stack(dirs)

        labeled = [(v, 1) for _, _, v in liked] + [(v, 0) for _, _, v in disliked]
        raws = [float(np.max(D @ np.asarray(v, float))) for v, _ in labeled]
        labels = [y for _, y in labeled]
        calib = _fit_platt(raws, labels)
        names = [n for _, n, _ in liked]
        return cls("content-sim-v2", direction, calib, L, names, dirs=D)

    def score(self, vec) -> float:
        if self.version == "coldstart":
            return 0.0
        v = np.asarray(vec, float)
        if self._dirs is not None:
            return float(np.max(self._dirs @ v))
        return float(np.dot(v, self._dir))
```

(`p_like` and `why` are unchanged.)

- [ ] **Step 4: Run the new tests + existing model tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_content_preference_multicentroid.py tests/scoring/test_content_preference_direction.py -v`
Expected: all PASS. If `test_content_preference_direction.py` asserts `version == "content-sim-v1"` anywhere, update that assertion to `content-sim-v2` — the direction semantics it tests are preserved.

- [ ] **Step 5: Wire neutrals into the offline scorer**

In `scripts/score_content_preferences.py`, `score_user` (after the `disliked` list, before `model = ...`):

```python
    neutral = [(a, meta.get(a, ("", 0))[0] or "", emb[a])
               for a, r in labels.items() if r == "neutral" and a in emb]
    model = ContentPreferenceModel.fit(liked, disliked, neutral=neutral)
```

And extend the log line:

```python
    print(f"[cpref] {user_email}: model={model.version} scored={len(scored)} "
          f"likes={len(liked)} neutrals={len(neutral)}", flush=True)
```

- [ ] **Step 6: Run the full Python suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -x -q` (bare, own call, read exit)
Expected: 0 failed.

- [ ] **Step 7: Commit**

```bash
git add src/agf/scoring/content_preference.py scripts/score_content_preferences.py tests/scoring/test_content_preference_multicentroid.py tests/scoring/test_content_preference_direction.py && git commit -m "feat(reco): multi-centroid taste model v2 + neutrals as weak negatives (Python canon)" -- src/agf/scoring/content_preference.py scripts/score_content_preferences.py tests/scoring/
```

---

### Task 2: Go port of model v2 + regenerated golden parity

**Files:**
- Modify: `backend/internal/reco/content_model.go`
- Test: `backend/internal/reco/content_model_test.go`

**Interfaces:**
- Consumes: Task 1's Python semantics (goldens generated from it).
- Produces: `type LabeledVec struct { ID int64; Name string; Vec []float64 }`; `type FitOpts struct { NeutralWeight float64; LikesPerCentroid int; MaxCentroids int }`; `func DefaultFitOpts() FitOpts` (env-read with defaults 0.25/12/4); `func FitContentModel(liked, disliked, neutral []LabeledVec, opts FitOpts) *ContentModel`; `clusterAssign(vecs [][]float64, k, iters int) []int`. Task 4 calls `FitContentModel(liked, disliked, neutral, reco.DefaultFitOpts())`.

- [ ] **Step 1: Generate golden values from the Python canon**

Write `/private/tmp/claude-501/-Users-edaslakavicius-Projects-emerging-app-finder/0ede88bb-1ad6-4057-aa21-4a8462ccfa56/scratchpad/gen_goldens_v2.py`:

```python
"""Golden generator for the v2 Go parity test. Mirrors goldenFixtureV2 in
backend/internal/reco/content_model_test.go: 12 likes near e1 (ids 100+),
12 likes near e2 (ids 200+), 2 dislikes near e4 (ids 900+), 3 neutrals
near e3 (ids 950+), dim=4. Uses explicit params matching Go DefaultFitOpts."""
import numpy as np
from agf.scoring.content_preference import ContentPreferenceModel


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def mk(n, base, name, start_id, wiggle_axis=2):
    out = []
    for i in range(n):
        v = np.asarray(base, float).copy()
        v[wiggle_axis] += 0.05 * i
        out.append((start_id + i, f"{name}{i}", unit(v)))
    return out


liked = mk(12, [1.0, 0.0, 0.05, 0.0], "A", 100) + mk(12, [0.0, 1.0, 0.05, 0.0], "B", 200)
disliked = mk(2, [0.0, 0.0, 0.0, 1.0], "D", 900)
neutral = mk(3, [0.0, 0.0, 1.0, 0.0], "N", 950, wiggle_axis=1)
m = ContentPreferenceModel.fit(liked, disliked, neutral=neutral,
                               neutral_weight=0.25, likes_per_centroid=12,
                               max_centroids=4, split_max_cos=0.85)
assert m.version == "content-sim-v2", m.version
probes = [unit([1, 0, 0, 0]), unit([0, 1, 0, 0]), unit([1, 1, 0, 0]),
          unit([0, 0, 1, 0]), unit([0, 0, 0, 1]), unit([0.7, 0.1, 0.1, 0.6])]
for p in probes:
    print(f"{{unitVec([]float64{{{', '.join(str(x) for x in np.round(p*np.linalg.norm(p), 10))}}}), "
          f"{m.p_like(m.score(p)):.10f}, {m.score(p):.10f}, \"{m.why(p)}\"}},")
```

Run: `cd /Users/edaslakavicius/Projects/emerging-app-finder && PYTHONPATH=src .venv/bin/python <scratchpad>/gen_goldens_v2.py`
Copy the printed rows — they are the Go test's case table. (Print the probe basis vectors as their pre-normalization ints if rounding makes them noisy; the probes are constructed identically in Go via `unitVec`.)

- [ ] **Step 2: Write the failing Go test**

In `backend/internal/reco/content_model_test.go`, KEEP the old fixture helpers but replace `TestContentModelGoldenParity` and add cluster tests. The old golden table dies with v1 (single-direction values no longer apply):

```go
// goldenFixtureV2 mirrors scratchpad gen_goldens_v2.py: 12 likes near e1,
// 12 likes near e2, 2 dislikes near e4, 3 neutrals near e3, dim=4.
func goldenFixtureV2() (liked, disliked, neutral []LabeledVec) {
	for i := 0; i < 12; i++ {
		liked = append(liked, LabeledVec{ID: int64(100 + i), Name: fmt.Sprintf("A%d", i),
			Vec: unitVec([]float64{1.0, 0.0, 0.05 + 0.05*float64(i), 0.0})})
	}
	for i := 0; i < 12; i++ {
		liked = append(liked, LabeledVec{ID: int64(200 + i), Name: fmt.Sprintf("B%d", i),
			Vec: unitVec([]float64{0.0, 1.0, 0.05 + 0.05*float64(i), 0.0})})
	}
	for i := 0; i < 2; i++ {
		disliked = append(disliked, LabeledVec{ID: int64(900 + i), Name: fmt.Sprintf("D%d", i),
			Vec: unitVec([]float64{0.0, 0.0, 0.05 * float64(i), 1.0})})
	}
	for i := 0; i < 3; i++ {
		neutral = append(neutral, LabeledVec{ID: int64(950 + i), Name: fmt.Sprintf("N%d", i),
			Vec: unitVec([]float64{0.0, 0.05 * float64(i), 1.0, 0.0})})
	}
	return liked, disliked, neutral
}
```

CRITICAL: the wiggle formula in the Go fixture and the Python generator must be
byte-identical. The Python `mk` above uses `v[wiggle_axis] += 0.05 * i` on a base
whose axis already holds 0.05 (likes) or 0.0 (dislikes/neutrals) — mirror exactly
(likes: `0.05 + 0.05*i` on axis 2; dislikes: `0.05*i` on axis 2; neutrals:
`0.05*i` on axis 1). If in doubt, print both fixtures and diff.

```go
var fitOptsGolden = FitOpts{NeutralWeight: 0.25, LikesPerCentroid: 12, MaxCentroids: 4, SplitMaxCos: 0.85}

// Golden values from src/agf/scoring/content_preference.py (v2) on
// goldenFixtureV2 via scratchpad gen_goldens_v2.py, 2026-08-04.
func TestContentModelV2GoldenParity(t *testing.T) {
	liked, disliked, neutral := goldenFixtureV2()
	m := FitContentModel(liked, disliked, neutral, fitOptsGolden)
	if m.Coldstart() {
		t.Fatal("24 likes must not be coldstart")
	}
	cases := []struct {
		vec        []float64
		pLike, raw float64
		why        string
	}{
		// PASTE the generator's printed rows here.
	}
	for i, c := range cases {
		raw := m.Score(c.vec)
		if math.Abs(raw-c.raw) > 1e-6 {
			t.Errorf("case %d: raw = %.10f, want %.10f", i, raw, c.raw)
		}
		if p := m.PLike(raw); math.Abs(p-c.pLike) > 1e-6 {
			t.Errorf("case %d: p_like = %.10f, want %.10f", i, p, c.pLike)
		}
		if got := m.Why(c.vec); got != c.why {
			t.Errorf("case %d: why = %q, want %q", i, got, c.why)
		}
	}
}

func TestClusterAssignDeterministicSeparation(t *testing.T) {
	liked, _, _ := goldenFixtureV2()
	vecs := make([][]float64, len(liked))
	for i, lv := range liked {
		vecs[i] = lv.Vec
	}
	a := clusterAssign(vecs, 2, 50)
	for i := 1; i < 12; i++ {
		if a[i] != a[0] {
			t.Fatalf("cluster A split: %v", a)
		}
	}
	for i := 13; i < 24; i++ {
		if a[i] != a[12] {
			t.Fatalf("cluster B split: %v", a)
		}
	}
	if a[0] == a[12] {
		t.Fatalf("clusters merged: %v", a)
	}
}

func TestStructureGuardCollapsesBlob(t *testing.T) {
	// One blob forced into k=4 must collapse to the single global direction:
	// scores identical to a MaxCentroids=1 fit.
	var likes, dis []LabeledVec
	for i := 0; i < 12; i++ {
		likes = append(likes, LabeledVec{ID: int64(100 + i), Name: fmt.Sprintf("A%d", i),
			Vec: unitVec([]float64{1.0, 0.0, 0.05 + 0.05*float64(i), 0.0})})
	}
	for i := 0; i < 2; i++ {
		dis = append(dis, LabeledVec{ID: int64(900 + i), Name: fmt.Sprintf("D%d", i),
			Vec: unitVec([]float64{0.0, 0.0, 0.05 * float64(i), 1.0})})
	}
	multi := FitContentModel(likes, dis, nil, FitOpts{NeutralWeight: 0.25, LikesPerCentroid: 1, MaxCentroids: 4, SplitMaxCos: 0.85})
	single := FitContentModel(likes, dis, nil, FitOpts{NeutralWeight: 0.25, LikesPerCentroid: 12, MaxCentroids: 1, SplitMaxCos: 0.85})
	for _, probe := range [][]float64{{1, 0, 0, 0}, {0, 1, 0, 0}, {0.5, 0.5, 0.5, 0}} {
		v := unitVec(probe)
		if math.Abs(multi.Score(v)-single.Score(v)) > 1e-12 {
			t.Errorf("guard failed to collapse: %v vs %v", multi.Score(v), single.Score(v))
		}
	}
}

func TestFitOrderInvariantAfterIDSort(t *testing.T) {
	liked, disliked, neutral := goldenFixtureV2()
	rev := make([]LabeledVec, len(liked))
	for i, lv := range liked {
		rev[len(liked)-1-i] = lv
	}
	m1 := FitContentModel(liked, disliked, neutral, fitOptsGolden)
	m2 := FitContentModel(rev, disliked, neutral, fitOptsGolden)
	probe := unitVec([]float64{0.5, 0.5, 0, 0})
	if math.Abs(m1.Score(probe)-m2.Score(probe)) > 1e-12 {
		t.Errorf("order-dependent fit: %v vs %v", m1.Score(probe), m2.Score(probe))
	}
}
```

The existing `TestContentModelColdstart` keeps passing with the updated call
signature (`FitContentModel(liked, nil, nil, DefaultFitOpts())`) — update its
call sites, not its assertions. The old v1 golden test and its `goldenFixture`
are deleted (superseded by v2 parity).

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && go test ./internal/reco/ -run 'V2|Cluster|OrderInvariant' -v`
Expected: compile FAIL (`LabeledVec.ID`, `FitOpts` undefined).

- [ ] **Step 4: Implement the Go port**

In `backend/internal/reco/content_model.go`:

```go
// LabeledVec is one rated app's content embedding plus identity. ID orders the
// like set before clustering so Go and Python fit identical models regardless
// of scan order.
type LabeledVec struct {
	ID   int64
	Name string
	Vec  []float64
}

// FitOpts are the v2 taste knobs. DefaultFitOpts reads the env once per call —
// cheap, and keeps this package free of load-order coupling.
type FitOpts struct {
	NeutralWeight    float64 // weight of a neutral vec in the negative centroid
	LikesPerCentroid int     // K = clamp(nLikes/this, 1, MaxCentroids)
	MaxCentroids     int
	SplitMaxCos      float64 // structure guard: dirs closer than this collapse to one
}

func DefaultFitOpts() FitOpts {
	return FitOpts{
		NeutralWeight:    envFloat("AGF_NEUTRAL_WEIGHT", 0.25),
		LikesPerCentroid: envInt("AGF_TASTE_LIKES_PER_CENTROID", 12),
		MaxCentroids:     envInt("AGF_TASTE_CENTROIDS_MAX", 4),
		SplitMaxCos:      envFloat("AGF_TASTE_SPLIT_MAX_COS", 0.85),
	}
}

func envFloat(key string, def float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}
```

(`os` joins the import list. NOTE: `backend/repositories` has its own private
`envFloat` — same name, different package, no conflict.)

`clusterAssign` (port of Python `_cluster` — keep the comment pointing at parity):

```go
// clusterAssign is the Go port of content_preference._cluster: deterministic
// k-means over unit vectors. Farthest-point init from index 0 (next center =
// point with smallest max-dot to chosen centers, first-min wins); Lloyd's
// assignment by highest dot (first-max wins); centers are l2-normalized member
// means; empty clusters keep their old center. No RNG — golden-pinned to Python.
func clusterAssign(vecs [][]float64, k, iters int) []int {
	n := len(vecs)
	if k > n {
		k = n
	}
	if k <= 1 || n == 0 {
		return make([]int, n)
	}
	chosen := []int{0}
	inChosen := map[int]bool{0: true}
	for len(chosen) < k {
		bestI, bestSim := -1, math.Inf(1)
		for i := 0; i < n; i++ {
			if inChosen[i] {
				continue
			}
			sim := math.Inf(-1)
			for _, c := range chosen {
				if s := dot(vecs[i], vecs[c]); s > sim {
					sim = s
				}
			}
			if sim < bestSim {
				bestSim, bestI = sim, i
			}
		}
		chosen = append(chosen, bestI)
		inChosen[bestI] = true
	}
	dim := len(vecs[0])
	centers := make([][]float64, len(chosen))
	for j, c := range chosen {
		centers[j] = append([]float64(nil), vecs[c]...)
	}
	assign := make([]int, n)
	haveAssign := false
	for it := 0; it < iters; it++ {
		cur := make([]int, n)
		for i, v := range vecs {
			best, bestJ := math.Inf(-1), 0
			for j, c := range centers {
				if s := dot(v, c); s > best {
					best, bestJ = s, j
				}
			}
			cur[i] = bestJ
		}
		if haveAssign && slices.Equal(cur, assign) {
			break
		}
		assign, haveAssign = cur, true
		for j := range centers {
			mean := make([]float64, dim)
			cnt := 0
			for i, a := range assign {
				if a == j {
					for d, x := range vecs[i] {
						mean[d] += x
					}
					cnt++
				}
			}
			if cnt > 0 {
				for d := range mean {
					mean[d] /= float64(cnt)
				}
				centers[j] = l2norm(mean)
			}
		}
	}
	return assign
}
```

(`slices` joins the imports.)

`ContentModel` struct: replace `dir []float64` with `dirs [][]float64` PLUS keep
a `dir []float64` field for nothing — no: Go has no `.direction` consumer, so
drop the single dir entirely. Struct becomes:

```go
type ContentModel struct {
	version    string
	dirs       [][]float64
	a, b       float64
	likedVecs  [][]float64
	likedNames []string
}
```

`FitContentModel`:

```go
// FitContentModel fits the v2 multi-centroid model (port of ContentPreferenceModel.fit).
// Likes are sorted by ID, clustered deterministically; each cluster direction is
// l2norm(mean(cluster) − disC) where disC is the weighted mean of disliked plus
// opts.NeutralWeight × neutral vectors. Score = max over cluster directions.
// Neutrals never enter the Platt calibration. Fewer than MinLikes likes → coldstart.
func FitContentModel(liked, disliked, neutral []LabeledVec, opts FitOpts) *ContentModel {
	if len(liked) < MinLikes {
		return &ContentModel{version: "coldstart", a: 1.0, b: 0.0}
	}
	liked = append([]LabeledVec(nil), liked...)
	sort.Slice(liked, func(i, j int) bool { return liked[i].ID < liked[j].ID })

	dim := len(liked[0].Vec)
	disC := make([]float64, dim)
	denom := float64(len(disliked)) + opts.NeutralWeight*float64(len(neutral))
	if denom > 0 {
		for _, dv := range disliked {
			for i, x := range dv.Vec {
				disC[i] += x
			}
		}
		for _, nv := range neutral {
			for i, x := range nv.Vec {
				disC[i] += opts.NeutralWeight * x
			}
		}
		for i := range disC {
			disC[i] /= denom
		}
	}

	vecs := make([][]float64, len(liked))
	for i, lv := range liked {
		vecs[i] = lv.Vec
	}
	// Global single direction — the structure guard's fallback (mirrors the
	// Python model's `direction` property).
	globalMean := make([]float64, dim)
	for _, v := range vecs {
		for d, x := range v {
			globalMean[d] += x
		}
	}
	for d := range globalMean {
		globalMean[d] = globalMean[d]/float64(len(vecs)) - disC[d]
	}
	globalDir := l2norm(globalMean)

	perC := opts.LikesPerCentroid
	if perC < 1 {
		perC = 1
	}
	k := len(liked) / perC
	if k < 1 {
		k = 1
	}
	if k > opts.MaxCentroids {
		k = opts.MaxCentroids
	}
	assign := clusterAssign(vecs, k, 50)

	maxCluster := 0
	for _, a := range assign {
		if a > maxCluster {
			maxCluster = a
		}
	}
	var dirs [][]float64
	for j := 0; j <= maxCluster; j++ {
		mean := make([]float64, dim)
		cnt := 0
		for i, a := range assign {
			if a == j {
				for d, x := range vecs[i] {
					mean[d] += x
				}
				cnt++
			}
		}
		if cnt == 0 {
			continue
		}
		for d := range mean {
			mean[d] = mean[d]/float64(cnt) - disC[d]
		}
		dirs = append(dirs, l2norm(mean))
	}

	// Structure guard: near-coincident split directions mean the clustering cut
	// one blob, not real taste structure — collapse to the global direction
	// (2026-08-04 measurement: founder's likes best-k centroid cosine 0.92).
	if len(dirs) > 1 {
	guard:
		for i := range dirs {
			for j := i + 1; j < len(dirs); j++ {
				if dot(dirs[i], dirs[j]) > opts.SplitMaxCos {
					dirs = [][]float64{globalDir}
					break guard
				}
			}
		}
	}

	scoreMax := func(v []float64) float64 {
		best := math.Inf(-1)
		for _, d := range dirs {
			if s := dot(v, d); s > best {
				best = s
			}
		}
		return best
	}
	raws := make([]float64, 0, len(liked)+len(disliked))
	labels := make([]float64, 0, len(liked)+len(disliked))
	for _, lv := range liked {
		raws = append(raws, scoreMax(lv.Vec))
		labels = append(labels, 1)
	}
	for _, dv := range disliked {
		raws = append(raws, scoreMax(dv.Vec))
		labels = append(labels, 0)
	}
	a, b := fitPlatt(raws, labels)

	likedVecs := make([][]float64, len(liked))
	likedNames := make([]string, len(liked))
	for i, lv := range liked {
		likedVecs[i] = lv.Vec
		likedNames[i] = lv.Name
	}
	return &ContentModel{
		version: "content-sim-v2", dirs: dirs, a: a, b: b,
		likedVecs: likedVecs, likedNames: likedNames,
	}
}
```

PARITY NOTE (cluster dir arithmetic): Python computes `_l2(L[idx].mean(axis=0) - dis_c)`
— mean first, then subtract. The Go code above does `mean[d]/cnt - disC[d]` in one
expression, which is the same arithmetic order. Keep it that way.

`Score` becomes max-over-dirs (`0` in coldstart); `Why` unchanged:

```go
// Score is the raw taste score: max over cluster directions of dot(vec, dir);
// 0 in coldstart.
func (m *ContentModel) Score(vec []float64) float64 {
	if m.Coldstart() || len(m.dirs) == 0 {
		return 0
	}
	best := math.Inf(-1)
	for _, d := range m.dirs {
		if s := dot(vec, d); s > best {
			best = s
		}
	}
	return best
}
```

Update the one production caller signature reference in this package's tests
(`FitContentModel(liked, disliked)` → `FitContentModel(liked, disliked, nil, DefaultFitOpts())`).
`backend/repositories/preference_repo.go:98` will not compile until Task 4 — to
keep the tree green, update that single line NOW as part of this task:

```go
		model := reco.FitContentModel(liked, disliked, nil, reco.DefaultFitOpts())
```

(Task 4 replaces `nil` with the real neutral slice.) Also add `ID: <advertiser id>`
where `LabeledVec` is constructed at `preference_repo.go:90`:

```go
			lv := reco.LabeledVec{ID: r.AdvertiserID, Name: r.AppName, Vec: vec}
```

- [ ] **Step 5: Run the reco tests, then the whole backend build**

Run: `cd backend && go test ./internal/reco/ -v` then `go build ./... && go test ./... 2>&1 | tail -5` for a smoke; the bare full gate comes at the end of the task:
Run (bare, own call): `cd backend && go test ./...`
Expected: 0 failed. If a golden number misses by >1e-6, re-run the Python generator and diff the fixture construction first (wiggle formula) before touching the port.

- [ ] **Step 6: Commit**

```bash
git add backend/internal/reco/content_model.go backend/internal/reco/content_model_test.go backend/repositories/preference_repo.go && git commit -m "feat(reco): Go port of multi-centroid taste v2, golden parity regenerated" -- backend/internal/reco/ backend/repositories/preference_repo.go
```

---

### Task 3: Queue — seeded niche counts + cap-free final pass

**Files:**
- Modify: `backend/repositories/preference_queue.go`
- Test: `backend/repositories/preference_queue_test.go`

**Interfaces:**
- Consumes: existing `prefCandidate`, `queueOpts`, `assembleQueue`, test helpers `cand()`, `baseOpts()`, `ids()`.
- Produces: `queueOpts.SeedNicheCounts map[string]int`; `func nicheKeyFrom(topicID *int, nicheKeyword *string, cohort string) string` (exact same fallback chain as today's `nicheKey`). Task 4 calls both.

- [ ] **Step 1: Write the failing tests**

Append to `backend/repositories/preference_queue_test.go`:

```go
func TestAssembleQueue_SeededNicheCountsSuppressTopic(t *testing.T) {
	// 6 candidates in niche "hot", 6 spread across distinct niches.
	var cands []prefCandidate
	for i := 0; i < 6; i++ {
		cands = append(cands, cand(i, "hot", 0.9, 0.2, "scored"))
	}
	for i := 6; i < 12; i++ {
		cands = append(cands, cand(i, fmt.Sprintf("cool%d", i), 0.5, 0.5, "scored"))
	}
	opts := baseOpts(6, 1)
	opts.SeedNicheCounts = map[string]int{"n:hot": defaultPerNicheCap}
	got := assembleQueue(cands, opts)
	require.Len(t, got, 6)
	for _, c := range got {
		require.NotEqual(t, "hot", *c.NicheKeyword,
			"seeded-at-cap niche must not serve while alternatives exist")
	}
}

func TestAssembleQueue_SeedPartialStillServesRemainder(t *testing.T) {
	var cands []prefCandidate
	for i := 0; i < 6; i++ {
		cands = append(cands, cand(i, "hot", 0.9, 0.2, "scored"))
	}
	for i := 6; i < 12; i++ {
		cands = append(cands, cand(i, fmt.Sprintf("cool%d", i), 0.5, 0.5, "scored"))
	}
	opts := baseOpts(12, 1)
	opts.SeedNicheCounts = map[string]int{"n:hot": defaultPerNicheCap - 1}
	got := assembleQueue(cands, opts)
	hot := 0
	for _, c := range got {
		if *c.NicheKeyword == "hot" {
			hot++
		}
	}
	require.Equal(t, 1, hot, "one slot left under the cap")
}

func TestAssembleQueue_CapFreePassPreventsUnderfill(t *testing.T) {
	// Every candidate lives in one niche and the seed already caps it: without
	// the cap-free pass the deck would be empty.
	var cands []prefCandidate
	for i := 0; i < 8; i++ {
		cands = append(cands, cand(i, "only", 0.9, 0.2, "scored"))
	}
	opts := baseOpts(5, 1)
	opts.SeedNicheCounts = map[string]int{"n:only": 99}
	got := assembleQueue(cands, opts)
	require.Len(t, got, 5, "cap-free pass must fill the deck when everything is capped")
	require.Len(t, uniqueIDs(got), 5)
}

func uniqueIDs(cs []prefCandidate) map[int]bool {
	out := map[int]bool{}
	for _, c := range cs {
		out[c.AdvertiserID] = true
	}
	return out
}

func TestNicheKeyFromMatchesNicheKey(t *testing.T) {
	topic := 7
	kw := "sleep tracker"
	c := cand(1, kw, 0.5, 0.5, "scored")
	c.TopicID = &topic
	c.Cohort = "Fresh"
	require.Equal(t, nicheKey(c), nicheKeyFrom(&topic, &kw, "Fresh"))
	require.Equal(t, "t:7", nicheKeyFrom(&topic, &kw, "Fresh"))
	require.Equal(t, "n:sleep tracker", nicheKeyFrom(nil, &kw, "Fresh"))
	require.Equal(t, "c:Fresh", nicheKeyFrom(nil, nil, "Fresh"))
	require.Equal(t, "", nicheKeyFrom(nil, nil, ""))
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && go test ./repositories/ -run 'Seeded|CapFree|NicheKeyFrom|SeedPartial' -v`
Expected: compile FAIL (`SeedNicheCounts`, `nicheKeyFrom` undefined).

- [ ] **Step 3: Implement**

In `backend/repositories/preference_queue.go`:

Refactor `nicheKey` into `nicheKeyFrom` + thin wrapper (replaces the existing
`nicheKey` body at lines 83-94):

```go
// nicheKeyFrom is the diversity-cap key for raw fields: embedding topic, else
// niche_keyword, else cohort, else exempt. Shared by candidates (nicheKey) and
// the recent-ratings seed (preference_repo).
func nicheKeyFrom(topicID *int, nicheKeyword *string, cohort string) string {
	if topicID != nil {
		return "t:" + strconv.Itoa(*topicID)
	}
	if nicheKeyword != nil && *nicheKeyword != "" {
		return "n:" + *nicheKeyword
	}
	if cohort != "" {
		return "c:" + cohort
	}
	return ""
}

func nicheKey(c prefCandidate) string {
	return nicheKeyFrom(c.TopicID, c.NicheKeyword, c.Cohort)
}
```

`queueOpts` gains the seed map:

```go
type queueOpts struct {
	Limit         int
	PerNicheCap   int
	ExploitFrac   float64
	UncertainFrac float64
	// SeedNicheCounts pre-fills the per-niche counter (decayed cap: the user's
	// recent ratings count against each niche's budget, so a topic that already
	// filled its cap in the window stops appearing until the window rolls off).
	SeedNicheCounts map[string]int
	Rng             *rand.Rand
}
```

In `assembleQueue`, seed the counter (replaces `nicheCount := map[string]int{}`):

```go
	nicheCount := map[string]int{}
	for k, v := range opts.SeedNicheCounts {
		if k != "" && v > 0 {
			nicheCount[k] = v
		}
	}
```

Parameterize `tryTake` with a cap toggle (replace the existing closure):

```go
	tryTake := func(b int, capped bool) bool {
		for cursors[b] < len(buckets[b]) {
			c := buckets[b][cursors[b]]
			cursors[b]++
			if seenAdv[c.AdvertiserID] {
				continue
			}
			if c.AppID != nil && seenApp[*c.AppID] {
				continue
			}
			k := nicheKey(c)
			if capped && k != "" && nicheCount[k] >= cap {
				continue
			}
			seenAdv[c.AdvertiserID] = true
			if c.AppID != nil {
				seenApp[*c.AppID] = true
			}
			if k != "" {
				nicheCount[k]++
			}
			picked = append(picked, c)
			got[b]++
			return true
		}
		return false
	}
```

Existing passes call `tryTake(b, true)`. After the underfill pass, add the
cap-free last resort (cursors reset so candidates skipped for cap reasons get
re-scanned; `seenAdv` still dedupes):

```go
	// Cap-free last resort: a heavily seeded cap (or a candidate pool collapsed
	// into few niches) must never underfill the deck. Cursors reset because the
	// capped passes already consumed them past skipped candidates.
	if len(picked) < opts.Limit {
		cursors = []int{0, 0, 0}
		for len(picked) < opts.Limit {
			progressed := false
			for b := 0; b < 3; b++ {
				if tryTake(b, false) {
					progressed = true
					if len(picked) >= opts.Limit {
						break
					}
				}
			}
			if !progressed {
				break
			}
		}
	}
```

- [ ] **Step 4: Run the queue tests, then the package**

Run: `cd backend && go test ./repositories/ -run 'AssembleQueue|NicheKey|WeightedSample|CrowdingRankScore' -v`
Expected: all PASS (old cap/underfill tests must still pass — the cap-free pass
only activates when the capped passes underfill, which the old
`TestAssembleQueue_UnderfillRedistributes` scenario... CHECK: if that test
expected fewer-than-limit results due to cap exhaustion, the cap-free pass now
fills further — read the test and update its expectation to the new invariant
(deck fills whenever enough deduped candidates exist). The new invariant is the
spec'd behavior; note the change in the commit message.)

- [ ] **Step 5: Commit**

```bash
git add backend/repositories/preference_queue.go backend/repositories/preference_queue_test.go && git commit -m "feat(review): decayed niche cap seeding + cap-free underfill guard in assembleQueue" -- backend/repositories/preference_queue.go backend/repositories/preference_queue_test.go
```

---

### Task 4: Repo wiring — rated rows carry topics/timestamps, neutrals reach the model, seed computed

**Files:**
- Modify: `backend/repositories/preference_repo.go`
- Test: `backend/repositories/preference_repo_test.go`

**Interfaces:**
- Consumes: Task 2's `FitContentModel(liked, disliked, neutral, opts)` + `LabeledVec.ID`; Task 3's `queueOpts.SeedNicheCounts` + `nicheKeyFrom`.
- Produces: the live serving behavior; pure helper `seedNicheCounts(rated []prefRatedRow, cutoff time.Time) map[string]int`.

- [ ] **Step 1: Write the failing tests**

Pure helper test appended to `backend/repositories/preference_queue_test.go`
(same package `repositories`, so it can see internals; the DB test lives in the
`_test` package file and comes in Step 4):

```go
func TestSeedNicheCounts_WindowAndKeyFallback(t *testing.T) {
	topic := 3
	kw := "habit tracker"
	now := time.Now()
	rated := []prefRatedRow{
		{AdvertiserID: 1, Rating: "like", RatedAt: now.Add(-1 * time.Hour), TopicID: &topic},
		{AdvertiserID: 2, Rating: "neutral", RatedAt: now.Add(-2 * time.Hour), TopicID: &topic},
		{AdvertiserID: 3, Rating: "dislike", RatedAt: now.Add(-30 * time.Hour), TopicID: &topic}, // outside window
		{AdvertiserID: 4, Rating: "like", RatedAt: now.Add(-1 * time.Hour), NicheKeyword: &kw},
		{AdvertiserID: 5, Rating: "like", RatedAt: now.Add(-1 * time.Hour)}, // no keys → exempt
	}
	got := seedNicheCounts(rated, now.Add(-24*time.Hour))
	require.Equal(t, map[string]int{"t:3": 2, "n:habit tracker": 1}, got)
}
```

(`time` joins that file's imports.)

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && go test ./repositories/ -run SeedNicheCounts -v`
Expected: compile FAIL (`prefRatedRow`, `seedNicheCounts` undefined).

- [ ] **Step 3: Implement the wiring**

In `backend/repositories/preference_repo.go`:

Knob (join the existing crowding var block):

```go
var (
	crowdingTail    = envFloat("AGF_CROWDING_TAIL", 0.80)
	crowdingPenalty = envFloat("AGF_CROWDING_PENALTY_GO", 0.5)
	// reviewCapHours is the decayed-cap window: the user's ratings from the
	// last N hours pre-fill the per-niche cap so recently-served topics rest.
	// 0 disables seeding (per-request cap only, the pre-2026-08 behavior).
	reviewCapHours = envFloat("AGF_REVIEW_CAP_HOURS", 24)
)
```

Rated-rows scan struct + query (replaces the anonymous struct + SQL at lines 69-83):

```go
		// The user's labels, with app names for the "similar to <name>" why,
		// plus rated_at and the niche-cap keys (topic → niche_keyword → cohort)
		// for the decayed-cap seed. Rated apps may have left the matview, hence
		// the LEFT JOINs + COALESCE; a vanished app yields no key and is exempt.
		var rated []prefRatedRow
		if err := tx.Raw(
			`SELECT r.advertiser_id, r.rating, r.rated_at,
			        COALESCE(m.app_name, m.page_name, '') AS app_name,
			        t.topic_id, m.niche_keyword, COALESCE(m.cohort, '') AS cohort
			 FROM frontend.app_ratings r
			 LEFT JOIN frontend.emerging_scaling_apps m
			   ON m.advertiser_id = r.advertiser_id
			 LEFT JOIN frontend.app_topics t
			   ON t.advertiser_id = r.advertiser_id
			 WHERE r.user_email = ?`, userEmail,
		).Scan(&rated).Error; err != nil {
			return err
		}
```

Top-level type + helper (near `prefCandidate`'s home file is fine; keep them in
`preference_repo.go`):

```go
// prefRatedRow is one of the user's ratings plus the fields the decayed niche
// cap needs (rated_at + the nicheKeyFrom fallback chain).
type prefRatedRow struct {
	AdvertiserID int64     `gorm:"column:advertiser_id"`
	Rating       string    `gorm:"column:rating"`
	RatedAt      time.Time `gorm:"column:rated_at"`
	AppName      string    `gorm:"column:app_name"`
	TopicID      *int      `gorm:"column:topic_id"`
	NicheKeyword *string   `gorm:"column:niche_keyword"`
	Cohort       string    `gorm:"column:cohort"`
}

// seedNicheCounts counts the user's ratings at or after cutoff per niche key.
// These pre-fill assembleQueue's cap counter: a niche that already served (and
// got rated) cap-many cards inside the window rests until it rolls off.
func seedNicheCounts(rated []prefRatedRow, cutoff time.Time) map[string]int {
	out := map[string]int{}
	for _, r := range rated {
		if r.RatedAt.Before(cutoff) {
			continue
		}
		if k := nicheKeyFrom(r.TopicID, r.NicheKeyword, r.Cohort); k != "" {
			out[k]++
		}
	}
	return out
}
```

Label loop gains neutral + IDs (replaces lines 84-97):

```go
		var liked, disliked, neutral []reco.LabeledVec
		for _, r := range rated {
			vec, ok := embs[r.AdvertiserID]
			if !ok {
				continue
			}
			lv := reco.LabeledVec{ID: r.AdvertiserID, Name: r.AppName, Vec: vec}
			switch r.Rating {
			case "like":
				liked = append(liked, lv)
			case "dislike":
				disliked = append(disliked, lv)
			case "neutral":
				neutral = append(neutral, lv)
			}
		}
		model := reco.FitContentModel(liked, disliked, neutral, reco.DefaultFitOpts())
```

Assemble call gains the seed (replaces the `assembleQueue` call):

```go
		var seed map[string]int
		if reviewCapHours > 0 {
			cutoff := time.Now().Add(-time.Duration(reviewCapHours * float64(time.Hour)))
			seed = seedNicheCounts(rated, cutoff)
		}
		selected := assembleQueue(cands, queueOpts{
			Limit:           limit,
			PerNicheCap:     defaultPerNicheCap,
			ExploitFrac:     exploitFrac,
			UncertainFrac:   uncertainFrac,
			SeedNicheCounts: seed,
			Rng:             rand.New(rand.NewSource(time.Now().UnixNano())),
		})
```

- [ ] **Step 4: Add the DB-backed decayed-cap test**

Append to `backend/repositories/preference_repo_test.go` (package
`repositories_test`; uses the existing `seedPrefCandidates`, `seedEmbedding`,
`must`, `ratingsDB` fixtures — read that file's `TestPreferenceQueueServeTime`
first and mirror its setup idiom):

```go
// TestPreferenceQueueDecayedTopicCap: rating 3 apps of a topic recently rests
// that topic for the whole window — a fresh request serves none of its
// remaining apps while alternatives exist.
func TestPreferenceQueueDecayedTopicCap(t *testing.T) {
	g := ratingsDB(t)
	ids := seedPrefCandidates(g, 12)
	user := "cap-test@example.com"
	must(g.Exec(`DELETE FROM frontend.app_ratings WHERE user_email = ?`, user).Error)
	must(g.Exec(`DELETE FROM frontend.app_topics WHERE advertiser_id IN ?`, ids).Error)

	// First 6 advertisers share topic 7001; give every advertiser an embedding
	// so none land in the fresh bucket.
	for i, id := range ids {
		if i < 6 {
			must(g.Exec(`INSERT INTO frontend.app_topics (advertiser_id, topic_id, topic_size)
			             VALUES (?, 7001, 6)
			             ON CONFLICT (advertiser_id) DO UPDATE SET topic_id = 7001`, id).Error)
		}
		seedEmbedding(g, id, "{1,0}")
	}

	// Rate 3 of the topic-7001 apps just now (any rating value counts).
	for _, id := range ids[:3] {
		must(g.Exec(`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating)
		             VALUES (?, ?, 'neutral')`, user, id).Error)
	}

	rows, err := repositories.PreferenceQueue(context.Background(), g, user, 12, nil)
	require.NoError(t, err)
	require.NotEmpty(t, rows)
	topic := map[int64]bool{}
	for _, id := range ids[3:6] {
		topic[id] = true
	}
	for _, r := range rows {
		require.False(t, topic[int64(r.AdvertiserID)],
			"topic 7001 rested by 3 recent ratings, but advertiser %d served", r.AdvertiserID)
	}
}

// TestPreferenceQueueOldRatingsDoNotSeed: the same 3 ratings backdated outside
// the window leave the topic servable.
func TestPreferenceQueueOldRatingsDoNotSeed(t *testing.T) {
	g := ratingsDB(t)
	ids := seedPrefCandidates(g, 12)
	user := "cap-old-test@example.com"
	must(g.Exec(`DELETE FROM frontend.app_ratings WHERE user_email = ?`, user).Error)
	must(g.Exec(`DELETE FROM frontend.app_topics WHERE advertiser_id IN ?`, ids).Error)
	for i, id := range ids {
		if i < 6 {
			must(g.Exec(`INSERT INTO frontend.app_topics (advertiser_id, topic_id, topic_size)
			             VALUES (?, 7002, 6)
			             ON CONFLICT (advertiser_id) DO UPDATE SET topic_id = 7002`, id).Error)
		}
		seedEmbedding(g, id, "{1,0}")
	}
	for _, id := range ids[:3] {
		must(g.Exec(`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, rated_at)
		             VALUES (?, ?, 'neutral', now() - interval '48 hours')`, user, id).Error)
	}
	rows, err := repositories.PreferenceQueue(context.Background(), g, user, 12, nil)
	require.NoError(t, err)
	served := map[int64]bool{}
	for _, r := range rows {
		served[int64(r.AdvertiserID)] = true
	}
	fromTopic := 0
	for _, id := range ids[3:6] {
		if served[id] {
			fromTopic++
		}
	}
	require.Greater(t, fromTopic, 0,
		"48h-old ratings must not rest the topic (window is 24h)")
}
```

ADAPTATION NOTE: `ratingsDB`/`must` names come from the existing test file —
if the actual helpers differ (e.g. a `db(t)` helper or package-level `testDB`),
follow the file's own idiom; the assertions above are the contract. If
`frontend.app_topics` has no ON CONFLICT-compatible unique constraint beyond
the PK shown in `db/schema/161_topics_suppressions.sql`, the PK IS
`advertiser_id`, so the clause is valid.

- [ ] **Step 5: Run the repo tests**

Run: `cd backend && AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./repositories/ -run 'PreferenceQueue|SeedNicheCounts' -v`
Expected: all PASS (including the existing serve-time + coldstart tests — the
coldstart test may need its expectations checked: coldstart path is untouched,
but the serve-time test rates apps and asserts ordering; if it rates ≥cap apps
sharing a niche key within the window it may now see suppression — read the
test, and if it trips the new cap, set `AGF_REVIEW_CAP_HOURS=0` via `t.Setenv`…
NO: `reviewCapHours` is read at package load. If the existing test conflicts,
backdate its seeded ratings with `rated_at = now() - interval '48 hours'`
instead — behavior-preserving for what that test asserts.)

- [ ] **Step 6: Run the full backend suite bare (own call, solo on the test DB)**

Run: `cd backend && AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it go test ./...`
Expected: 0 failed.

- [ ] **Step 7: Commit**

```bash
git add backend/repositories/preference_repo.go backend/repositories/preference_repo_test.go backend/repositories/preference_queue_test.go && git commit -m "feat(review): neutrals reach the taste model; recent ratings seed the decayed topic cap" -- backend/repositories/
```

---

## Post-build (controller-run, ship-increment step 4)

1. Whole-branch review (`scripts/review-package MERGE_BASE HEAD`).
2. Live gate: local stack with a prod-shaped ratings snapshot — serve
   `/api/preference-queue` twice with fresh ratings in between; assert the
   rated topic rests, the deck fills to 12, and p_like spreads across ≥2 like
   clusters. Full Python + Go suites bare, solo, 0 failed.
3. Deploy: API image rebuild + rollout (backend change only; the Python side
   ships with the next cron/scorer run — no migration, no ordering hazard;
   `AGF_*` knobs all have code defaults so no env changes are required).
4. Update memory + specs with measured before/after.
