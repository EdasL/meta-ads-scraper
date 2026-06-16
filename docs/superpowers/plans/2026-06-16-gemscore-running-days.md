# GemScore v3 — running-days over ad-volume — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-tilt GemScore so ad running-days + installs + reviews drive ~70% of the score, and a falling ad count (winner consolidation) can never lower a score.

**Architecture:** Keep the 5-axis multiplicative gate. Rebuild the SV ("scaling velocity") axis internals to be running-days driven (`winner_age` = oldest live ad, `winner_depth` = count of live ads ≥21 days), demote ad-count growth to a small component that is **clamped at 0** (drops never punish), and bump the Demand axis exponent (installs+reviews) up. Two new feature fields; no DB migration.

**Tech Stack:** Python, SQLAlchemy core, numpy, pytest (DB-backed tests use the `pg_conn` fixture).

**Spec:** `docs/superpowers/specs/2026-06-16-gemscore-running-days-design.md`

---

## Reference: current vs target

Current axis gate (`src/agf/scoring/gemscore.py:74`):
```python
return 100.0 * (a ** 0.40) * (b ** 0.25) * (c ** 0.05) * (e ** 0.20) * (f ** 0.10)
#                sv          ur            fi            d            w
```
Target:
```python
return 100.0 * (a ** 0.42) * (b ** 0.12) * (c ** 0.03) * (e ** 0.38) * (f ** 0.05)
```
(0.42 + 0.12 + 0.03 + 0.38 + 0.05 = 1.00)

Current `SV_WEIGHTS` (`gemscore.py:15`): `{"wow":.15,"ramp":.30,"accel":.10,"ewma":.10,"longevity":.15,"iteration":.20}`
Target `SV_WEIGHTS`: `{"winner_age":.40,"winner_depth":.30,"count_growth":.15,"iteration":.15}` (sums to 1.0)

