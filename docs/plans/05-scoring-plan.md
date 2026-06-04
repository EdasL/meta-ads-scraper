# Plan 5 — Velocity tracker + GemScore — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the ads/metrics already in Postgres into a cohort-normalized, multiplicative **GemScore** with 5 vetoes, and expose the survivors through the product matview `frontend.emerging_scaling_apps` (Plan 5 is its sole owner).

**Architecture:** Two halves. (1) **Tracker** (`src/agf/tracker/snapshots.py`) writes daily ad-metric snapshots (via `extractor.ad_details`, §3.4) and weekly advertiser rollups into the spine (conn-first, §3.3). (2) **Scoring** (`src/agf/scoring/`) is mostly *pure stats*: cohort normalization (MAD-z / percentile), derivative features (log-growth WoW, acceleration, EWMA), three sub-scores combined into `gem_score = 100·clamp01(SV)^.5·clamp01(UR)^.35·clamp01(FI)^.15` (multiplicative AND-gate + per-axis floor for sparse data), and 5 post-score vetoes. `run_scoring.score_all` builds features → normalizes within cohort → scores → vetoes → writes `adtech.advertiser_scores`; a separate autocommit `refresh_emerging_matview` does `REFRESH … CONCURRENTLY`.

**Tech Stack:** Python 3.13 · numpy / scipy / statsmodels (STL) · SQLAlchemy Core + psycopg · PostgreSQL 17 (table + materialized view) · pytest (pure-stats unit + live-Postgres integration).

---

## Frozen inputs this plan builds against (do not change)

- **§3.4 `Extractor.ad_details(ad_archive_id) -> AdDetails(eu_total_reach: int|None, active: bool)`** — async. The tracker calls it per active ad.
- **§3.3 repo** (`src/agf/db/repo.py`, conn-first, **never commits**): `record_ad_metric(conn, ad_id, snapshot_date, eu_total_reach, active)`, `upsert_app_advertiser_metric(conn, advertiser_id, week_start, active_ad_count, distinct_creative_count, total_eu_reach)`.
- **§3.5 tables**: `public.ads(id, external_ad_id, advertiser_id, app_id, cta_type, collation_count, is_active, start_date, …)`, `public.apps(id, store, store_id, genre, rating_count, release_date, is_emerging, name)`, `public.advertisers(id, page_id, page_name, parent_company_id, ig_followers)`, `public.creatives(ad_id, …)`, `public.funnels(ad_id, …)`, `adtech.ad_metrics(ad_id, snapshot_date, eu_total_reach, active, PK(ad_id,snapshot_date))`, `adtech.app_advertiser_metrics(advertiser_id, week_start, active_ad_count, distinct_creative_count, total_eu_reach, PK(advertiser_id,week_start))`.
- **§3.6 matview columns (FROZEN — Plan 7 builds against these exactly):** `advertiser_id, cohort, page_name, app_id (NULLABLE), app_name, gem_score, scaling_velocity, under_radar, funnel_intent, active_ad_count, max_longevity_days, alert (bool), refreshed_at` + **UNIQUE INDEX on `advertiser_id`**.
- **Reused pure helper:** `agf.scale.longevity_days(start_epoch, now_epoch) -> int`.

## Hard rules carried from prior plans (each cost us something)

- **conn-first, never commit** inside repo/scoring write functions — the caller owns the transaction.
- **Postgres `now()` is transaction-frozen** → use `clock_timestamp()` for event timestamps (matview `refreshed_at`, `scored_at` default).
- **`REFRESH MATERIALIZED VIEW … CONCURRENTLY`** (a) needs the UNIQUE index (this plan creates it), (b) **cannot run inside a transaction block**, so it needs its **own autocommit connection** — never the rolled-back `pg_conn` fixture.
- **`pg_conn`** (root `conftest.py`) wraps each test in a txn that is **rolled back** → use it for conn-first reads/writes. Anything that must `REFRESH CONCURRENTLY` (sees only committed rows) uses **`_migrated_engine`** directly with manual cleanup (mirror `tests/db/test_models_match_schema.py::test_begin_commits_and_rolls_back`).
- **Schema numbering P5 = 050–099.** `migrate.py` globs lexically; `requirements.txt` is append-only per `# --- plan-N ---` section.
- **Do not touch other phases' owned files.** In particular **do not edit `src/agf/db/models.py`** (Plan 3): `adtech.advertiser_scores` is a Plan-5 internal table and the drift test (`test_models_match_schema`) only checks tables it mirrors, so leaving the new table out keeps it green. The matview is not a table and is never mirrored.
- **RUN THE ASSEMBLED THING LIVE before claiming done** (fakes + contract-adherence hid a real bug in Plan 4).

## Files created by this plan

| File | Responsibility |
|---|---|
| `src/agf/tracker/__init__.py` | package marker |
| `src/agf/tracker/snapshots.py` | `snapshot_ad_metrics`, `snapshot_advertiser_week` |
| `src/agf/scoring/__init__.py` | package marker |
| `src/agf/scoring/cohorts.py` | `cohort_key`, `robust_z`, `pctile` |
| `src/agf/scoring/features.py` | `log_growth_wow`, `acceleration`, `ewma`, `FeatureVector`, `build_features` |
| `src/agf/scoring/gemscore.py` | `clamp01`, `weighted_axis`, `scaling_velocity`, `under_radar`, `funnel_intent`, `gem_score` |
| `src/agf/scoring/vetoes.py` | `VetoResult`, the 5 vetoes, `apply_vetoes` |
| `src/agf/scoring/run_scoring.py` | `score_all`, `refresh_emerging_matview`, `run_scoring`, `ALERT_THRESHOLD` |
| `db/schema/050_scoring.sql` | `adtech.advertiser_scores` table |
| `db/schema/051_emerging_matview.sql` | `frontend.emerging_scaling_apps` matview + UNIQUE index |
| `tests/tracker/{__init__.py,conftest.py,test_*.py}` | tracker tests |
| `tests/scoring/{__init__.py,test_*.py}` | scoring tests |
| `requirements.txt` | append `# --- plan-5 ---` (numpy, scipy, statsmodels) |

## Task ↔ master-plan mapping (built in dependency order: pure leaves → DB → live e2e)

| Plan task | master-plan | what |
|---|---|---|
| Task 0 | — | scaffold packages + pin numpy/scipy/statsmodels |
| Task 1 | P5-T3 | `cohorts.py` |
| Task 2 | P5-T4 | `features.py` primitives |
| Task 3 | P5-T6 | sub-scores + `weighted_axis` |
| Task 4 | P5-T7 | `gem_score` |
| Task 5 | P5-T8 | `vetoes.py` |
| Task 6 | P5-T9 | `050`+`051` SQL + matview shape test |
| Task 7 | P5-T1 | `snapshot_ad_metrics` |
| Task 8 | P5-T2 | `snapshot_advertiser_week` |
| Task 9 | P5-T5 | `build_features` |
| Task 10 | P5-T10 | `run_scoring.score_all` + refresh + live e2e |
| Task 11 | — | live verification + update §5 dashboard + HANDOFF |

---

## Task 0: Scaffold packages + pin stats deps

**Files:**
- Create: `src/agf/tracker/__init__.py`, `src/agf/scoring/__init__.py`, `tests/tracker/__init__.py`, `tests/scoring/__init__.py`
- Modify: `requirements.txt` (append section)

- [ ] **Step 1: Create the package markers**

All four `__init__.py` files are empty.

- [ ] **Step 2: Append the plan-5 deps to `requirements.txt`**

```
# --- plan-5 ---
numpy==2.2.1
scipy==1.15.1
statsmodels==0.14.4   # pulls pandas + patsy (STL deseasonalize in vetoes.py)
```

- [ ] **Step 3: Install + smoke-import**

Run: `.venv/bin/pip install -r requirements.txt && .venv/bin/python -c "import numpy, scipy, statsmodels.tsa.seasonal as s; print('ok', s.STL.__name__)"`
Expected: prints `ok STL`

- [ ] **Step 4: Commit**

```bash
git add src/agf/tracker/__init__.py src/agf/scoring/__init__.py tests/tracker/__init__.py tests/scoring/__init__.py requirements.txt
git commit -m "chore(plan-5): scaffold tracker/scoring packages, pin numpy/scipy/statsmodels"
```

---

