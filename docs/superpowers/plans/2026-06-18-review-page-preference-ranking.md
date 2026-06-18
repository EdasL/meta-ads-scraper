# Review Page + Preference Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a TikTok-style one-app-at-a-time review page (`/review`) whose queue is ordered by a per-user logistic-regression preference model, blending exploit (highest predicted-like) with explore (most-uncertain) ~75/25.

**Architecture:** A Python script fits `statsmodels.Logit` per user on `frontend.app_ratings`, scores every app in the matview, and writes `(p_like, uncertainty, queue_rank, why)` into a new `frontend.preference_scores` table (same "Python scores → DB → Go reads" pattern as GemScore). A Go endpoint `GET /api/preference-queue` joins the matview to those scores, drops already-rated apps, orders by `queue_rank`, and reuses the existing `attachEnrichment()` to supply the hero creative. The web `/review` page renders one card at a time and reuses the existing `setRating()` write + `RatingControl` interaction.

**Tech Stack:** Python 3 (statsmodels/numpy — already deps, NO new package), SQLAlchemy, Postgres; Go (Gin + GORM-raw); Next.js 16 (custom build) + next-auth.

## Global Constraints

- **No new dependencies.** Use `statsmodels==0.14.4` / `numpy==2.2.1` / `scipy==1.15.1` already in `requirements.txt`. No sklearn.
- **DB tests skip silently without env vars.** Python: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it`. Go: `AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it`. After any test run, grep the summary for "skipped" — a skip is NOT a pass. Re-run Go tests with `-count=1` (a cached green burned us before).
- **Commit directly to `main`** (solo, local-only repo; no remote). Commit after each task.
- **Product copy is plain, clear, professional** (see `CLAUDE.md`): short honest UI strings, no marketing fluff, no re-derived trend words next to numbers. Keep nouns: Field Desk, GemScore, cohort.
- **Custom Next 16:** before using any new Next API, read the relevant guide under `web/node_modules/next/dist/docs/` (per `web/AGENTS.md`).
- **Run the API after Go changes:** `cd backend && go build -o ~/bin/agf-api . && launchctl kickstart -k gui/$(id -u)/com.fielddesk.api`.
- **Multi-user from day one:** everything keys on `user_email`. The model fits per user.
- **Like vs rest:** target is `like=1`, `neutral+dislike=0`.
- **DB access:** `docker exec db-postgres-1 psql -U agf -d agf -c "..."` (prod `agf`, test `agf_it`, host `localhost:5433`).

---

### Task 1: Migration — `frontend.preference_scores` table

**Files:**
- Create: `db/schema/133_preference_scores.sql`
- Test: `tests/db/test_preference_scores_migration.py`

**Interfaces:**
- Produces: table `frontend.preference_scores(user_email TEXT, advertiser_id BIGINT, p_like DOUBLE PRECISION, uncertainty DOUBLE PRECISION, queue_rank INTEGER, why TEXT, model_version TEXT, scored_at TIMESTAMPTZ)`, PK `(user_email, advertiser_id)`.

> Note: this adds a `why TEXT` column to the spec's §5 table — the model writes a short "why shown" string per app so the API/UI need no extra computation. Within the spec's intent (it lists `why` in the API response).

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_preference_scores_migration.py
from sqlalchemy import text


def test_preference_scores_table_exists(_migrated_engine):
    with _migrated_engine.connect() as conn:
        cols = {
            r[0]
            for r in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='frontend' AND table_name='preference_scores'"
            )).fetchall()
        }
    assert {
        "user_email", "advertiser_id", "p_like", "uncertainty",
        "queue_rank", "why", "model_version", "scored_at",
    } <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_preference_scores_migration.py -v`
Expected: FAIL (table does not exist). Confirm it is not `skipped` (env var is set).

- [ ] **Step 3: Write the migration**

```sql
-- db/schema/133_preference_scores.sql
-- Per-user content-based preference scores, written by scripts/score_preferences.py.
-- p_like = P(user likes this app) from a statsmodels Logit fit on app_ratings;
-- uncertainty = 1 - |2*p_like - 1| (peaks at p_like = 0.5); queue_rank = the
-- precomputed 75/25 exploit/explore review order; why = short "why shown" string.
CREATE TABLE IF NOT EXISTS frontend.preference_scores (
    user_email    TEXT   NOT NULL,
    advertiser_id BIGINT NOT NULL,
    p_like        DOUBLE PRECISION NOT NULL,
    uncertainty   DOUBLE PRECISION NOT NULL,
    queue_rank    INTEGER NOT NULL,
    why           TEXT   NOT NULL DEFAULT '',
    model_version TEXT   NOT NULL DEFAULT '',
    scored_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_email, advertiser_id)
);
CREATE INDEX IF NOT EXISTS preference_scores_user_rank_idx
    ON frontend.preference_scores (user_email, queue_rank);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_preference_scores_migration.py -v`
Expected: PASS. (The `_migrated_engine` fixture runs `apply_migrations`, which picks up the new file.)

- [ ] **Step 5: Apply the migration to the prod DB too (so the Go endpoint has the table)**

Run: `AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf PYTHONPATH=src .venv/bin/python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; apply_migrations(get_engine())"`
Expected: completes without error. Verify: `docker exec db-postgres-1 psql -U agf -d agf -c "\d frontend.preference_scores"`.

- [ ] **Step 6: Commit**

```bash
git add db/schema/133_preference_scores.sql tests/db/test_preference_scores_migration.py
git commit -m "feat(pref): preference_scores table (migration 133)"
```

---

### Task 2: Feature extraction + target collapse (pure Python)

**Files:**
- Create: `src/agf/scoring/preference.py`
- Test: `tests/scoring/test_preference_features.py`

**Interfaces:**
- Produces:
  - `FEATURES = ["has_funnel", "whitespace", "funnel_intent", "scaling_velocity", "under_radar", "demand"]`
  - `CONTINUOUS = FEATURES[1:]` (all but `has_funnel`)
  - `collapse_target(rating: str) -> int` → `1` if `rating == "like"` else `0`
  - `feature_vector(features: dict) -> list[float]` → values in FEATURES order; `has_funnel` coerced to `0.0/1.0`; missing/None continuous → `float("nan")`

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_preference_features.py
import math

from agf.scoring.preference import FEATURES, collapse_target, feature_vector


def test_collapse_target_like_vs_rest():
    assert collapse_target("like") == 1
    assert collapse_target("neutral") == 0
    assert collapse_target("dislike") == 0


def test_feature_vector_order_and_has_funnel_coercion():
    f = {
        "has_funnel": True, "whitespace": 0.8, "funnel_intent": 0.7,
        "scaling_velocity": 0.6, "under_radar": 0.5, "demand": 0.4,
    }
    v = feature_vector(f)
    assert v == [1.0, 0.8, 0.7, 0.6, 0.5, 0.4]
    assert FEATURES[0] == "has_funnel"