**Decision (deviation from spec's "drop unused features" line):** we do NOT delete the existing
`wow_log_growth / window_growth / accel / ewma_velocity / longevity_share` computations from
`build_features`. `window_growth` is reused to derive `count_growth`; the others are cheap, already
unit-tested, and may be read elsewhere. We simply stop referencing them in `_sv_components`. This keeps
the change minimal and avoids breaking `test_build_features_accel_and_longevity_share`.

---

## Task 1: New axis exponents + SV weights (pure functions)

**Files:**
- Modify: `src/agf/scoring/gemscore.py:15` (`SV_WEIGHTS`), `gemscore.py:74` (exponents), docstrings at `:1` and `:60`
- Test: `tests/scoring/test_subscores.py`, `tests/scoring/test_gemscore.py`

- [ ] **Step 1: Update the SV-weights test to the new keys**

In `tests/scoring/test_subscores.py`, replace `test_scaling_velocity_combines_with_weights` (lines 36-41) with:

```python
def test_scaling_velocity_combines_with_weights():
    sv = scaling_velocity({"winner_age": 1.0, "winner_depth": 1.0,
                           "count_growth": 1.0, "iteration": 1.0})
    assert sv == 1.0
    assert set(SV_WEIGHTS) == {"winner_age", "winner_depth", "count_growth", "iteration"}
    assert abs(sum(SV_WEIGHTS.values()) - 1.0) < 1e-9
    # winner signal (age + depth) is the majority of the axis
    assert SV_WEIGHTS["winner_age"] + SV_WEIGHTS["winner_depth"] == 0.70
```

- [ ] **Step 2: Update the gem_score exponent tests**

In `tests/scoring/test_gemscore.py`:

Replace `test_missing_demand_and_whitespace_floor_to_neutral` (lines 8-10) with:
```python
def test_missing_demand_and_whitespace_floor_to_neutral():
    # three-axis callers (no D/W data yet) land at exactly 100 * 0.5^(0.38+0.05)
    assert abs(gem_score(1.0, 1.0, 1.0) - 100 * 0.5 ** 0.43) < 1e-9
```

Replace the second assertion block of `test_incumbent_present_low_axis_gates_to_near_zero`
(lines 17-19) — UR's exponent drops .25 → .12, so it is intentionally a lighter brake in v3:
```python
    # a low (but present) UR still drags the score down, just less hard than v2
    low_ur = gem_score(0.9, 0.02, 0.9, 0.9, 0.9)
    high_ur = gem_score(0.9, 0.9, 0.9, 0.9, 0.9)
    assert low_ur < 0.7 * high_ur
```
(Leave the first assertion `gem_score(1.0, 0.0, 1.0, 1.0, 1.0) == 0.0` unchanged.)

- [ ] **Step 3: Run the two tests to verify they FAIL**

Run: `pytest tests/scoring/test_subscores.py::test_scaling_velocity_combines_with_weights tests/scoring/test_gemscore.py::test_missing_demand_and_whitespace_floor_to_neutral -v`
Expected: FAIL — old `SV_WEIGHTS` keys (`wow`/`ramp`/…) and old `0.5**0.3` exponent.

- [ ] **Step 4: Update `SV_WEIGHTS` and the gate exponents**

In `src/agf/scoring/gemscore.py`, replace `SV_WEIGHTS` (lines 11-16, including the stale `ramp`/`iteration` comment) with:
```python
# SV axis = "winner durability" (GemScore v3, spec 2026-06-16). The signal of a real
# winner is RUNNING-DAYS, not ad count: an app that tests 20 ads, finds 2, and kills the
# other 18 should climb, not fall. winner_age = oldest still-live ad; winner_depth = how
# many live ads have run >= PROVEN_WINNER_DAYS. count_growth is a small bonus for rising
# ad count, CLAMPED at 0 so a falling count never subtracts. iteration = still launching.
SV_WEIGHTS = {"winner_age": 0.40, "winner_depth": 0.30,
              "count_growth": 0.15, "iteration": 0.15}
```

In the same file, replace the gate return (line 74) with:
```python
    return 100.0 * (a ** 0.42) * (b ** 0.12) * (c ** 0.03) * (e ** 0.38) * (f ** 0.05)
```

Update the module docstring (line 3) and the `gem_score` docstring (line 60) to read
`100 · SV^.42 · UR^.12 · FI^.03 · D^.38 · W^.05`.

- [ ] **Step 5: Run the full gemscore + subscores suites**

Run: `pytest tests/scoring/test_gemscore.py tests/scoring/test_subscores.py -v`
Expected: PASS (all). `test_all_axes_max_is_100`, `test_present_zero_gates_every_axis`,
and `test_monotonic_in_each_axis` still hold because every exponent is > 0.

- [ ] **Step 6: Commit**

```bash
git add src/agf/scoring/gemscore.py tests/scoring/test_gemscore.py tests/scoring/test_subscores.py
git commit -m "feat(scoring): GemScore v3 axis weights — running-days SV + bigger demand"
```

---

## Task 2: Feature fields `winner_age`/`winner_depth`/`count_growth` (build_features)

`winner_age` already exists as `FeatureVector.max_longevity_days` (max running-days over **live** ads,
`features.py:174-175`). This task adds `proven_winner_count` (count of live ads ≥21d) and `count_growth`
(clamped ramp).

**Files:**
- Modify: `src/agf/scoring/features.py` — add constant, two dataclass fields, two computations
- Test: `tests/scoring/test_build_features.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/scoring/test_build_features.py`:
```python
def test_build_features_winner_depth_and_count_growth_clamp(pg_conn):
    """v3: proven_winner_count = live ads >=21d; count_growth clamps a FALLING count to 0."""
    adv = repo.upsert_advertiser(pg_conn, _ad("F9", days_old=40))
    app = repo.upsert_app(pg_conn, {"store": "ios", "store_id": "999",
                                    "name": "Feet", "genre": "Health & Fitness",
                                    "rating_count": 120, "release_date": "2026-05-20",
                                    "is_emerging": True})
    # three live ads: 40d and 25d are >=21 (proven), 5d is not
    repo.upsert_ad(pg_conn, _ad("F9", days_old=40), adv, app)
    repo.upsert_ad(pg_conn, _ad("F10", days_old=25), adv, app)
    repo.upsert_ad(pg_conn, _ad("F11", days_old=5), adv, app)
    # active_ad_count series 8,4,2 -> count FELL (winner consolidation)
    for wk, n in zip(("2026-05-11", "2026-05-18", "2026-05-25", "2026-06-01"), (8, 8, 4, 2)):
        repo.upsert_app_advertiser_metric(pg_conn, adv, wk, n, n, n * 10)

    fv = build_features(pg_conn, adv, AS_OF)

    assert fv.proven_winner_count == 2          # 40d + 25d ads, the 5d ad excluded
    assert fv.max_longevity_days >= 40          # winner_age = oldest live ad
    assert fv.window_growth < 0                 # raw ramp is negative (8 -> 2)
    assert fv.count_growth == 0.0               # clamped: a falling count never subtracts
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/scoring/test_build_features.py::test_build_features_winner_depth_and_count_growth_clamp -v`
Expected: FAIL — `FeatureVector` has no attribute `proven_winner_count` / `count_growth`.

- [ ] **Step 3: Add the constant**

In `src/agf/scoring/features.py`, after `LONGEVITY_FLOOR_DAYS = 14` (line 97), add:
```python
PROVEN_WINNER_DAYS = 21  # a still-live ad running >= this many days is a proven "winner"
```

- [ ] **Step 4: Add the two dataclass fields**

In `src/agf/scoring/features.py`, in the `FeatureVector` dataclass, immediately after
`iteration_rate: float = 0.0` (line 148) add:
```python
    proven_winner_count: int = 0         # live ads running >= PROVEN_WINNER_DAYS (winner_depth)
    count_growth: float = 0.0            # max(0, window_growth): rising ad count only, never negative
```

- [ ] **Step 5: Compute `proven_winner_count` from the live-ad longevities**

In `build_features`, the `longs` list already holds per-live-ad running days (line 174).
Immediately after the `long_share = (...)` assignment (line 176-177) add:
```python
    proven_winners = sum(1 for d in longs if d >= PROVEN_WINNER_DAYS)
```

- [ ] **Step 6: Pass both new fields into the returned `FeatureVector`**

In the `return FeatureVector(...)` call, after the `iteration_rate=ewma(...)` argument (lines 264-265) add:
```python
        proven_winner_count=proven_winners,
        # rising ad count is a small bonus; a FALLING count clamps to 0 so winner
        # consolidation (20 ads -> 2 winners) is never punished. window_growth is the
        # sustained multi-week ramp already computed above.
        count_growth=max(0.0, window_log_growth(series)),
```

- [ ] **Step 7: Run the new test to verify it passes**

Run: `pytest tests/scoring/test_build_features.py::test_build_features_winner_depth_and_count_growth_clamp -v`
Expected: PASS

- [ ] **Step 8: Run the whole build_features suite (no regressions)**

Run: `pytest tests/scoring/test_build_features.py -v`
Expected: PASS (all 7 existing + the new one).

- [ ] **Step 9: Commit**

```bash
git add src/agf/scoring/features.py tests/scoring/test_build_features.py
git commit -m "feat(scoring): winner_depth + clamped count_growth features"
```

---

## Task 3: Rewire `_sv_components` to the new SV inputs

**Files:**
- Modify: `src/agf/scoring/run_scoring.py:31-41` (`_sv_components`)
- Test: covered by Task 4's regression test + the full suite.

- [ ] **Step 1: Replace `_sv_components`**

In `src/agf/scoring/run_scoring.py`, replace `_sv_components` (lines 31-41) with:
```python
def _sv_components(fv, members):
    # v3: running-days winner signal dominates; count_growth is a clamped (>=0) bonus.
    return {
        "winner_age": pctile(fv.max_longevity_days, _values(members, "max_longevity_days")),
        "winner_depth": pctile(fv.proven_winner_count, _values(members, "proven_winner_count")),
        "count_growth": pctile(fv.count_growth, _values(members, "count_growth")),
        "iteration": pctile(fv.iteration_rate, _values(members, "iteration_rate")),
    }
```

- [ ] **Step 2: Run the scoring suite to confirm nothing references old SV keys**

Run: `pytest tests/scoring/ -v`
Expected: PASS. (If any test still feeds `wow`/`ramp`/`accel`/`ewma`/`longevity` to
`scaling_velocity`, it was updated in Task 1; nothing else should reference them.)

- [ ] **Step 3: Commit**

```bash
git add src/agf/scoring/run_scoring.py
git commit -m "feat(scoring): feed running-days winner signal into SV axis"
```

---

## Task 4: Regression test — consolidation beats spray

The headline behavior from the spec: an app that consolidated to 2 long-running winners must outscore an
app spraying 20 fresh ads. Test at the `_sv_components` → `scaling_velocity` → `gem_score` level with
hand-built feature vectors (no DB needed), holding all other axes equal.

**Files:**
- Create: `tests/scoring/test_running_days_regression.py`

- [ ] **Step 1: Write the failing test**

Create `tests/scoring/test_running_days_regression.py`:
```python
"""GemScore v3 regression: winner consolidation must beat ad-spray (spec 2026-06-16)."""
from agf.scoring.run_scoring import _sv_components
from agf.scoring.gemscore import scaling_velocity, gem_score


class _FV:
    """Minimal stand-in exposing only the fields _sv_components reads."""
    def __init__(self, max_longevity_days, proven_winner_count, count_growth, iteration_rate):
        self.max_longevity_days = max_longevity_days
        self.proven_winner_count = proven_winner_count
        self.count_growth = count_growth
        self.iteration_rate = iteration_rate


def _sv(fv, members):
    return scaling_velocity(_sv_components(fv, members))


def test_consolidated_winners_outscore_spray():
    # consolidated: count fell 20->2 (count_growth clamped to 0), but 2 ads have run 60d
    consolidated = _FV(max_longevity_days=60, proven_winner_count=2,
                       count_growth=0.0, iteration_rate=0.1)
    # spray: 20 fresh ads, none proven, count rising fast, launching constantly
    spray = _FV(max_longevity_days=5, proven_winner_count=0,
                count_growth=0.8, iteration_rate=1.0)
    members = [consolidated, spray]

    sv_consolidated = _sv(consolidated, members)
    sv_spray = _sv(spray, members)
    assert sv_consolidated > sv_spray            # winner signal (0.70) beats count/iteration (0.30)

    # with every other axis held equal, the full GemScore follows SV
    gs_consolidated = gem_score(sv_consolidated, 0.6, 0.6, 0.6, 0.6)
    gs_spray = gem_score(sv_spray, 0.6, 0.6, 0.6, 0.6)
    assert gs_consolidated > gs_spray


def test_falling_count_is_no_worse_than_flat_count():
    # two identical apps except one's count fell (clamped 0) and one's was flat (0).
    # clamping means a falling count is treated the same as flat — never a penalty.
    falling = _FV(max_longevity_days=30, proven_winner_count=1,
                  count_growth=0.0, iteration_rate=0.3)   # window_growth was negative -> clamped 0
    flat = _FV(max_longevity_days=30, proven_winner_count=1,
               count_growth=0.0, iteration_rate=0.3)       # genuinely flat -> 0
    members = [falling, flat]
    assert _sv(falling, members) == _sv(flat, members)
```

- [ ] **Step 2: Run it to verify it passes**

Run: `pytest tests/scoring/test_running_days_regression.py -v`
Expected: PASS. (Hand-check: `_sv_components` over the two members gives consolidated
percentiles winner_age=.75, winner_depth=.75, count_growth=.25, iteration=.25 →
SV = .40·.75 + .30·.75 + .15·.25 + .15·.25 = 0.60; spray = 0.40. 0.60 > 0.40.)

- [ ] **Step 3: Commit**

```bash
git add tests/scoring/test_running_days_regression.py
git commit -m "test(scoring): regression — consolidated winners beat ad-spray"
```

---

## Task 5: Full verification + live rescore note

**Files:** none (verification only).

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -q`
Expected: PASS. Watch specifically for `tests/scoring/test_backtest_synthetic.py` and
`tests/scoring/test_schema_matview.py` — absolute scores shift under the new weights, so if either
asserts a hard score number (not a relative ordering), update the expected value to the newly observed
one ONLY after confirming the ordering/behavior is still correct. Do not blindly bump assertions.

- [ ] **Step 2: Sanity-check the alert threshold**

`ALERT_THRESHOLD = 60.0` (`run_scoring.py:18`). The Demand floor exponent rose (.20→.38) and Whitespace
fell (.10→.05); a no-D/W app now multiplies by `0.5^0.43 ≈ 0.742` (was `0.5^0.30 ≈ 0.812`), so absolute
scores drift DOWN slightly. No code change in this task — just confirm in Step 3's rescore that the feed
is not emptied. If the feed collapses, that is a follow-up threshold retune, not a silent edit here.

- [ ] **Step 3: Rescore the live feed and eyeball (manual, host)**

Per memory: the live launchd worker holds scoring code in memory and reverts manual re-scores, so kick it
first. From the project root on the host:
```bash
launchctl kickstart -k gui/$(id -u)/com.fielddesk.worker   # picks up new code; see veto-thresholds memory
python -m agf.scoring.run_scoring  # or the project's run-scoring entrypoint, against localhost:5433
```
Expected: scoring completes; spot-check that apps with old long-running ads but few current ads moved UP
the feed relative to high-count/young-ad apps. Note the new feed count.

- [ ] **Step 4: Final commit / branch wrap**

The branch `feat/gemscore-running-days` now holds the spec, plan, and implementation. Use the
`superpowers:finishing-a-development-branch` skill to choose merge vs PR.

---

## Self-review notes

- **Spec coverage:** axis reweight (Task 1) ✓; SV running-days rebuild + clamped count (Tasks 1-3) ✓;
  `PROVEN_WINNER_DAYS=21` (Task 2) ✓; winner depth+age blend (Tasks 2-4) ✓; demand bump (Task 1) ✓;
  longevity-floor veto stays OFF — already absent from `_VETOES` (`vetoes.py:76-85`), nothing to do ✓;
  incumbent ad-volume veto stays ON — untouched ✓; regression test (Task 4) ✓.
- **Data limitation honored:** all running-days come from live ads via existing `max_longevity_days` /
  `longs`; no `end_date` assumed.
- **Deviation logged:** spec's "drop unused SV features" narrowed to "stop referencing them" to avoid
  breaking `test_build_features_accel_and_longevity_share` and any external reader; `window_growth` is
  still needed for `count_growth`.