## Task 1 (P5-T3): `cohorts.py` — cohort key + robust normalization

**Files:**
- Create: `src/agf/scoring/cohorts.py`
- Test: `tests/scoring/test_cohorts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_cohorts.py
import numpy as np
from scipy.stats import median_abs_deviation
from agf.scoring.cohorts import cohort_key, robust_z, pctile


def test_cohort_key_is_category_x_country():
    assert cohort_key("Health & Fitness", "US") == "Health & Fitness×US"
    assert cohort_key(None, "US") == "unknown×US"          # web funnels / no app


def test_robust_z_matches_scipy_reference():
    vals = [10.0, 12.0, 11.0, 9.0, 100.0]                  # 100 is an outlier
    med = float(np.median(vals))
    scale = median_abs_deviation(vals, scale="normal")     # == MAD * 1.4826
    expected = (100.0 - med) / scale
    assert robust_z(100.0, vals) == round(expected, 10) or abs(robust_z(100.0, vals) - expected) < 1e-9


def test_robust_z_degenerate_is_zero_not_nan():
    assert robust_z(5.0, [5.0, 5.0, 5.0]) == 0.0           # MAD == 0
    assert robust_z(5.0, [5.0]) == 0.0                     # singleton


def test_pctile_is_midrank_in_unit_interval():
    vals = [1.0, 2.0, 3.0, 4.0]
    assert pctile(4.0, vals) == 0.875                      # (3 below + 0.5*1 eq)/4
    assert pctile(1.0, vals) == 0.125
    assert pctile(5.0, [5.0]) == 0.5                       # singleton -> neutral


def test_pctile_empty_is_neutral():
    assert pctile(1.0, []) == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/scoring/test_cohorts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.scoring.cohorts'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/scoring/cohorts.py
"""Cohort identity + robust within-cohort normalization (master-plan §6 P5-T3).
All GemScore normalization is within a (category × country) cohort."""
import numpy as np

_MAD_TO_STD = 1.4826022185056018  # 1/Φ⁻¹(0.75); matches scipy median_abs_deviation(scale="normal")


def cohort_key(category, country) -> str:
    """Stable cohort label. NULL category (web funnels / unlinked) -> 'unknown'."""
    return f"{category or 'unknown'}×{country or 'US'}"


def robust_z(x: float, values) -> float:
    """Median/MAD z-score (outlier-robust). Degenerate (MAD==0) -> 0.0, never NaN."""
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return 0.0
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    if mad == 0.0:
        return 0.0
    return (x - med) / (_MAD_TO_STD * mad)


def pctile(x: float, values) -> float:
    """Midrank percentile in [0,1]: (count<x + 0.5*count==x)/n. Empty/singleton -> 0.5."""
    arr = np.asarray(list(values), dtype=float)
    n = arr.size
    if n == 0:
        return 0.5
    below = float(np.count_nonzero(arr < x))
    equal = float(np.count_nonzero(arr == x))
    return (below + 0.5 * equal) / n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/scoring/test_cohorts.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/cohorts.py tests/scoring/test_cohorts.py
git commit -m "feat(plan-5): cohort_key + robust_z(MAD) + pctile within-cohort normalization"
```

---

## Task 2 (P5-T4): `features.py` primitives — log-growth, acceleration, EWMA

**Files:**
- Create: `src/agf/scoring/features.py` (primitives only; `build_features` is added in Task 9)
- Test: `tests/scoring/test_features_primitives.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_features_primitives.py
import math
from agf.scoring.features import log_growth_wow, acceleration, ewma


def test_log_growth_flat_is_zero():
    assert log_growth_wow([5, 5, 5]) == 0.0
    assert log_growth_wow([7]) == 0.0          # <2 points -> 0


def test_log_growth_rising_is_positive():
    g = log_growth_wow([2, 4])                  # log((4+1)/(2+1)) = log(5/3)
    assert g == math.log(5 / 3)
    assert g > 0


def test_acceleration_flat_is_zero():
    assert acceleration([5, 5, 5]) == 0.0
    assert acceleration([1, 2]) == 0.0         # <3 points -> 0


def test_acceleration_convex_positive_concave_negative():
    assert acceleration([2, 4, 8]) > 0         # growth rate increasing
    assert acceleration([1, 4, 5]) < 0         # growth rate decreasing


def test_ewma_constant_series_returns_constant():
    assert ewma([3, 3, 3], alpha=0.5) == 3.0


def test_ewma_weights_recent_more():
    # alpha=0.5: acc=1 -> 0.5*5+0.5*1=3
    assert ewma([1, 5], alpha=0.5) == 3.0
    assert ewma([], alpha=0.5) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/scoring/test_features_primitives.py -q`
Expected: FAIL — `ImportError: cannot import name 'log_growth_wow'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/scoring/features.py
"""Derivative velocity features (master-plan §6 P5-T4/T5).
Primitives operate on an ordered weekly series (oldest -> newest)."""
import math

_EPS = 1.0  # log smoothing so a 0 -> N week doesn't blow up


def log_growth_wow(series) -> float:
    """Latest week-over-week log growth. Flat or <2 points -> 0.0."""
    s = list(series)
    if len(s) < 2:
        return 0.0
    return math.log((s[-1] + _EPS) / (s[-2] + _EPS))


def acceleration(series) -> float:
    """Change in WoW log-growth (2nd derivative). Convex>0, concave<0, flat/<3 ->0."""
    s = list(series)
    if len(s) < 3:
        return 0.0
    g_prev = math.log((s[-2] + _EPS) / (s[-3] + _EPS))
    g_last = math.log((s[-1] + _EPS) / (s[-2] + _EPS))
    return g_last - g_prev


def ewma(series, alpha: float = 0.5) -> float:
    """Exponentially weighted moving average (latest value). Empty -> 0.0."""
    s = list(series)
    if not s:
        return 0.0
    acc = float(s[0])
    for x in s[1:]:
        acc = alpha * float(x) + (1 - alpha) * acc
    return acc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/scoring/test_features_primitives.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/features.py tests/scoring/test_features_primitives.py
git commit -m "feat(plan-5): velocity primitives — log_growth_wow, acceleration, ewma"
```

---

## Task 3 (P5-T6): sub-scores + `weighted_axis`

The three axes are weighted sums of already-cohort-normalized components in [0,1]. A `None` component (no data for that feature) is dropped and the remaining weights re-normalize. If **every** component is missing, the axis is `None` (the gem_score floor handles it in Task 4).