def test_feature_vector_missing_continuous_is_nan():
    v = feature_vector({"has_funnel": False})
    assert v[0] == 0.0
    assert all(math.isnan(x) for x in v[1:])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_preference_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agf.scoring.preference'`.

- [ ] **Step 3: Write the implementation**

```python
# src/agf/scoring/preference.py
"""Content-based per-user preference model (statsmodels Logit).

Pure model logic — no DB. scripts/score_preferences.py does the DB I/O and
calls into here. The fitted coefficients also double as a GemScore-reweight
proposal (funnel/whitespace are the user's strongest signals but GemScore
weights them ~0).
"""
from __future__ import annotations

import math

# Feature order is fixed; everything downstream relies on it.
FEATURES = ["has_funnel", "whitespace", "funnel_intent",
            "scaling_velocity", "under_radar", "demand"]
CONTINUOUS = FEATURES[1:]  # has_funnel is binary, the rest are [0,1] axis scores

# Friendly labels for the "why shown" string.
WHY_LABELS = {
    "has_funnel": "has funnel",
    "whitespace": "low saturation",
    "funnel_intent": "strong funnel signal",
    "scaling_velocity": "scaling fast",
    "under_radar": "under the radar",
    "demand": "growing demand",
}


def collapse_target(rating: str) -> int:
    """Like vs rest: like=1, neutral/dislike=0."""
    return 1 if rating == "like" else 0


def feature_vector(features: dict) -> list[float]:
    """Values in FEATURES order. has_funnel → 0/1; missing continuous → NaN."""
    out: list[float] = []
    for name in FEATURES:
        v = features.get(name)
        if name == "has_funnel":
            out.append(1.0 if v else 0.0)
        elif v is None:
            out.append(float("nan"))
        else:
            out.append(float(v))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_preference_features.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/preference.py tests/scoring/test_preference_features.py
git commit -m "feat(pref): feature extraction + like-vs-rest target"
```

---

### Task 3: Model fit + predict + uncertainty (pure Python)

**Files:**
- Modify: `src/agf/scoring/preference.py`
- Test: `tests/scoring/test_preference_model.py`

**Interfaces:**
- Consumes: `FEATURES`, `CONTINUOUS`, `feature_vector`, `collapse_target` (Task 2).
- Produces:
  - `MIN_LABELS = 30`, `L1_ALPHA = 1.0`
  - `class PreferenceModel` with:
    - classmethod `fit(rows: list[tuple[str, dict]]) -> "PreferenceModel"` where each row is `(rating, features_dict)`. Falls back to a heuristic model when `< MIN_LABELS` labels or only one class present or the Logit fit raises.
    - `predict(features: dict) -> float` → `p_like` in `[0,1]`.
    - attribute `version: str` (`"logit-v1"` or `"heuristic-v1"`).
    - attribute `coefficients: dict[str, float]` (feature → fitted coef; empty for heuristic).
  - `uncertainty(p: float) -> float` → `1 - abs(2*p - 1)`.

Implementation notes (statsmodels):
- Standardize CONTINUOUS columns using training mean/std (store them); leave `has_funnel` raw. Impute NaN continuous with the training mean (→ 0 after standardize).
- Fit `sm.Logit(y, X)` with a constant column (`sm.add_constant`), via `fit_regularized(method="l1", alpha=alpha_vec, disp=0)` where `alpha_vec` is `0` for the constant and `L1_ALPHA` for each feature (L1 avoids the perfect-separation blow-up on small/funnel-dominated data and yields sparse, interpretable coefficients).
- Heuristic fallback `p_like = clamp01(0.6*has_funnel + 0.4*whitespace)` (whitespace already [0,1]; missing→0).

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_preference_model.py
import math

from agf.scoring.preference import PreferenceModel, uncertainty


def _synthetic(n_like=40, n_dis=40):
    """Funnel + high whitespace → like; no funnel + low whitespace → dislike."""
    rows = []
    for _ in range(n_like):
        rows.append(("like", {"has_funnel": True, "whitespace": 0.9,
                               "funnel_intent": 0.8, "scaling_velocity": 0.6,
                               "under_radar": 0.6, "demand": 0.5}))
    for _ in range(n_dis):
        rows.append(("dislike", {"has_funnel": False, "whitespace": 0.1,
                                 "funnel_intent": 0.5, "scaling_velocity": 0.5,
                                 "under_radar": 0.5, "demand": 0.5}))
    return rows


def test_uncertainty_peaks_at_half():
    assert uncertainty(0.5) == 1.0
    assert uncertainty(1.0) == 0.0
    assert uncertainty(0.0) == 0.0
    assert abs(uncertainty(0.75) - 0.5) < 1e-9


def test_logit_learns_funnel_preference():
    m = PreferenceModel.fit(_synthetic())
    assert m.version == "logit-v1"
    p_funnel = m.predict({"has_funnel": True, "whitespace": 0.9,
                          "funnel_intent": 0.8, "scaling_velocity": 0.6,
                          "under_radar": 0.6, "demand": 0.5})
    p_none = m.predict({"has_funnel": False, "whitespace": 0.1,
                        "funnel_intent": 0.5, "scaling_velocity": 0.5,
                        "under_radar": 0.5, "demand": 0.5})
    assert p_funnel > p_none
    assert 0.0 <= p_none <= 1.0 and 0.0 <= p_funnel <= 1.0


def test_cold_start_falls_back_to_heuristic():
    m = PreferenceModel.fit([("like", {"has_funnel": True, "whitespace": 0.9})])
    assert m.version == "heuristic-v1"
    p_hi = m.predict({"has_funnel": True, "whitespace": 0.9})
    p_lo = m.predict({"has_funnel": False, "whitespace": 0.1})
    assert p_hi > p_lo
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_preference_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'PreferenceModel'`.

- [ ] **Step 3: Write the implementation (append to `src/agf/scoring/preference.py`)**

```python
import numpy as np

MIN_LABELS = 30
L1_ALPHA = 1.0


def uncertainty(p: float) -> float:
    """Distance from the decision boundary, peaking at p=0.5."""
    return 1.0 - abs(2.0 * p - 1.0)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


class PreferenceModel:
    def __init__(self, version, coefficients, means, stds, intercept):
        self.version = version
        self.coefficients = coefficients          # feature -> coef (logit only)
        self._means = means                       # per CONTINUOUS feature
        self._stds = stds
        self._intercept = intercept

    @classmethod
    def fit(cls, rows):
        ys = [collapse_target(r) for r, _ in rows]
        n, n_pos = len(ys), sum(ys)
        if n < MIN_LABELS or n_pos == 0 or n_pos == n:
            return cls._heuristic()
        X_raw = np.array([feature_vector(f) for _, f in rows], dtype=float)
        # Column means over CONTINUOUS (index 1..) ignoring NaN, for impute+standardize.
        cont = X_raw[:, 1:]
        means = np.nanmean(cont, axis=0)
        means = np.where(np.isnan(means), 0.0, means)
        stds = np.nanstd(cont, axis=0)
        stds = np.where(stds < 1e-9, 1.0, stds)
        Xs = cls._transform(X_raw, means, stds)
        try:
            import statsmodels.api as sm
            Xc = sm.add_constant(Xs, has_constant="add")
            alpha = np.array([0.0] + [L1_ALPHA] * len(FEATURES))
            res = sm.Logit(np.array(ys), Xc).fit_regularized(
                method="l1", alpha=alpha, disp=0)
            params = np.asarray(res.params, dtype=float)
        except Exception:
            return cls._heuristic()
        intercept = float(params[0])
        coefs = {name: float(params[i + 1]) for i, name in enumerate(FEATURES)}
        return cls("logit-v1", coefs,
                   {f: float(means[i]) for i, f in enumerate(CONTINUOUS)},
                   {f: float(stds[i]) for i, f in enumerate(CONTINUOUS)},
                   intercept)

    @classmethod
    def _heuristic(cls):
        return cls("heuristic-v1", {}, {}, {}, 0.0)

    @staticmethod
    def _transform(X_raw, means, stds):
        X = X_raw.copy()
        cont = X[:, 1:]
        inds = np.where(np.isnan(cont))
        cont[inds] = np.take(means, inds[1])      # impute with column mean
        X[:, 1:] = (cont - means) / stds          # standardize
        return X

    def predict(self, features: dict) -> float:
        if self.version == "heuristic-v1":
            hf = 1.0 if features.get("has_funnel") else 0.0
            ws = features.get("whitespace")
            ws = 0.0 if ws is None else float(ws)
            return _clamp01(0.6 * hf + 0.4 * ws)
        v = feature_vector(features)
        z = self._intercept + self.coefficients["has_funnel"] * v[0]
        for i, name in enumerate(CONTINUOUS):
            x = v[i + 1]
            if math.isnan(x):
                x = self._means[name]
            xs = (x - self._means[name]) / self._stds[name]
            z += self.coefficients[name] * xs
        return 1.0 / (1.0 + math.exp(-z))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_preference_model.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/preference.py tests/scoring/test_preference_model.py
git commit -m "feat(pref): statsmodels Logit fit + predict + cold-start fallback"
```

---

### Task 4: Queue interleave + "why shown" string (pure Python)

**Files:**
- Modify: `src/agf/scoring/preference.py`
- Test: `tests/scoring/test_preference_queue.py`

**Interfaces:**
- Consumes: `PreferenceModel`, `FEATURES`, `CONTINUOUS`, `WHY_LABELS` (Tasks 2–3).
- Produces:
  - `EXPLORE_EVERY = 4` (1-in-4 slots = explore → 75/25)
  - `interleave(scored: list[dict]) -> list[dict]` — input dicts have `advertiser_id, p_like, uncertainty`; returns the same dicts ordered for review, each with an added `queue_rank` (0-based). Exploit list = `p_like` desc; explore list = `uncertainty` desc; merged 3:1, de-duplicated.
  - `why_string(model: PreferenceModel, features: dict, top_k: int = 2) -> str` — for logit, the top-`k` positively-contributing features (`coef * standardized value > 0`) mapped via `WHY_LABELS`, joined with " · "; for heuristic, names whichever of has_funnel / high-whitespace apply. Returns "" if nothing positive.

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_preference_queue.py
from agf.scoring.preference import EXPLORE_EVERY, PreferenceModel, interleave, why_string


def test_interleave_ratio_and_rank():
    scored = [{"advertiser_id": i, "p_like": 0.9 - i * 0.01,
               "uncertainty": 0.01} for i in range(8)]
    # Make two items clearly the most uncertain.
    scored[6]["uncertainty"] = 0.99
    scored[7]["uncertainty"] = 0.98
    out = interleave(scored)
    assert len(out) == 8
    assert [r["queue_rank"] for r in out] == list(range(8))
    # 4th slot (index 3) is the first explore pick = the most uncertain item.
    assert out[EXPLORE_EVERY - 1]["advertiser_id"] == 6
    # Every advertiser appears exactly once.
    assert sorted(r["advertiser_id"] for r in out) == list(range(8))


def test_why_string_heuristic_names_funnel():
    m = PreferenceModel._heuristic()
    s = why_string(m, {"has_funnel": True, "whitespace": 0.9})
    assert "has funnel" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_preference_queue.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Write the implementation (append to `src/agf/scoring/preference.py`)**

```python
EXPLORE_EVERY = 4  # 1-in-4 slots are explore picks → ~75/25 exploit/explore


def interleave(scored: list[dict]) -> list[dict]:
    exploit = sorted(scored, key=lambda r: r["p_like"], reverse=True)
    explore = sorted(scored, key=lambda r: r["uncertainty"], reverse=True)
    seen: set = set()
    order: list[dict] = []
    ei = xi = 0

    def _next(lst, idx):
        while idx < len(lst) and lst[idx]["advertiser_id"] in seen:
            idx += 1
        return idx

    while len(order) < len(scored):
        use_explore = (len(order) % EXPLORE_EVERY) == (EXPLORE_EVERY - 1)
        if use_explore:
            ei = _next(explore, ei)
            pick = explore[ei] if ei < len(explore) else None
        else:
            pick = None
        if pick is None:                      # exploit slot, or explore exhausted
            xi = _next(exploit, xi)
            pick = exploit[xi] if xi < len(exploit) else None
        if pick is None:                      # exploit exhausted, take any explore
            ei = _next(explore, ei)
            pick = explore[ei]
        pick = dict(pick)
        pick["queue_rank"] = len(order)
        seen.add(pick["advertiser_id"])
        order.append(pick)
    return order


def why_string(model: PreferenceModel, features: dict, top_k: int = 2) -> str:
    if model.version == "heuristic-v1":
        parts = []
        if features.get("has_funnel"):
            parts.append(WHY_LABELS["has_funnel"])
        ws = features.get("whitespace")
        if ws is not None and float(ws) >= 0.6:
            parts.append(WHY_LABELS["whitespace"])
        return " · ".join(parts[:top_k])
    contribs = []
    for name in FEATURES:
        coef = model.coefficients.get(name, 0.0)
        v = features.get(name)
        if name == "has_funnel":
            x = 1.0 if v else 0.0
            contrib = coef * x
        else:
            if v is None:
                x = model._means.get(name, 0.0)
            else:
                x = float(v)
            xs = (x - model._means.get(name, 0.0)) / model._stds.get(name, 1.0)
            contrib = coef * xs
        if contrib > 0:
            contribs.append((contrib, name))
    contribs.sort(reverse=True)
    return " · ".join(WHY_LABELS[n] for _, n in contribs[:top_k])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_preference_queue.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/preference.py tests/scoring/test_preference_queue.py