**Files:**
- Create: `src/agf/scoring/gemscore.py` (sub-scores + helpers; `gem_score` added in Task 4)
- Test: `tests/scoring/test_subscores.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_subscores.py
from agf.scoring.gemscore import (
    clamp01, weighted_axis, scaling_velocity, under_radar, funnel_intent,
    SV_WEIGHTS, UR_WEIGHTS, FI_WEIGHTS,
)


def test_clamp01_bounds():
    assert clamp01(-1) == 0.0 and clamp01(2) == 1.0 and clamp01(0.4) == 0.4


def test_weighted_axis_basic():
    # equal weights, two present components
    assert weighted_axis({"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5}) == 0.5


def test_weighted_axis_missing_component_renormalizes():
    # b is None -> weight collapses onto a -> returns a's value
    assert weighted_axis({"a": 0.8, "b": None}, {"a": 0.5, "b": 0.5}) == 0.8


def test_weighted_axis_all_missing_is_none():
    assert weighted_axis({"a": None, "b": None}, {"a": 0.5, "b": 0.5}) is None


def test_under_radar_famous_is_near_zero():
    # famous brand: every "under radar" component ~0 (huge rating/followers/reach, old)
    ur = under_radar({"rating": 0.0, "followers": 0.0, "reach": 0.0, "newness": 0.0})
    assert ur == 0.0


def test_under_radar_gem_is_high():
    ur = under_radar({"rating": 1.0, "followers": 1.0, "reach": 1.0, "newness": 1.0})
    assert ur == 1.0


def test_scaling_velocity_combines_with_weights():
    sv = scaling_velocity({"wow": 1.0, "accel": 1.0, "ewma": 1.0, "longevity": 1.0})
    assert sv == 1.0
    assert set(SV_WEIGHTS) == {"wow", "accel", "ewma", "longevity"}


def test_funnel_intent_weights_present():
    fi = funnel_intent({"fingerprint": 1.0, "cta": 0.0, "monetize": 0.0})
    assert fi == FI_WEIGHTS["fingerprint"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/scoring/test_subscores.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.scoring.gemscore'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/scoring/gemscore.py
"""GemScore axes + multiplicative AND-gate (master-plan §2, §6 P5-T6/T7).

gem_score = 100 · clamp01(SV)^.5 · clamp01(UR)^.35 · clamp01(FI)^.15

Each axis is a weighted sum of cohort-normalized components in [0,1]; missing
components re-normalize the remaining weights. A fully-missing (sparse) axis is
imputed to `floor` in gem_score so a data gap does not kill an otherwise-strong
gem — but a *present, genuinely low* axis (famous brand -> UR≈0) still gates it."""

# axis component weights (sum to 1 within each axis)
SV_WEIGHTS = {"wow": 0.35, "accel": 0.25, "ewma": 0.20, "longevity": 0.20}
UR_WEIGHTS = {"rating": 0.40, "followers": 0.20, "reach": 0.20, "newness": 0.20}
FI_WEIGHTS = {"fingerprint": 0.50, "cta": 0.30, "monetize": 0.20}


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def weighted_axis(components: dict, weights: dict):
    """Weighted sum of present (non-None) components, weights re-normalized over
    the present subset. Returns None if every component is missing."""
    present = {k: v for k, v in components.items() if v is not None}
    total_w = sum(weights[k] for k in present)
    if total_w == 0:
        return None
    return sum(weights[k] * clamp01(present[k]) for k in present) / total_w


def scaling_velocity(components: dict):
    return weighted_axis(components, SV_WEIGHTS)


def under_radar(components: dict):
    return weighted_axis(components, UR_WEIGHTS)


def funnel_intent(components: dict):
    return weighted_axis(components, FI_WEIGHTS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/scoring/test_subscores.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/gemscore.py tests/scoring/test_subscores.py
git commit -m "feat(plan-5): SV/UR/FI sub-scores (weighted axes, missing-component re-normalize)"
```

---

## Task 4 (P5-T7): `gem_score` multiplicative AND-gate + per-axis floor

**Files:**
- Modify: `src/agf/scoring/gemscore.py` (append `gem_score`)
- Test: `tests/scoring/test_gemscore.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_gemscore.py
from agf.scoring.gemscore import gem_score


def test_all_axes_max_is_100():
    assert gem_score(1.0, 1.0, 1.0) == 100.0


def test_incumbent_present_low_axis_gates_to_near_zero():
    # UR genuinely 0 (famous brand) -> AND-gate collapses the score to exactly 0
    assert gem_score(1.0, 0.0, 1.0) == 0.0
    # a low (but present) UR is gated hard relative to a high UR at equal SV/FI
    # (absolute value depends on the frozen .35 exponent; assert the gating ratio)
    assert gem_score(0.9, 0.02, 0.9) < 0.5 * gem_score(0.9, 0.9, 0.9)


def test_sparse_axis_is_imputed_to_floor_not_zero():
    # UR is None (no rating/followers/reach data) -> imputed to floor, gem survives
    s_missing = gem_score(0.9, None, 0.9, floor=0.05)
    s_zero = gem_score(0.9, 0.0, 0.9)
    assert s_zero == 0.0
    assert s_missing > 0.0                      # not killed by a data gap
    # FI has the smallest exponent (.15) -> a missing FI barely dents the score
    assert gem_score(0.9, 0.9, None, floor=0.05) > gem_score(0.9, None, 0.9, floor=0.05)


def test_floor_only_applies_to_missing_axes():
    # a present 0.0 is NOT lifted to the floor
    assert gem_score(1.0, 0.0, 1.0, floor=0.05) == 0.0


def test_monotonic_in_each_axis():
    base = gem_score(0.5, 0.5, 0.5)
    assert gem_score(0.6, 0.5, 0.5) > base
    assert gem_score(0.5, 0.6, 0.5) > base
    assert gem_score(0.5, 0.5, 0.6) > base
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/scoring/test_gemscore.py -q`
Expected: FAIL — `ImportError: cannot import name 'gem_score'`

- [ ] **Step 3: Append the implementation to `gemscore.py`**

```python
# --- append to src/agf/scoring/gemscore.py ---

def gem_score(sv, ur, fi, floor: float = 0.05) -> float:
    """Multiplicative AND-gate: 100 · clamp01(SV)^.5 · clamp01(UR)^.35 · clamp01(FI)^.15.

    None axis (no data at all) -> imputed to `floor` so a sparse gem isn't killed.
    A present axis is clamped to [0,1] as-is: a famous brand whose UR computes to
    ~0 still collapses the product to ~0 (the 'still unknown' hard gate)."""
    a = floor if sv is None else clamp01(sv)
    b = floor if ur is None else clamp01(ur)
    c = floor if fi is None else clamp01(fi)
    return 100.0 * (a ** 0.5) * (b ** 0.35) * (c ** 0.15)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/scoring/test_gemscore.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/gemscore.py tests/scoring/test_gemscore.py
git commit -m "feat(plan-5): gem_score multiplicative AND-gate with per-axis sparse floor"
```

---

## Task 5 (P5-T8): `vetoes.py` — 5 post-score vetoes

Vetoes are pure functions over a `FeatureVector`-like object plus an optional weekly series. `apply_vetoes` runs them in order and returns the first that fires. STL needs ≥2 seasonal periods or it short-circuits to *pass* (no veto).

> The vetoes read a small set of attributes (`weeks_observed`, `max_longevity_days`, `parent_company_id`, `is_emerging`, `rating_count`, `velocity_series`). Task 9's `FeatureVector` provides all of them; the unit test uses a lightweight stand-in with the same attribute names.

**Files:**
- Create: `src/agf/scoring/vetoes.py`
- Test: `tests/scoring/test_vetoes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_vetoes.py
from types import SimpleNamespace
from agf.scoring.vetoes import (
    apply_vetoes, persistence_gate, longevity_floor, disguised_incumbent,
    relaunch, seasonal_spike,
)


def _fv(**kw):
    base = dict(weeks_observed=4, max_longevity_days=30, parent_company_id=None,
               is_emerging=True, rating_count=200,
               velocity_series=(2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12))
    base.update(kw)
    return SimpleNamespace(**base)


def test_clean_gem_passes_all_vetoes():
    res = apply_vetoes(_fv())
    assert res.vetoed is False and res.reason is None


def test_persistence_gate_fires_on_single_week():
    assert persistence_gate(_fv(weeks_observed=1)) is True
    assert apply_vetoes(_fv(weeks_observed=1)).reason == "insufficient_persistence"


def test_longevity_floor_fires_below_14d():
    assert longevity_floor(_fv(max_longevity_days=10)) is True
    assert apply_vetoes(_fv(max_longevity_days=10)).reason == "longevity_below_floor"


def test_disguised_incumbent_fires_when_parent_set_tolerates_null():
    assert disguised_incumbent(_fv(parent_company_id=7)) is True
    assert disguised_incumbent(_fv(parent_company_id=None)) is False


def test_relaunch_fires_on_emerging_with_huge_rating_base():
    assert relaunch(_fv(is_emerging=True, rating_count=500_000)) is True
    assert relaunch(_fv(is_emerging=True, rating_count=200)) is False
    assert relaunch(_fv(is_emerging=False, rating_count=500_000)) is False  # not tagged emerging


def test_seasonal_spike_fires_on_pure_season_passes_on_trend():
    seasonal = [2, 1, 2, 6] * 3            # period-4 pattern, ends on a peak, no trend
    trend = list(range(1, 13))             # strictly rising, last point is the peak
    assert seasonal_spike(_fv(velocity_series=seasonal)) is True
    assert seasonal_spike(_fv(velocity_series=trend)) is False


def test_seasonal_spike_short_series_short_circuits_pass():
    assert seasonal_spike(_fv(velocity_series=(1, 5, 1))) is False   # < 2 periods -> pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/scoring/test_vetoes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.scoring.vetoes'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/scoring/vetoes.py
"""Post-score vetoes (master-plan §6 P5-T8). Pure functions; each returns True
when it should KILL the gem. apply_vetoes returns the first that fires."""
from dataclasses import dataclass
import numpy as np
from statsmodels.tsa.seasonal import STL

MIN_WEEKS = 2            # persistence: a 1-week blip is not a trend
LONGEVITY_FLOOR = 14     # days; mirrors the discovery 14-day scaling gate
RELAUNCH_RATING_MAX = 100_000  # "emerging" + this many ratings == a relaunch, not new
_STL_PERIOD = 4          # weekly series, ~monthly seasonal cycle
_REAL_GROWTH_EPS = 1e-6


@dataclass(frozen=True)
class VetoResult:
    vetoed: bool
    reason: str | None


def persistence_gate(fv) -> bool:
    return (fv.weeks_observed or 0) < MIN_WEEKS


def longevity_floor(fv) -> bool:
    return (fv.max_longevity_days or 0) < LONGEVITY_FLOOR


def disguised_incumbent(fv) -> bool:
    # owned by a parent (self-FK) -> not an independent gem. NULL tolerated (passes).
    return fv.parent_company_id is not None


def relaunch(fv) -> bool:
    # tagged emerging by release date but sitting on a huge rating base == rebrand/relaunch
    return bool(fv.is_emerging) and (fv.rating_count or 0) > RELAUNCH_RATING_MAX


def seasonal_spike(fv) -> bool:
    """STL-deseasonalize the velocity series; veto if the latest spike disappears
    once seasonality is removed. <2 seasonal periods -> short-circuit pass."""
    s = np.asarray(list(fv.velocity_series or ()), dtype=float)
    if s.size < 2 * _STL_PERIOD:
        return False
    seasonal = STL(s, period=_STL_PERIOD, robust=True).fit().seasonal
    deseasonalized = s - seasonal
    is_spike = s[-1] > float(np.mean(s[:-1]))
    real_growth = deseasonalized[-1] - float(np.mean(deseasonalized[:-1]))
    return bool(is_spike and real_growth <= _REAL_GROWTH_EPS)


_VETOES = [
    (persistence_gate, "insufficient_persistence"),
    (longevity_floor, "longevity_below_floor"),
    (disguised_incumbent, "disguised_incumbent"),
    (relaunch, "relaunch"),
    (seasonal_spike, "seasonal_spike"),
]


def apply_vetoes(fv) -> VetoResult:
    for fn, reason in _VETOES:
        if fn(fv):
            return VetoResult(True, reason)
    return VetoResult(False, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/scoring/test_vetoes.py -q`