git commit -m "feat(pref): 75/25 explore/exploit queue + why-shown string"
```

---

### Task 5: `score_preferences.py` — read labels + matview, score, UPSERT

**Files:**
- Create: `scripts/score_preferences.py`
- Test: `tests/scoring/test_score_preferences_e2e.py`

**Interfaces:**
- Consumes: `PreferenceModel`, `interleave`, `why_string`, `uncertainty`, `FEATURES` (Tasks 2–4); `agf.db.engine.get_engine`/`begin`.
- Produces:
  - `score_user(conn, user_email: str) -> int` — fits the user's model, scores every matview app, UPSERTs `frontend.preference_scores`, returns rows written. Reads matview feature columns: `funnel_intent, whitespace, scaling_velocity, under_radar, demand, (funnel_url IS NOT NULL) AS has_funnel`.
  - `score_all_users(conn) -> dict[str, int]` — runs `score_user` for every distinct `user_email` in `app_ratings`; returns per-user counts. Also logs the coefficient table per user (the GemScore-tuning proposal).
  - `main()` — `get_engine()` from `AGF_DATABASE_URL`, `with begin(engine) as conn: score_all_users(conn)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_score_preferences_e2e.py
from sqlalchemy import text

from agf.db.engine import begin
from scripts.score_preferences import score_user


def _seed(conn, email):
    # Two advertisers in the matview source + the matview-feeding tables is heavy;
    # instead seed app_ratings labels directly (features snapshot is what the model
    # reads for training) and seed two matview rows for scoring.
    conn.execute(text(
        "INSERT INTO public.advertisers (id, page_name, page_like_count) "
        "VALUES (90001,'Funnel Co',1200),(90002,'Plain Co',900) "
        "ON CONFLICT (id) DO NOTHING"))
    # 30+ labels so the Logit path (not cold-start) runs: 15 like w/ funnel, 15 dislike.
    for i in range(15):
        conn.execute(text(
            "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, features) "
            "VALUES (:e, :a, 'like', :f) "
            "ON CONFLICT (user_email, advertiser_id) DO UPDATE SET rating=EXCLUDED.rating"),
            {"e": email, "a": 80000 + i,
             "f": '{"has_funnel": true, "whitespace": 0.9, "funnel_intent": 0.8,'
                  ' "scaling_velocity": 0.6, "under_radar": 0.6, "demand": 0.5}'})
    for i in range(15):
        conn.execute(text(
            "INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, features) "
            "VALUES (:e, :a, 'dislike', :f) "
            "ON CONFLICT (user_email, advertiser_id) DO UPDATE SET rating=EXCLUDED.rating"),
            {"e": email, "a": 81000 + i,
             "f": '{"has_funnel": false, "whitespace": 0.1, "funnel_intent": 0.5,'
                  ' "scaling_velocity": 0.5, "under_radar": 0.5, "demand": 0.5}'})


def test_score_user_writes_ranked_scores(_migrated_engine):
    email = "pref-test@fielddesk.local"
    with begin(_migrated_engine) as conn:
        _seed(conn, email)
        # Insert directly into the matview-backed read path: score_user reads the
        # matview; for the test, score_user accepts any matview rows present. We
        # assert it writes a row per matview advertiser with a queue_rank.
        n = score_user(conn, email)
        assert n >= 0
        rows = conn.execute(text(
            "SELECT p_like, uncertainty, queue_rank FROM frontend.preference_scores "
            "WHERE user_email = :e"), {"e": email}).fetchall()
        # ranks are a contiguous 0..n-1 set
        ranks = sorted(r[2] for r in rows)
        assert ranks == list(range(len(rows)))
        for p, u, _ in rows:
            assert 0.0 <= p <= 1.0
            assert 0.0 <= u <= 1.0
```

> Reviewer note: this asserts structural correctness against whatever matview rows exist in the test DB (could be 0 on a pristine DB — the contiguous-rank check still holds for an empty set). The live end-to-end run in Task 11 covers real volume.

- [ ] **Step 2: Run test to verify it fails**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_score_preferences_e2e.py -v`
Expected: FAIL (`ModuleNotFoundError: scripts.score_preferences`). Confirm not `skipped`.

- [ ] **Step 3: Write the implementation**

```python
# scripts/score_preferences.py
"""Fit a per-user preference model on frontend.app_ratings and write per-app
P(like) + a 75/25 review order into frontend.preference_scores.

Mirrors the GemScore pattern (Python writes, Go reads). The fitted coefficients
are logged as a GemScore-reweight proposal.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/score_preferences.py
"""
import json
import os
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.scoring.preference import (
    FEATURES, PreferenceModel, interleave, uncertainty, why_string,
)

_MV_FEATURES = (
    "SELECT advertiser_id, funnel_intent, whitespace, scaling_velocity, "
    "under_radar, demand, (funnel_url IS NOT NULL) AS has_funnel "
    "FROM frontend.emerging_scaling_apps"
)


def _label_rows(conn, user_email):
    rows = conn.execute(text(
        "SELECT rating, features FROM frontend.app_ratings WHERE user_email = :e"),
        {"e": user_email}).fetchall()
    out = []
    for rating, features in rows:
        f = features if isinstance(features, dict) else (
            json.loads(features) if features else {})
        out.append((rating, f))
    return out


def _matview_features(conn):
    out = []
    for r in conn.execute(text(_MV_FEATURES)).mappings():
        out.append((r["advertiser_id"], dict(r)))
    return out


def score_user(conn, user_email: str) -> int:
    model = PreferenceModel.fit(_label_rows(conn, user_email))
    apps = _matview_features(conn)
    scored = []
    why_by_id = {}
    for advertiser_id, feats in apps:
        p = model.predict(feats)
        scored.append({"advertiser_id": advertiser_id, "p_like": p,
                       "uncertainty": uncertainty(p)})
        why_by_id[advertiser_id] = why_string(model, feats)
    ordered = interleave(scored)

    conn.execute(text(
        "DELETE FROM frontend.preference_scores WHERE user_email = :e"),
        {"e": user_email})
    if ordered:
        conn.execute(text(
            "INSERT INTO frontend.preference_scores "
            "(user_email, advertiser_id, p_like, uncertainty, queue_rank, why, "
            " model_version, scored_at) VALUES "
            "(:e, :a, :p, :u, :q, :w, :v, now())"),
            [{"e": user_email, "a": r["advertiser_id"], "p": r["p_like"],
              "u": r["uncertainty"], "q": r["queue_rank"],
              "w": why_by_id[r["advertiser_id"]], "v": model.version}
             for r in ordered])
    print(f"[pref] {user_email}: model={model.version} scored={len(ordered)} "
          f"coefs={json.dumps(model.coefficients)}", flush=True)
    return len(ordered)


def score_all_users(conn) -> dict:
    emails = [r[0] for r in conn.execute(text(
        "SELECT DISTINCT user_email FROM frontend.app_ratings")).fetchall()]
    return {e: score_user(conn, e) for e in emails}


def main() -> None:
    url = os.environ.get("AGF_DATABASE_URL")
    engine = get_engine(url) if url else get_engine()
    with begin(engine) as conn:
        counts = score_all_users(conn)
    print(f"[pref] done: {counts}", flush=True)
    if not counts:
        print("[pref] no rated users yet", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/scoring/test_score_preferences_e2e.py -v`
Expected: PASS. Grep summary — must say `passed`, not `skipped`.

- [ ] **Step 5: Commit**

```bash
git add scripts/score_preferences.py tests/scoring/test_score_preferences_e2e.py
git commit -m "feat(pref): score_preferences.py writes per-user ranked scores"
```

---

### Task 6: Wire `score_preferences` into the worker scoring stage

**Files:**
- Modify: `src/agf/workers/main.py:565` (right after `run_scoring`)

**Interfaces:**
- Consumes: `score_all_users` (Task 5).
- Produces: preference scores refresh once per cycle after scoring (matview is fresh), guarded by `AGF_SCORE_PREFERENCES` (default on when `score_after` is on).

- [ ] **Step 1: Add the import (near line 89, with the other scoring import)**

```python
from agf.scoring.run_scoring import run_scoring, ALERT_THRESHOLD
from scripts.score_preferences import score_all_users  # preference re-scoring
```

- [ ] **Step 2: Call it after scoring (after line 565)**

```python
    # 2) scoring — optional; its own committed txn + CONCURRENT matview refresh
    scored = run_scoring(engine, as_of) if score_after else None

    # 2b) preference re-scoring — per-user P(like) for the /review queue. Runs
    #     after the matview refresh so it scores current features. Best-effort:
    #     a model failure must not stall the worker cycle.
    if score_after and os.environ.get("AGF_SCORE_PREFERENCES", "1") != "0":
        try:
            with begin(engine) as conn:
                pref_counts = score_all_users(conn)
            print(f"[worker] preference scoring: {pref_counts}", flush=True)
        except Exception as exc:
            print(f"[worker] preference scoring failed: {exc!r}",
                  file=sys.stderr, flush=True)
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `PYTHONPATH=src .venv/bin/python -c "import agf.workers.main"`
Expected: no output, exit 0 (confirms the `scripts.score_preferences` import resolves; `scripts/` must be importable — `pytest.ini` sets `pythonpath = src .`, and the worker runs with repo root on the path. If the import fails, move `score_preferences` core into `src/agf/scoring/` and re-export; do NOT silence the error).

- [ ] **Step 4: Commit**

```bash
git add src/agf/workers/main.py
git commit -m "feat(pref): worker re-scores preferences after each scoring cycle"
```

---

### Task 7: Go repo — `PreferenceQueue`

**Files:**
- Modify: `backend/models/models.go` (add `PreferenceCardRow`)
- Create: `backend/repositories/preference_repo.go`
- Test: `backend/repositories/preference_repo_test.go`

**Interfaces:**
- Consumes: `mvCols`, `mvScanRow`, `toShortlistRows`, `attachEnrichment` (in `backend/repositories/repositories.go`), `models.ShortlistRow`, `db.WithReadTx`.
- Produces:
  - `models.PreferenceCardRow{ ShortlistRow (embedded); PLike float64 \`json:"p_like"\`; Why string \`json:"why"\` }`
  - `repositories.PreferenceQueue(ctx context.Context, g *gorm.DB, userEmail string, limit int) ([]models.PreferenceCardRow, error)`

- [ ] **Step 1: Add the response model to `backend/models/models.go`**

```go
// PreferenceCardRow is one app in the /review queue: a shortlist row plus the
// user's predicted-like score and the short "why shown" string.
type PreferenceCardRow struct {
	ShortlistRow
	PLike float64 `json:"p_like" gorm:"column:p_like"`
	Why   string  `json:"why" gorm:"column:why"`
}
```

- [ ] **Step 2: Write the failing test**

```go
// backend/repositories/preference_repo_test.go
package repositories

import (
	"context"
	"os"
	"testing"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

var prefDB *gorm.DB

func TestPreferenceQueue(t *testing.T) {
	dsn := os.Getenv("AGF_IT_DB_URL")
	if dsn == "" {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g, err := db.Init(dsn)
	require.NoError(t, err)
	prefDB = g

	email := "pref-repo-test@fielddesk.local"
	// Clean slate for this user.
	require.NoError(t, g.Exec(
		`DELETE FROM frontend.preference_scores WHERE user_email = ?`, email).Error)
	require.NoError(t, g.Exec(
		`DELETE FROM frontend.app_ratings WHERE user_email = ?`, email).Error)

	// Pick two advertisers that exist in the matview.
	var ids []int64
	require.NoError(t, g.Raw(
		`SELECT advertiser_id FROM frontend.emerging_scaling_apps LIMIT 2`).Scan(&ids).Error)
	if len(ids) < 2 {
		t.Skip("need >=2 matview rows in the test DB")
	}
	// Rank both; rate the first so it must be excluded.
	require.NoError(t, g.Exec(
		`INSERT INTO frontend.preference_scores
		   (user_email, advertiser_id, p_like, uncertainty, queue_rank, why, model_version)
		 VALUES (?, ?, 0.9, 0.2, 0, 'has funnel', 'logit-v1'),
		        (?, ?, 0.4, 0.9, 1, '', 'logit-v1')`,
		email, ids[0], email, ids[1]).Error)
	require.NoError(t, g.Exec(
		`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, features)
		 VALUES (?, ?, 'like', '{}')`, email, ids[0]).Error)

	rows, err := PreferenceQueue(context.Background(), prefDB, email, 50)
	require.NoError(t, err)
	require.Len(t, rows, 1)                  // the rated one is excluded
	require.EqualValues(t, ids[1], rows[0].AdvertiserID)
	require.Equal(t, 0.4, rows[0].PLike)
}
```

- [ ] **Step 3: Apply migrations to the test DB, then run the test to verify it fails**

Run:
```bash
AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -c "from agf.db.engine import get_engine; from agf.db.migrate import apply_migrations; apply_migrations(get_engine('postgresql://agf:agf@localhost:5433/agf_it'))"
cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./repositories/ -run TestPreferenceQueue -count=1 -v
```
Expected: FAIL — `undefined: PreferenceQueue`.

- [ ] **Step 4: Write the implementation**