Expected: PASS (7 passed)

> If `seasonal_spike` booleans differ on the synthetic fixtures (STL numerics), keep the *rule* and nudge the fixtures (a flatter seasonal pattern / a steeper trend) until the documented booleans hold — these two cases are qualitatively unambiguous. This is real TDD: run it, observe, adjust the fixture, not the threshold.

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/vetoes.py tests/scoring/test_vetoes.py
git commit -m "feat(plan-5): 5 vetoes — persistence, longevity-floor, disguised-incumbent, relaunch, STL seasonal-spike"
```

---

## Task 6 (P5-T9): schema `050` + `051` + matview shape test

`adtech.advertiser_scores` holds one scored row per advertiser; the matview joins it to `advertisers`/`apps` for the display columns and exposes exactly the §3.6 frozen columns with a UNIQUE index on `advertiser_id`.

**Files:**
- Create: `db/schema/050_scoring.sql`, `db/schema/051_emerging_matview.sql`
- Test: `tests/scoring/test_schema_matview.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_schema_matview.py
from sqlalchemy import text

FROZEN = {
    "advertiser_id", "cohort", "page_name", "app_id", "app_name", "gem_score",
    "scaling_velocity", "under_radar", "funnel_intent", "active_ad_count",
    "max_longevity_days", "alert", "refreshed_at",
}


def test_advertiser_scores_table_exists(pg_conn):
    cols = set(pg_conn.execute(
        text("SELECT * FROM adtech.advertiser_scores LIMIT 0")).keys())
    assert {"advertiser_id", "cohort", "gem_score", "alert", "vetoed",
            "veto_reason"} <= cols


def test_matview_exposes_frozen_columns(pg_conn):
    cols = set(pg_conn.execute(
        text("SELECT * FROM frontend.emerging_scaling_apps LIMIT 0")).keys())
    assert FROZEN <= cols, FROZEN - cols