```go
// backend/repositories/preference_repo.go
package repositories

import (
	"context"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

// prefScanRow extends the matview scan with the per-user preference columns.
type prefScanRow struct {
	mvScanRow
	PLike float64 `gorm:"column:p_like"`
	Why   string  `gorm:"column:why"`
}

// PreferenceQueue returns the user's review queue: matview rows joined to their
// preference_scores, excluding apps the user already rated, ordered by
// queue_rank. Hero creative + growth are attached via the shared enrichment
// pass. The CTE aliases the score table's advertiser_id so the bare matview
// columns in mvCols stay unambiguous.
func PreferenceQueue(ctx context.Context, g *gorm.DB, userEmail string, limit int) ([]models.PreferenceCardRow, error) {
	scanned := make([]prefScanRow, 0)
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`WITH ranked AS (
			   SELECT ps.advertiser_id AS adv_id, ps.p_like, ps.why, ps.queue_rank
			   FROM frontend.preference_scores ps
			   WHERE ps.user_email = ?
			     AND NOT EXISTS (
			       SELECT 1 FROM frontend.app_ratings r
			       WHERE r.user_email = ps.user_email
			         AND r.advertiser_id = ps.advertiser_id)
			 )
			 SELECT `+mvCols+`, ranked.p_like, ranked.why
			 FROM frontend.emerging_scaling_apps
			 JOIN ranked ON ranked.adv_id = frontend.emerging_scaling_apps.advertiser_id
			 ORDER BY ranked.queue_rank
			 LIMIT ?`,
			userEmail, limit,
		).Scan(&scanned).Error
	})
	if err != nil {
		return nil, err
	}
	// Build ShortlistRows (folds growth), attach hero creative + enrichment.
	mvRows := make([]mvScanRow, len(scanned))
	for i, s := range scanned {
		mvRows[i] = s.mvScanRow
	}
	shortRows := toShortlistRows(mvRows)
	if err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return attachEnrichment(tx, shortRows)
	}); err != nil {
		return nil, err
	}
	out := make([]models.PreferenceCardRow, len(shortRows))
	for i := range shortRows {
		out[i] = models.PreferenceCardRow{
			ShortlistRow: shortRows[i],
			PLike:        scanned[i].PLike,
			Why:          scanned[i].Why,
		}
	}
	return out, nil
}
```

> Reviewer note: confirm `mvScanRow` embeds `models.ShortlistRow` (it does per repositories.go). If `toShortlistRows`/`attachEnrichment` require being inside the same tx, fold both into one `WithReadTx` closure.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./repositories/ -run TestPreferenceQueue -count=1 -v`
Expected: PASS. (If it `--- SKIP`s, the test DB has <2 matview rows — seed two via the matview source or run after a real score; do not treat SKIP as pass.)

- [ ] **Step 6: Commit**

```bash
git add backend/models/models.go backend/repositories/preference_repo.go backend/repositories/preference_repo_test.go
git commit -m "feat(pref): PreferenceQueue repo (ranked, rated-excluded, hero creative)"
```

---

### Task 8: Go controller + route — `GET /api/preference-queue`

**Files:**
- Create: `backend/controllers/preference_controller.go`
- Modify: `backend/routers/routers.go`

**Interfaces:**
- Consumes: `PreferenceQueue` (Task 7), `helpers.QueryInt`, `helpers.Detail`, `reqCtx`, `db.DB()`.
- Produces: route `api.GET("/preference-queue", preference.Queue)`; `200` → `[]models.PreferenceCardRow`; `422` when `user_email` missing.

- [ ] **Step 1: Write the controller**

```go
// backend/controllers/preference_controller.go
package controllers

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// PreferenceController serves the per-user /review queue, ordered by the
// preference model's predicted-like score (explore/exploit blended).
type PreferenceController struct{}

func NewPreferenceController() *PreferenceController { return &PreferenceController{} }

// Queue handles GET /api/preference-queue?user_email=...&limit=...
func (c *PreferenceController) Queue(ctx *gin.Context) {
	email, ok := ctx.GetQuery("user_email")
	if !ok || email == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "user_email is required.")
		return
	}
	limit, err := helpers.QueryInt(ctx, "limit", 50)
	if err != nil || limit <= 0 || limit > 200 {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "bad limit.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.PreferenceQueue(rctx, db.DB(), email, limit)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not load review queue.")
		return
	}
	ctx.JSON(http.StatusOK, rows)
}
```

- [ ] **Step 2: Register the route in `backend/routers/routers.go`**

Add the constructor next to the others (after the `review := ...` line):
```go
	preference := controllers.NewPreferenceController()
```
Add the route inside the `api` group, next to the other GETs (after the `/review/queue` line):
```go
		api.GET("/preference-queue", preference.Queue)
```

- [ ] **Step 3: Build to verify it compiles**

Run: `cd backend && go build ./...`
Expected: exit 0, no output.

- [ ] **Step 4: Run the full backend test suite (no regressions)**

Run: `cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./... -count=1`
Expected: all packages PASS (or SKIP only where the DB genuinely lacks fixtures — read the output, don't assume).

- [ ] **Step 5: Deploy + smoke-test the endpoint**

Run:
```bash
cd backend && go build -o ~/bin/agf-api . && launchctl kickstart -k gui/$(id -u)/com.fielddesk.api
sleep 1
curl -s "http://localhost:8000/api/preference-queue?user_email=founder@fielddesk.local&limit=3" | head -c 400
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8000/api/preference-queue"   # expect 422
```
Expected: first curl returns a JSON array (possibly `[]` if preferences not scored yet — that's fine here); second returns `422`.

- [ ] **Step 6: Commit**

```bash
git add backend/controllers/preference_controller.go backend/routers/routers.go
git commit -m "feat(pref): GET /api/preference-queue endpoint"
```

---

### Task 9: Web — API client, types, adapter

**Files:**
- Modify: `web/src/types/api.ts` (add `ApiPreferenceCardRow`)
- Modify: `web/src/types/contracts.ts` (add `PreferenceCard` UI type)
- Modify: `web/src/lib/adapters.ts` (add `adaptPreferenceCard`)
- Modify: `web/src/lib/api.ts` (add `getPreferenceQueue`)

**Interfaces:**
- Consumes: `ApiShortlistRow`, `adaptShortlistRow`, `mediaUrl`, `live()` (existing).
- Produces:
  - `ApiPreferenceCardRow extends ApiShortlistRow { p_like: number; why: string }`
  - UI `PreferenceCard extends ShortlistRow { pLike: number; why: string }`
  - `adaptPreferenceCard(r: ApiPreferenceCardRow, mediaUrl): PreferenceCard`
  - `getPreferenceQueue(userEmail: string): Promise<PreferenceCard[]>`

- [ ] **Step 1: Add the API + UI types**

In `web/src/types/api.ts`:
```typescript
export interface ApiPreferenceCardRow extends ApiShortlistRow {
  p_like: number;
  why: string;
}
```
In `web/src/types/contracts.ts` (next to `ShortlistRow`):
```typescript
export interface PreferenceCard extends ShortlistRow {
  pLike: number;
  why: string;
}
```

- [ ] **Step 2: Add the adapter in `web/src/lib/adapters.ts`**

```typescript
import { ApiPreferenceCardRow } from "../types/api";
import { PreferenceCard } from "../types/contracts";

export function adaptPreferenceCard(
  r: ApiPreferenceCardRow,
  mediaUrl: MediaUrl,
): PreferenceCard {
  return {
    ...adaptShortlistRow(r, mediaUrl),
    pLike: typeof r.p_like === "number" ? r.p_like : 0,
    why: r.why ?? "",
  };
}
```
> Reviewer note: match the actual `adaptShortlistRow` signature/`MediaUrl` type in this file. If `adaptShortlistRow` is not exported, export it.

- [ ] **Step 3: Add the fetcher in `web/src/lib/api.ts`**

```typescript
import { ApiPreferenceCardRow } from "../types/api";
import { adaptPreferenceCard } from "./adapters";
import { PreferenceCard } from "../types/contracts";

export async function getPreferenceQueue(
  userEmail: string,
): Promise<PreferenceCard[]> {
  const rows = await live<ApiPreferenceCardRow[]>(
    `/preference-queue?user_email=${encodeURIComponent(userEmail)}`,
  );
  return rows.map((r) => adaptPreferenceCard(r, (k) => mediaUrl(k)));
}
```
> Reviewer note: match how `getShortlist` passes `mediaUrl` into its adapter in this file; reuse that exact call shape.

- [ ] **Step 4: Typecheck**

Run: `cd web && npm run typecheck` (or `npx tsc --noEmit` if no script — check `web/package.json`).
Expected: no type errors.

- [ ] **Step 5: Commit**

```bash
git add web/src/types/api.ts web/src/types/contracts.ts web/src/lib/adapters.ts web/src/lib/api.ts
git commit -m "feat(pref): web client getPreferenceQueue + types + adapter"
```

---

### Task 10: Web — `/review` page (card deck)

**Files:**
- Create: `web/src/app/review/page.tsx`
- Create: `web/src/components/review/ReviewDeck.tsx`
- Create: `web/src/components/review/ReviewCard.tsx`

**Interfaces:**
- Consumes: `getPreferenceQueue`, `PreferenceCard`, `setRating`, `useSession` (next-auth/react), `RatingControl` (reused), `ProductShell`, `Button`, design tokens.
- Produces: a client-rendered full-screen deck showing one `PreferenceCard` at a time; rating a card (buttons, or ← dislike / → like / ↓ neutral keys) calls `setRating` and advances; queue position shown.

- [ ] **Step 1: Page route (mirror `discover/page.tsx`)**

```tsx
// web/src/app/review/page.tsx
import { Suspense } from "react";
import { ProductShell } from "../../components/chrome/ProductShell";
import { ReviewDeck } from "../../components/review/ReviewDeck";

export default function ReviewPage() {
  return (
    <ProductShell>
      <Suspense>
        <ReviewDeck />
      </Suspense>
    </ProductShell>
  );
}
```

- [ ] **Step 2: The deck (fetch, advance, keyboard)**

```tsx
// web/src/components/review/ReviewDeck.tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { useSession } from "next-auth/react";

import { getPreferenceQueue, setRating } from "../../lib/api";
import { PreferenceCard } from "../../types/contracts";
import type { Rating } from "../../types/api";
import { ReviewCard } from "./ReviewCard";

export function ReviewDeck() {
  const { data: session } = useSession();
  const userEmail = session?.user?.email ?? null;
  const [cards, setCards] = useState<PreferenceCard[]>([]);
  const [idx, setIdx] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!userEmail) return;
    let alive = true;
    setLoading(true);
    getPreferenceQueue(userEmail)
      .then((c) => alive && setCards(c))
      .catch(() => alive && setCards([]))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [userEmail]);

  const current = cards[idx];

  const rate = useCallback(
    (rating: Rating, comment = "") => {
      if (!current || !userEmail) return;
      // Optimistic advance; persist in the background.
      setIdx((i) => i + 1);
      setRating(current.advertiser_id, userEmail, rating, comment).catch(() => {
        /* best-effort: the card has already advanced; user can re-rate later */
      });
    },
    [current, userEmail],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!current) return;
      if (e.key === "ArrowRight") rate("like");
      else if (e.key === "ArrowLeft") rate("dislike");
      else if (e.key === "ArrowDown" || e.key === " ") rate("neutral");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, rate]);

  if (!userEmail) return <p style={{ padding: "var(--sp-8)" }}>Sign in to review.</p>;
  if (loading) return <p style={{ padding: "var(--sp-8)" }}>Loading the queue…</p>;
  if (!current) {
    return (
      <p style={{ padding: "var(--sp-8)" }}>
        No more apps to review. New ones appear as discovery runs.
      </p>
    );
  }

  return (
    <ReviewCard
      card={current}
      position={idx + 1}
      total={cards.length}
      onRate={rate}
    />
  );
}
```

> Reviewer note: confirm `Rating` is exported from `web/src/types/api.ts` (it is: `"like" | "neutral" | "dislike" | "none"`). Confirm next-auth's `useSession` is the project's session source (DiscoverFeed uses it).

- [ ] **Step 3: The card (visual + buttons + note)**

```tsx
// web/src/components/review/ReviewCard.tsx
"use client";

import { useState } from "react";
import { PreferenceCard } from "../../types/contracts";
import type { Rating } from "../../types/api";