def test_matview_has_unique_index_on_advertiser_id(pg_conn):
    defs = pg_conn.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='frontend' "
        "AND tablename='emerging_scaling_apps'")).scalars().all()
    assert any("UNIQUE" in d.upper() and "advertiser_id" in d for d in defs), defs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f db/docker-compose.yml up -d && export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf && .venv/bin/pytest tests/scoring/test_schema_matview.py -q`
Expected: FAIL — relation `adtech.advertiser_scores` / `frontend.emerging_scaling_apps` does not exist

- [ ] **Step 3: Write the schema files**

```sql
-- db/schema/050_scoring.sql
-- Plan 5 — per-advertiser scores (schema `adtech`). Feeds the product matview (051).
CREATE TABLE IF NOT EXISTS adtech.advertiser_scores (
    advertiser_id      INTEGER PRIMARY KEY REFERENCES public.advertisers(id),
    cohort             TEXT NOT NULL,
    gem_score          REAL NOT NULL,
    scaling_velocity   REAL,
    under_radar        REAL,
    funnel_intent      REAL,
    active_ad_count    INTEGER,
    max_longevity_days INTEGER,
    alert              BOOLEAN NOT NULL DEFAULT FALSE,
    vetoed             BOOLEAN NOT NULL DEFAULT FALSE,
    veto_reason        TEXT,
    scored_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
```

```sql
-- db/schema/051_emerging_matview.sql
-- Plan 5 OWNS this matview alone (master-plan §3.6). Columns are FROZEN; Plan 7
-- builds against them. One row per advertiser (web funnels keep app_id NULL).
-- The UNIQUE index on advertiser_id is required for REFRESH ... CONCURRENTLY.
DROP MATERIALIZED VIEW IF EXISTS frontend.emerging_scaling_apps;
CREATE MATERIALIZED VIEW frontend.emerging_scaling_apps AS
SELECT
    s.advertiser_id,
    s.cohort,
    adv.page_name,
    app.app_id,
    app.app_name,
    s.gem_score,
    s.scaling_velocity,
    s.under_radar,
    s.funnel_intent,
    s.active_ad_count,
    s.max_longevity_days,
    s.alert,
    clock_timestamp() AS refreshed_at
FROM adtech.advertiser_scores s
JOIN public.advertisers adv ON adv.id = s.advertiser_id
LEFT JOIN LATERAL (
    SELECT a.app_id AS app_id, ap.name AS app_name
    FROM public.ads a
    JOIN public.apps ap ON ap.id = a.app_id
    WHERE a.advertiser_id = s.advertiser_id AND a.app_id IS NOT NULL
    ORDER BY a.last_seen DESC NULLS LAST
    LIMIT 1
) app ON TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_emerging_advertiser
    ON frontend.emerging_scaling_apps (advertiser_id);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/scoring/test_schema_matview.py -q`
Expected: PASS (3 passed). `apply_migrations` (session fixture) applies `050`/`051` once.

> Note: matview columns are not in `information_schema`; the test reads them via `SELECT … LIMIT 0` keys and the index via `pg_indexes`. CONCURRENTLY refresh is exercised live in Task 10 (it cannot run inside the rolled-back `pg_conn` txn).

- [ ] **Step 5: Commit**

```bash
git add db/schema/050_scoring.sql db/schema/051_emerging_matview.sql tests/scoring/test_schema_matview.py
git commit -m "feat(plan-5): advertiser_scores table + emerging_scaling_apps matview (frozen cols, UNIQUE index)"
```

---

## Task 7 (P5-T1): `snapshot_ad_metrics`

Daily snapshot: for every **active** ad in the spine, call `extractor.ad_details(external_ad_id)` and record `(eu_total_reach, active)` into `adtech.ad_metrics`. Idempotent on the `(ad_id, snapshot_date)` PK.

**Files:**
- Create: `src/agf/tracker/snapshots.py`
- Create: `tests/tracker/conftest.py` (a fake extractor that implements `ad_details`)
- Test: `tests/tracker/test_snapshot_ad_metrics.py`

- [ ] **Step 1: Write the fake extractor + the failing test**

```python
# tests/tracker/conftest.py
from agf.extract.ad_details import AdDetails


class FakeDetailsExtractor:
    """Async stand-in: maps external_ad_id -> AdDetails (master-plan §3.4)."""
    def __init__(self, details: dict):
        self._details = details            # {ad_archive_id(str): AdDetails}
        self.calls = []

    async def ad_details(self, ad_archive_id):
        self.calls.append(str(ad_archive_id))
        return self._details.get(str(ad_archive_id), AdDetails(None, False))
```

```python
# tests/tracker/test_snapshot_ad_metrics.py
import asyncio
from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.extract.ad_details import AdDetails
from agf.tracker.snapshots import snapshot_ad_metrics
from tests.tracker.conftest import FakeDetailsExtractor


def _ad(ext, active=True):
    return AdRecord(ext, "P1", "GemCo", active, 1_714_500_000, None, 3,
                    "INSTALL_MOBILE_APP", "Install", "https://x.example", "body")


def test_snapshots_active_ads_only_and_is_idempotent(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad("A1"))
    active_id = repo.upsert_ad(pg_conn, _ad("A1", active=True), adv, None)
    repo.upsert_ad(pg_conn, _ad("A2", active=False), adv, None)   # inactive -> skipped
    ex = FakeDetailsExtractor({"A1": AdDetails(eu_total_reach=4321, active=True)})

    n1 = asyncio.run(snapshot_ad_metrics(pg_conn, ex, "2026-06-04"))
    n2 = asyncio.run(snapshot_ad_metrics(pg_conn, ex, "2026-06-04"))  # re-run same day

    assert n1 == 1 and n2 == 1                       # one active ad, idempotent
    assert ex.calls == ["A1", "A1"]                  # only the active ad queried
    row = pg_conn.execute(text("SELECT eu_total_reach, active FROM adtech.ad_metrics "
                               "WHERE ad_id=:a AND snapshot_date='2026-06-04'"),
                          {"a": active_id}).first()
    assert row.eu_total_reach == 4321 and row.active is True
    cnt = pg_conn.execute(text("SELECT count(*) FROM adtech.ad_metrics WHERE ad_id=:a"),
                          {"a": active_id}).scalar()
    assert cnt == 1                                  # PK conflict updated, not duplicated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/tracker/test_snapshot_ad_metrics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.tracker.snapshots'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agf/tracker/snapshots.py
"""Daily/weekly snapshots into the spine (master-plan §6 P5-T1/T2). conn-first:
never commits — the caller owns the transaction."""
from sqlalchemy import text
from agf.db import repo


async def snapshot_ad_metrics(conn, extractor, snapshot_date) -> int:
    """For each ACTIVE ad, fetch eu_total_reach + active via extractor.ad_details
    (§3.4) and record an ad_metrics row. Idempotent on (ad_id, snapshot_date).
    Returns the number of metrics written."""
    rows = conn.execute(text(
        "SELECT id, external_ad_id FROM public.ads WHERE is_active = TRUE "
        "ORDER BY id")).all()
    n = 0
    for ad_id, external_ad_id in rows:
        details = await extractor.ad_details(external_ad_id)
        repo.record_ad_metric(conn, ad_id, snapshot_date,
                              details.eu_total_reach, details.active)
        n += 1
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/tracker/test_snapshot_ad_metrics.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/tracker/snapshots.py tests/tracker/conftest.py tests/tracker/test_snapshot_ad_metrics.py
git commit -m "feat(plan-5): snapshot_ad_metrics — active ads -> ad_metrics via extractor.ad_details (idempotent)"
```

---

## Task 8 (P5-T2): `snapshot_advertiser_week`

Weekly rollup into `adtech.app_advertiser_metrics`: per advertiser, the count of active ads, distinct creatives, and total EU reach (latest in-week snapshot per ad). This is the DB-native equivalent of Plan 1's `scale.aggregate_advertisers` (same definitions — distinct active ads + reach — computed set-based in SQL rather than by reconstructing `AdRecord`s). Idempotent on `(advertiser_id, week_start)`.

**Files:**
- Modify: `src/agf/tracker/snapshots.py` (append `snapshot_advertiser_week`)
- Test: `tests/tracker/test_snapshot_advertiser_week.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tracker/test_snapshot_advertiser_week.py
from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.tracker.snapshots import snapshot_advertiser_week


def _ad(ext, active=True):
    return AdRecord(ext, "P1", "GemCo", active, 1_714_500_000, None, 2,
                    "INSTALL_MOBILE_APP", "Install", "https://x.example", "body")


def test_weekly_rollup_counts_and_reach_idempotent(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad("A1"))
    a1 = repo.upsert_ad(pg_conn, _ad("A1", active=True), adv, None)
    a2 = repo.upsert_ad(pg_conn, _ad("A2", active=True), adv, None)
    repo.upsert_ad(pg_conn, _ad("A3", active=False), adv, None)        # inactive
    # two creatives on a1, one on a2 -> 3 distinct creatives
    for sha in ("s1", "s2"):
        repo.upsert_creative(pg_conn, a1, "image", "b", "t", None, None, sha, None)
    repo.upsert_creative(pg_conn, a2, "image", "b", "t", None, None, "s3", None)
    # in-week ad_metrics: a1 reach 100, a2 reach 50
    repo.record_ad_metric(pg_conn, a1, "2026-06-02", 100, True)
    repo.record_ad_metric(pg_conn, a2, "2026-06-03", 50, True)

    snapshot_advertiser_week(pg_conn, "2026-06-01")
    snapshot_advertiser_week(pg_conn, "2026-06-01")                    # idempotent

    row = pg_conn.execute(text(
        "SELECT active_ad_count, distinct_creative_count, total_eu_reach "
        "FROM adtech.app_advertiser_metrics WHERE advertiser_id=:a AND week_start='2026-06-01'"),
        {"a": adv}).first()
    assert row.active_ad_count == 2          # a1, a2 (a3 inactive)
    assert row.distinct_creative_count == 3  # s1, s2, s3
    assert row.total_eu_reach == 150         # 100 + 50
    cnt = pg_conn.execute(text("SELECT count(*) FROM adtech.app_advertiser_metrics "
                               "WHERE advertiser_id=:a"), {"a": adv}).scalar()
    assert cnt == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/tracker/test_snapshot_advertiser_week.py -q`
Expected: FAIL — `ImportError: cannot import name 'snapshot_advertiser_week'`

- [ ] **Step 3: Append the implementation**

```python
# --- append to src/agf/tracker/snapshots.py (add imports at top of file) ---
from datetime import timedelta
from agf.db.repo import _as_date   # reuse the date coercion helper


def snapshot_advertiser_week(conn, week_start) -> int:
    """Roll the week [week_start, week_start+7) into app_advertiser_metrics:
    active ad count, distinct creatives, total EU reach (latest in-week snapshot
    per ad). DB-native analogue of scale.aggregate_advertisers. Idempotent on
    (advertiser_id, week_start). Returns rows upserted.

    Reach is aggregated in its own CTE (one row per ad) so the creative LEFT JOIN
    fan-out cannot double-count it."""
    wk = _as_date(week_start)
    wk_end = wk + timedelta(days=7)
    rows = conn.execute(text("""
        WITH active_ads AS (
            SELECT id, advertiser_id FROM public.ads
            WHERE is_active = TRUE AND advertiser_id IS NOT NULL
        ),
        ad_reach AS (   -- latest in-week eu_total_reach per ad
            SELECT DISTINCT ON (m.ad_id) m.ad_id, m.eu_total_reach
            FROM adtech.ad_metrics m
            WHERE m.snapshot_date >= :wk AND m.snapshot_date < :wk_end
            ORDER BY m.ad_id, m.snapshot_date DESC
        ),
        per_adv AS (
            SELECT aa.advertiser_id,
                   count(DISTINCT aa.id) AS active_ad_count,
                   count(DISTINCT c.id)  AS distinct_creative_count
            FROM active_ads aa
            LEFT JOIN public.creatives c ON c.ad_id = aa.id
            GROUP BY aa.advertiser_id
        ),
        reach_adv AS (
            SELECT aa.advertiser_id, COALESCE(sum(ar.eu_total_reach), 0) AS total_eu_reach
            FROM active_ads aa
            LEFT JOIN ad_reach ar ON ar.ad_id = aa.id
            GROUP BY aa.advertiser_id
        )
        SELECT p.advertiser_id, p.active_ad_count, p.distinct_creative_count,
               COALESCE(r.total_eu_reach, 0) AS total_eu_reach
        FROM per_adv p
        LEFT JOIN reach_adv r ON r.advertiser_id = p.advertiser_id
    """), {"wk": wk, "wk_end": wk_end}).all()
    for advertiser_id, active_ad_count, distinct_creative_count, total_eu_reach in rows:
        repo.upsert_app_advertiser_metric(
            conn, advertiser_id, wk, active_ad_count,
            distinct_creative_count, int(total_eu_reach))
    return len(rows)
```

> **Fan-out note (resolved during build):** reach must be summed in its own CTE (`reach_adv`, one row per ad), NOT in the same query as the `public.creatives` LEFT JOIN — otherwise an ad with N creatives counts its reach N times (the test's `total_eu_reach == 150` catches this). The creative count uses `count(DISTINCT c.id)` so its own fan-out doesn't inflate the ad count.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/tracker/test_snapshot_advertiser_week.py -q`
Expected: PASS (1 passed). If reach double-counts due to the creative join, switch the reach aggregate to a correlated scalar subquery per advertiser — verify the asserted `total_eu_reach == 150`.

- [ ] **Step 5: Commit**

```bash
git add src/agf/tracker/snapshots.py tests/tracker/test_snapshot_advertiser_week.py
git commit -m "feat(plan-5): snapshot_advertiser_week — weekly rollup into app_advertiser_metrics (idempotent)"
```

---

## Task 9 (P5-T5): `build_features(conn, advertiser_id, as_of)` → `FeatureVector`

Reads the weekly series + current ad/app/advertiser facts and produces the per-advertiser `FeatureVector` consumed by scoring and vetoes.

**Files:**
- Modify: `src/agf/scoring/features.py` (append `FeatureVector` + `build_features`)
- Test: `tests/scoring/test_build_features.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scoring/test_build_features.py
from datetime import date
from agf.parse import AdRecord
from agf.db import repo
from agf.scoring.features import build_features

NOW = 1_749_000_000   # ~2026-06-04 (matches tests.discovery.conftest.NOW_EPOCH)
DAY = 86_400


def _ad(ext, *, days_old, active=True):
    return AdRecord(ext, "PF", "FeatCo", active, NOW - days_old * DAY, None, 4,
                    "INSTALL_MOBILE_APP", "Install",
                    "https://apps.apple.com/us/app/x/id999", "body")


def test_build_features_accel_and_longevity_share(pg_conn):
    adv = repo.upsert_advertiser(pg_conn, _ad("F1", days_old=20))
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "999",
                                    "name": "Feet", "genre": "Health & Fitness",
                                    "rating_count": 120, "release_date": "2026-05-20",
                                    "is_emerging": True})
    # two active ads: one 20d old (>=14), one 5d old (<14) -> longevity_share 0.5
    repo.upsert_ad(pg_conn, _ad("F1", days_old=20), adv, app)
    repo.upsert_ad(pg_conn, _ad("F2", days_old=5), adv, app)
    # 4-week active_ad_count series 1,2,4,8 -> accelerating
    for wk, n in zip(("2026-05-11", "2026-05-18", "2026-05-25", "2026-06-01"), (1, 2, 4, 8)):
        repo.upsert_app_advertiser_metric(pg_conn, adv, wk, n, n, n * 10)

    fv = build_features(pg_conn, adv, date(2026, 6, 4))

    assert fv.advertiser_id == adv
    assert fv.cohort == "Health & Fitness×US"
    assert fv.weeks_observed == 4
    assert fv.accel > 0                         # 1,2,4,8 is convex in log space
    assert fv.max_longevity_days >= 20
    assert fv.longevity_share == 0.5            # 1 of 2 active ads >= 14d
    assert fv.rating_count == 120
    assert fv.is_emerging is True
    assert fv.install_cta is True
    assert fv.velocity_series == (1, 2, 4, 8)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/scoring/test_build_features.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_features'`

- [ ] **Step 3: Append `FeatureVector` + `build_features` to `features.py`**

```python
# --- append to src/agf/scoring/features.py ---
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import text
from agf.scale import longevity_days
from agf.scoring.cohorts import cohort_key

_MONETIZE_CTAS = {"INSTALL_MOBILE_APP", "SHOP_NOW", "SUBSCRIBE", "SIGN_UP",
                  "GET_OFFER", "ORDER_NOW", "DOWNLOAD", "GET_QUOTE"}
LONGEVITY_FLOOR_DAYS = 14


@dataclass(frozen=True)
class FeatureVector:
    advertiser_id: int
    cohort: str
    page_name: str | None
    app_id: int | None
    app_name: str | None
    # scaling-velocity inputs
    wow_log_growth: float
    accel: float
    ewma_velocity: float
    max_longevity_days: int
    longevity_share: float
    active_ad_count: int
    collation_total: int
    velocity_series: tuple
    # under-radar inputs
    rating_count: int | None
    ig_followers: int | None
    release_age_days: int | None
    total_eu_reach: int | None
    # funnel-intent inputs
    has_funnel: bool
    install_cta: bool
    has_app: bool
    # veto inputs
    is_emerging: bool
    parent_company_id: int | None
    weeks_observed: int


def _as_of_epoch(as_of) -> int:
    return int(datetime(as_of.year, as_of.month, as_of.day,
                        tzinfo=timezone.utc).timestamp())


def build_features(conn, advertiser_id: int, as_of) -> FeatureVector:
    """Assemble the per-advertiser FeatureVector as of `as_of` (a date)."""
    now_epoch = _as_of_epoch(as_of)

    series = [int(n) for (n,) in conn.execute(text(
        "SELECT active_ad_count FROM adtech.app_advertiser_metrics "
        "WHERE advertiser_id=:a AND week_start <= :d ORDER BY week_start"),
        {"a": advertiser_id, "d": as_of}).all()]
    reach_latest = conn.execute(text(
        "SELECT total_eu_reach FROM adtech.app_advertiser_metrics "
        "WHERE advertiser_id=:a AND week_start <= :d ORDER BY week_start DESC LIMIT 1"),
        {"a": advertiser_id, "d": as_of}).scalar()

    ad_rows = conn.execute(text(
        "SELECT is_active, cta_type, collation_count, app_id, "
        "       EXTRACT(EPOCH FROM start_date)::bigint AS start_epoch "
        "FROM public.ads WHERE advertiser_id=:a"), {"a": advertiser_id}).all()
    active = [r for r in ad_rows if r.is_active]
    longs = [longevity_days(r.start_epoch, now_epoch) for r in active if r.start_epoch]
    max_long = max(longs) if longs else 0
    long_share = (sum(1 for d in longs if d >= LONGEVITY_FLOOR_DAYS) / len(longs)
                  if longs else 0.0)
    collation_total = sum(int(r.collation_count or 0) for r in active)
    install_cta = any((r.cta_type or "") in _MONETIZE_CTAS for r in active)
    has_app = any(r.app_id is not None for r in ad_rows)

    adv = conn.execute(text(
        "SELECT page_name, parent_company_id, ig_followers FROM public.advertisers "
        "WHERE id=:a"), {"a": advertiser_id}).first()

    app = conn.execute(text("""
        SELECT ap.id, ap.name, ap.genre, ap.rating_count, ap.release_date, ap.is_emerging
        FROM public.ads a JOIN public.apps ap ON ap.id = a.app_id
        WHERE a.advertiser_id=:a AND a.app_id IS NOT NULL
        ORDER BY a.last_seen DESC NULLS LAST LIMIT 1"""), {"a": advertiser_id}).first()

    release_age = None
    if app and app.release_date:
        release_age = (as_of - app.release_date).days

    has_funnel = conn.execute(text(
        "SELECT count(*) FROM public.funnels f JOIN public.ads a ON a.id=f.ad_id "
        "WHERE a.advertiser_id=:a"), {"a": advertiser_id}).scalar() > 0

    return FeatureVector(
        advertiser_id=advertiser_id,
        cohort=cohort_key(app.genre if app else None, "US"),
        page_name=adv.page_name if adv else None,
        app_id=app.id if app else None,
        app_name=app.name if app else None,
        wow_log_growth=log_growth_wow(series),
        accel=acceleration(series),
        ewma_velocity=ewma(series),
        max_longevity_days=max_long,
        longevity_share=long_share,
        active_ad_count=len(active),
        collation_total=collation_total,
        velocity_series=tuple(series),
        rating_count=app.rating_count if app else None,
        ig_followers=adv.ig_followers if adv else None,
        release_age_days=release_age,
        total_eu_reach=int(reach_latest) if reach_latest is not None else None,
        has_funnel=has_funnel,
        install_cta=install_cta,
        has_app=has_app,
        is_emerging=bool(app.is_emerging) if app else False,
        parent_company_id=adv.parent_company_id if adv else None,
        weeks_observed=len(series),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/scoring/test_build_features.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/features.py tests/scoring/test_build_features.py
git commit -m "feat(plan-5): build_features — per-advertiser FeatureVector from spine (series + ad/app/advertiser facts)"
```

---

## Task 10 (P5-T10): `run_scoring.score_all` + cohort normalization + refresh + LIVE e2e

`score_all` builds features for every advertiser with ≥1 ad, normalizes each axis **within its cohort** (pctile for "higher = better", `1 - pctile` for "higher = more known"), computes sub-scores → `gem_score`, applies vetoes, and writes `adtech.advertiser_scores` with `alert = gem_score ≥ 70 AND not vetoed`. `refresh_emerging_matview` does the `REFRESH … CONCURRENTLY` on its own autocommit connection.

**Files:**
- Create: `src/agf/scoring/run_scoring.py`
- Test: `tests/scoring/test_run_scoring_e2e.py`

- [ ] **Step 1: Write the failing live e2e test**

```python
# tests/scoring/test_run_scoring_e2e.py
"""Live e2e against real Postgres. Uses _migrated_engine directly (not pg_conn)
because REFRESH ... CONCURRENTLY needs committed rows + an autocommit connection,
which the rolled-back pg_conn fixture cannot provide. Cleans up its sentinels."""
from datetime import date, datetime, timezone
from sqlalchemy import text
from agf.db.engine import begin
from agf.scoring.run_scoring import score_all, refresh_emerging_matview

AS_OF = date(2026, 6, 4)
SENTINELS = ("P5GEM", "P5BRAND", "P5SPIKE")


def _epoch(days_old):
    return int(datetime(2026, 6, 4, tzinfo=timezone.utc).timestamp()) - days_old * 86_400


def _seed(conn):
    from agf.parse import AdRecord
    from agf.db import repo
    out = {}
    specs = {
        # page_id: (genre, rating_count, release_date, longevity_days, weekly series, parent)
        "P5GEM":   ("Health & Fitness", 150,     "2026-05-20", 20,
                    [1, 2, 4, 8], None),
        "P5BRAND": ("Health & Fitness", 5_000_000, "2019-01-01", 400,
                    [40, 41, 42, 43], None),
        "P5SPIKE": ("Health & Fitness", 300,     "2026-05-01", 30,
                    [2, 1, 2, 6] * 3, None),
    }
    for pid, (genre, ratings, rel, longd, series, parent) in specs.items():
        ar = AdRecord(f"{pid}-AD", pid, pid, True, _epoch(longd), None, 5,
                      "INSTALL_MOBILE_APP", "Install",
                      f"https://apps.apple.com/us/app/x/id{pid}", "body")
        adv = repo.upsert_advertiser(conn, ar)
        app = repo.upsert_app(conn, {"store": "ios", "store_id": pid, "name": pid,
                                     "genre": genre, "rating_count": ratings,
                                     "release_date": rel, "is_emerging": True})
        repo.upsert_ad(conn, ar, adv, app)
        for i, n in enumerate(series):
            wk = date(2026, 3, 2)  # any Monday; spacing only needs to be weekly-ordered
            wk = wk.replace(day=2) if False else wk
            from datetime import timedelta
            repo.upsert_app_advertiser_metric(conn, adv, date(2026, 3, 2) + timedelta(weeks=i),
                                              n, n, n * 10)
        out[pid] = adv
    return out


def _cleanup(engine):
    with begin(engine) as conn:
        ids = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"),
                           {"p": list(SENTINELS)}).scalars().all()
        if ids:
            conn.execute(text("DELETE FROM adtech.advertiser_scores WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM adtech.app_advertiser_metrics WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM adtech.ad_metrics m USING public.ads a "
                              "WHERE m.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.creatives c USING public.ads a "
                              "WHERE c.ad_id=a.id AND a.advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.ads WHERE advertiser_id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})
        conn.execute(text("DELETE FROM public.apps WHERE store_id = ANY(:p)"), {"p": list(SENTINELS)})
    refresh_emerging_matview(engine)


def test_score_all_flags_gem_not_brand_not_spike(_migrated_engine):
    eng = _migrated_engine
    _cleanup(eng)
    try:
        with begin(eng) as conn:
            ids = _seed(conn)
            score_all(conn, AS_OF)
        refresh_emerging_matview(eng)
        with eng.connect() as conn:
            rows = {r.advertiser_id: r for r in conn.execute(text(
                "SELECT advertiser_id, gem_score, alert, cohort, app_name "
                "FROM frontend.emerging_scaling_apps WHERE advertiser_id = ANY(:i)"),
                {"i": list(ids.values())}).all()}
            scores = {r.advertiser_id: r for r in conn.execute(text(
                "SELECT advertiser_id, vetoed, veto_reason, gem_score "
                "FROM adtech.advertiser_scores WHERE advertiser_id = ANY(:i)"),
                {"i": list(ids.values())}).all()}
        gem, brand, spike = ids["P5GEM"], ids["P5BRAND"], ids["P5SPIKE"]
        assert rows[gem].alert is True                      # the gem alerts
        assert rows[brand].alert is False                   # famous brand: UR~0 -> low
        assert rows[gem].gem_score > rows[brand].gem_score
        assert scores[spike].vetoed is True                 # seasonal spike vetoed
        assert scores[spike].veto_reason == "seasonal_spike"
        assert rows[spike].alert is False
        assert rows[gem].cohort == "Health & Fitness×US"
    finally:
        _cleanup(eng)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/scoring/test_run_scoring_e2e.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agf.scoring.run_scoring'`

- [ ] **Step 3: Write the implementation**

```python
# src/agf/scoring/run_scoring.py
"""Score every advertiser, write adtech.advertiser_scores, refresh the product
matview. score_all is conn-first (no commit); refresh uses its own autocommit
connection (REFRESH ... CONCURRENTLY cannot run in a transaction block)."""
from collections import defaultdict
from sqlalchemy import text
from agf.scoring.cohorts import pctile
from agf.scoring.features import build_features
from agf.scoring.gemscore import (scaling_velocity, under_radar, funnel_intent, gem_score)
from agf.scoring.vetoes import apply_vetoes

ALERT_THRESHOLD = 70.0           # master-plan §2: GemScore >= 70 -> review queue


def _eligible_advertiser_ids(conn):
    return [a for (a,) in conn.execute(text(
        "SELECT DISTINCT advertiser_id FROM public.ads "
        "WHERE advertiser_id IS NOT NULL ORDER BY advertiser_id")).all()]


def _values(members, attr):
    return [getattr(m, attr) for m in members if getattr(m, attr) is not None]


def _sv_components(fv, members):
    return {
        "wow": pctile(fv.wow_log_growth, _values(members, "wow_log_growth")),
        "accel": pctile(fv.accel, _values(members, "accel")),
        "ewma": pctile(fv.ewma_velocity, _values(members, "ewma_velocity")),
        "longevity": pctile(fv.max_longevity_days, _values(members, "max_longevity_days")),
    }


def _ur_components(fv, members):
    # higher raw (more ratings/followers/reach, older) => MORE known => LOWER UR
    def inv(attr, val):
        vals = _values(members, attr)
        return None if val is None else 1.0 - pctile(val, vals)
    return {
        "rating": inv("rating_count", fv.rating_count),
        "followers": inv("ig_followers", fv.ig_followers),
        "reach": inv("total_eu_reach", fv.total_eu_reach),
        "newness": inv("release_age_days", fv.release_age_days),
    }


def _fi_components(fv):
    return {
        "fingerprint": 1.0 if fv.has_funnel else 0.0,
        "cta": 1.0 if fv.install_cta else 0.0,
        "monetize": 1.0 if (fv.has_app or fv.has_funnel) else 0.0,
    }


def score_all(conn, as_of) -> int:
    """Build features, normalize within cohort, score, veto, write advertiser_scores.
    conn-first: no commit. Returns the number of advertisers scored."""
    fvs = [build_features(conn, aid, as_of) for aid in _eligible_advertiser_ids(conn)]
    by_cohort = defaultdict(list)
    for fv in fvs:
        by_cohort[fv.cohort].append(fv)

    n = 0
    for cohort, members in by_cohort.items():
        for fv in members:
            sv = scaling_velocity(_sv_components(fv, members))
            ur = under_radar(_ur_components(fv, members))
            fi = funnel_intent(_fi_components(fv))
            gs = gem_score(sv, ur, fi)
            veto = apply_vetoes(fv)
            alert = (gs >= ALERT_THRESHOLD) and not veto.vetoed
            conn.execute(text("""
                INSERT INTO adtech.advertiser_scores
                  (advertiser_id, cohort, gem_score, scaling_velocity, under_radar,
                   funnel_intent, active_ad_count, max_longevity_days, alert,
                   vetoed, veto_reason, scored_at)
                VALUES (:adv, :coh, :gs, :sv, :ur, :fi, :aac, :mld, :alert,
                        :vetoed, :reason, clock_timestamp())
                ON CONFLICT (advertiser_id) DO UPDATE SET
                  cohort=EXCLUDED.cohort, gem_score=EXCLUDED.gem_score,
                  scaling_velocity=EXCLUDED.scaling_velocity, under_radar=EXCLUDED.under_radar,
                  funnel_intent=EXCLUDED.funnel_intent, active_ad_count=EXCLUDED.active_ad_count,
                  max_longevity_days=EXCLUDED.max_longevity_days, alert=EXCLUDED.alert,
                  vetoed=EXCLUDED.vetoed, veto_reason=EXCLUDED.veto_reason,
                  scored_at=clock_timestamp()
            """), {"adv": fv.advertiser_id, "coh": cohort, "gs": gs, "sv": sv,
                   "ur": ur, "fi": fi, "aac": fv.active_ad_count,
                   "mld": fv.max_longevity_days, "alert": alert,
                   "vetoed": veto.vetoed, "reason": veto.reason})
            n += 1
    return n


def refresh_emerging_matview(engine) -> None:
    """REFRESH ... CONCURRENTLY on a dedicated autocommit connection (master-plan
    hard rule: cannot run inside a transaction block; needs the UNIQUE index)."""
    raw = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        raw.exec_driver_sql(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY frontend.emerging_scaling_apps")
    finally:
        raw.close()


def run_scoring(engine, as_of) -> int:
    """Convenience orchestrator: score in one committed txn, then refresh."""
    from agf.db.engine import begin
    with begin(engine) as conn:
        n = score_all(conn, as_of)
    refresh_emerging_matview(engine)
    return n
```

- [ ] **Step 4: Run the live e2e to verify it passes**

Run: `.venv/bin/pytest tests/scoring/test_run_scoring_e2e.py -q`
Expected: PASS (1 passed). If the GEM doesn't clear 70, inspect the three advertisers' `gem_score`/`scaling_velocity`/`under_radar` in `adtech.advertiser_scores` — with only 3 cohort members the pctiles are coarse; confirm GEM is top on SV (fastest series) and top on UR (lowest ratings) so `100·SV^.5·UR^.35·FI^.15 ≥ 70`. Adjust the seed *spread* (make BRAND's ratings/series clearly dominate the "known/slow" end), not the formula.

- [ ] **Step 5: Commit**

```bash
git add src/agf/scoring/run_scoring.py tests/scoring/test_run_scoring_e2e.py
git commit -m "feat(plan-5): score_all (cohort-normalized SV/UR/FI -> gem_score + vetoes) + CONCURRENTLY matview refresh"
```

---

## Task 11: Full-suite verification + live snapshot/refresh smoke + docs

- [ ] **Step 1: Run the whole suite with infra up**

```bash
docker compose -f db/docker-compose.yml up -d
export AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf
.venv/bin/pytest -q
```
Expected: all prior tests still green + the new tracker/scoring tests; integration deselected. Record the pass count.

- [ ] **Step 2: Live "assembled thing" smoke (real Postgres, the RUN-IT-LIVE rule)**

Drive the real pieces end-to-end against the docker Postgres in a throwaway script: migrate → seed a gem/brand/spike via `repo` → `score_all` in a `begin()` txn → `refresh_emerging_matview(engine)` → `SELECT * FROM frontend.emerging_scaling_apps` and print rows → confirm exactly the gem has `alert=true`, then a SECOND `refresh_emerging_matview` to prove `CONCURRENTLY` works on the already-populated matview → clean up sentinels. This is the Plan-4 lesson: fakes + contract-adherence passed while a real bug hid; only the assembled live run surfaces it.

Expected: two consecutive `REFRESH … CONCURRENTLY` succeed (no "cannot refresh concurrently" / "cannot run inside a transaction block"); the matview shows the gem alerting and the brand/spike not.

- [ ] **Step 3: `/self-eval` then update the dashboards**

Update `docs/plans/00-master-plan.md` §5: Plan 5 → `✅ done`, tick P5-T1…T10 boxes in §6, and set Plan 6 to `⬜ ready (deps met)`. Update `docs/HANDOFF.md` §1 (Plan 5 ✅, Plan 6 NEXT), §6, and the test count.

```bash
git add docs/plans/00-master-plan.md docs/HANDOFF.md
git commit -m "docs(plan-5): mark Plan 5 done, unblock Plan 6, refresh handoff + dashboard"
```

- [ ] **Step 4: Merge the branch**

```bash
docker compose -f db/docker-compose.yml down
git checkout main
git merge --no-ff feat/plan-5-scoring -m "merge(plan-5): velocity tracker + GemScore + emerging matview"
```

---

## Self-review (run against master-plan §6 Plan 5 + §3)

- **P5-T1 snapshot_ad_metrics** → Task 7 (active-only, idempotent, calls `extractor.ad_details`). ✓
- **P5-T2 snapshot_advertiser_week** → Task 8 (counts, reach, idempotent; DB analogue of `aggregate_advertisers`). ✓
- **P5-T3 cohorts** → Task 1 (`cohort_key`/`robust_z`(MAD)/`pctile`; degenerate→0; scipy ref). ✓
- **P5-T4 features primitives** → Task 2 (`log_growth_wow`/`acceleration`/`ewma`; flat→0). ✓
- **P5-T5 build_features** → Task 9 (4-week fixture → accel + longevity_share). ✓
- **P5-T6 sub-scores** → Task 3 (weighted axes; famous→UR≈0; missing re-normalizes). ✓
- **P5-T7 gem_score** → Task 4 ((1,1,1)→100; incumbent→small; sparse imputed). ✓
- **P5-T8 vetoes** → Task 5 (STL deseasonalize, longevity-floor, disguised-incumbent via parent_company_id, relaunch, persistence; clean gem passes). ✓
- **P5-T9 050/051 SQL** → Task 6 (§3.6 columns, UNIQUE index, `DROP … IF EXISTS`; CONCURRENTLY proven in Task 10). ✓
- **P5-T10 score_all** → Task 10 (features→score→vetoes→write→refresh; gem alert=true, brand low, spike vetoed). ✓
- **Cohort = category × country, normalize within cohort** → `cohort_key` + `_sv/_ur_components` pctile over cohort members. ✓
- **GemScore = 100·clamp01(SV)^.5·clamp01(UR)^.35·clamp01(FI)^.15, multiplicative AND-gate + per-axis floor** → `gem_score`. ✓
- **Sole owner of `frontend.emerging_scaling_apps`, UNIQUE index for CONCURRENTLY** → `051`. ✓
- **numpy/statsmodels/scipy pinned under `# --- plan-5 ---`** → Task 0. ✓
- **conn-first/no-commit; `clock_timestamp()`; autocommit refresh** → Tasks 9/10. ✓
- **RUN IT LIVE** → Tasks 10 (e2e on real PG) + 11 (assembled smoke). ✓
- **Back-test note (master-plan §6):** weights/thresholds (SV/UR/FI weights, floor=.05, ALERT=70, RELAUNCH_RATING_MAX, STL period) are **starter priors**; flagged here for a future back-test against known past breakouts before the score is trusted in production. Not blocking Plan 5's mechanics.
```