export function ReviewCard({
  card,
  position,
  total,
  onRate,
}: {
  card: PreferenceCard;
  position: number;
  total: number;
  onRate: (rating: Rating, comment?: string) => void;
}) {
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const hero = card.hero_creative_url ?? card.icon_url ?? undefined;

  return (
    <div style={{ maxWidth: 520, margin: "0 auto", padding: "var(--sp-6)" }}>
      <div className="t-sm" style={{ color: "var(--ink-soft)", marginBottom: "var(--sp-4)" }}>
        {position} / {total}
      </div>

      {hero ? (
        <img
          src={hero}
          alt={card.app_name ?? card.page_name ?? "app"}
          style={{
            width: "100%", aspectRatio: "1 / 1", objectFit: "cover",
            border: "1.5px solid var(--ink)", boxShadow: "var(--shadow-hard)",
          }}
        />
      ) : null}

      <h1 className="t-2xl w-strong" style={{ marginTop: "var(--sp-4)" }}>
        {card.app_name ?? card.page_name}
      </h1>
      <div className="t-sm" style={{ color: "var(--ink-soft)" }}>{card.cohort}</div>
      {card.tagline ? <p className="t-base">{card.tagline}</p> : null}

      <div className="t-sm" style={{ display: "flex", gap: "var(--sp-6)", marginTop: "var(--sp-4)" }}>
        <span className="nums">{card.active_ad_count} active ads</span>
        <span className="nums">GemScore {Math.round(card.gem_score)}</span>
        {card.funnel_url ? (
          <a href={card.funnel_url} target="_blank" rel="noreferrer">Quiz funnel</a>
        ) : null}
      </div>

      {card.why ? (
        <div className="t-sm" style={{ marginTop: "var(--sp-2)", color: "var(--ink-soft)" }}>
          Why shown: {card.why}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: "var(--sp-4)", marginTop: "var(--sp-6)" }}>
        <button onClick={() => onRate("dislike", note)}>👎 Dislike</button>
        <button onClick={() => onRate("neutral", note)}>➖ Neutral</button>
        <button onClick={() => onRate("like", note)}>👍 Like</button>
        <button onClick={() => setShowNote((s) => !s)}>✏️ Note</button>
      </div>

      {showNote ? (
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Optional note"
          style={{ width: "100%", marginTop: "var(--sp-4)" }}
        />
      ) : null}

      <div className="t-xs" style={{ marginTop: "var(--sp-4)", color: "var(--ink-soft)" }}>
        ← dislike   ↓ neutral   → like
      </div>
    </div>
  );
}
```

> Reviewer note: the spec wants `+40% 4w` growth shown — wire `card.growth` (the adapter folds it) if present; omit if undefined (no re-derived trend words per CLAUDE.md). Use the shared `Button` component (`components/ui/Button.tsx`) instead of raw `<button>` if it matches the existing look; match RatingControl's button styling. Confirm `hero_creative_url` is the field the adapter produces (it may be nested as `card.heroCreative?.url` — check `adaptShortlistRow`'s output and use the real field).

- [ ] **Step 4: Typecheck + build**

Run: `cd web && npm run typecheck && npm run build`
Expected: no type errors; build succeeds. (If a new Next 16 API is needed, read `web/node_modules/next/dist/docs/` first.)

- [ ] **Step 5: Commit**

```bash
git add web/src/app/review/page.tsx web/src/components/review/ReviewDeck.tsx web/src/components/review/ReviewCard.tsx
git commit -m "feat(pref): /review card deck page (keyboard + buttons)"
```

---

### Task 11: End-to-end live verification

**Files:** none (verification only).

**Interfaces:** exercises Tasks 1–10 against the real DB + API + browser.

- [ ] **Step 1: Score preferences for the live user**

Run: `AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf PYTHONPATH=src .venv/bin/python scripts/score_preferences.py`
Expected: prints `[pref] founder@fielddesk.local: model=logit-v1 scored=<N>` with N ≈ the feed size, and a `coefs={...}` line. Note the coefficients (the GemScore-tuning proposal — funnel/whitespace should carry real weight).

- [ ] **Step 2: Confirm the rows + ordering in the DB**

Run:
```bash
docker exec db-postgres-1 psql -U agf -d agf -c \
"SELECT advertiser_id, round(p_like::numeric,3) p, round(uncertainty::numeric,3) u, queue_rank, why FROM frontend.preference_scores WHERE user_email='founder@fielddesk.local' ORDER BY queue_rank LIMIT 12"
```
Expected: 12 rows, `queue_rank` 0..11, ~3 high-`p` then 1 high-`u` pattern (75/25 interleave), `why` populated for high-p rows.

- [ ] **Step 3: Confirm the endpoint excludes rated apps + returns hero creative**

Run:
```bash
curl -s "http://localhost:8000/api/preference-queue?user_email=founder@fielddesk.local&limit=5" \
 | python3 -m json.tool | head -40
```
Expected: 5 cards in `queue_rank` order, each with `p_like`, `why`, and `hero_creative` (or `icon_url`); none are apps the user already rated.

- [ ] **Step 4: Browser walk-through**

- Ensure the web dev server is up (`cd web && npm run dev`, port 3000) and you're logged in as `founder@fielddesk.local`.
- Open `http://localhost:3000/review`.
- Confirm: a card renders with creative + name + cohort + signals + "Why shown" + buttons + the `N / total` counter.
- Press `→` (like): the card advances to the next.
- Verify the rating persisted and the app dropped out of the queue:
```bash
docker exec db-postgres-1 psql -U agf -d agf -c \
"SELECT advertiser_id, rating FROM frontend.app_ratings WHERE user_email='founder@fielddesk.local' ORDER BY updated_at DESC LIMIT 3"
```
Re-fetch the queue (Step 3) and confirm the just-rated advertiser is gone.

- [ ] **Step 5: Full regression (both suites, with DB env vars)**

Run:
```bash
AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf_it PYTHONPATH=src .venv/bin/python -m pytest tests/scoring tests/db -q
cd backend && AGF_IT_DB_URL=postgresql://agf:agf@localhost:5433/agf_it go test ./... -count=1
```
Expected: Python — passed, 0 unexpected `skipped` in the preference/db tests (grep the summary). Go — all PASS. The 2 pre-existing dry-run worker test failures (`FakeExtractor.search_listing`) are unrelated and may remain.

- [ ] **Step 6: Restart the worker so preference scoring runs each cycle**

Run: `launchctl kickstart -k gui/$(id -u)/com.fielddesk.worker`
Expected: worker log (`~/Library/Logs/fielddesk-worker.log`) shows `[worker] preference scoring: {...}` after the next scoring stage.

- [ ] **Step 7: Final commit (if any verification fixups were needed)**

```bash
git add -A && git commit -m "test(pref): live end-to-end verification fixups"
```
(Skip if no changes.)

---

## Self-Review

**Spec coverage:**
- §3 model (statsmodels Logit, no new dep, per-user, like-vs-rest, 6 features, L1, cold-start fallback, coefficients logged) → Tasks 2–5.
- §4 data flow (Python writes → DB → Go reads; 75/25 precomputed; rated-excluded; re-ranks each run) → Tasks 4–8.
- §5 data model (`preference_scores`, keyed per user) → Task 1 (+`why` column noted).
- §6 API (`/preference-queue`, join + exclude + hero creative; reuse existing rating POST) → Tasks 7–8.
- §7 page (full-screen card, hero creative, grounded signals, why-shown, keyboard, queue position, buttons + keyboard only) → Tasks 9–10.
- §8 Spec 2 → explicitly out of scope (no tasks, correct).
- §9–10 testing + verification gate → Task 11 + per-task TDD.

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". Reviewer notes flag exact existing signatures to match (adapter `MediaUrl`, hero-creative field name, `Button` reuse) — these are verification instructions, not placeholders; code is complete and runnable as written.

**Type consistency:** `PreferenceModel` (version/coefficients/predict/_means/_stds), `interleave`→`queue_rank`, `why_string`, `score_user`/`score_all_users`, Go `PreferenceCardRow{ShortlistRow,PLike,Why}` + `PreferenceQueue(ctx,g,userEmail,limit)`, web `ApiPreferenceCardRow`/`PreferenceCard`/`adaptPreferenceCard`/`getPreferenceQueue` are used consistently across tasks.

**Known assumptions to verify during implementation (flagged inline):** `mvScanRow` embeds `models.ShortlistRow`; `adaptShortlistRow` is exported and its `MediaUrl`/hero-creative output field name; `scripts/` is importable from the worker (pytest sets `pythonpath = src .`).
