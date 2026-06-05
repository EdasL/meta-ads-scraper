# GemScore Priors Backtest — 2026-06-05

## 1. Nature of this Validation

**This is a synthetic-trajectory validation, not a historical backtest.**

The `data/` directory contains no real Meta Ad Library snapshots. No labeled
historical advertiser trajectories exist in the repository. Consequently the
harness in `tests/scoring/test_backtest_synthetic.py` and `scripts/backtest.py`
builds a hand-labeled corpus of 16 archetypal advertisers, seeds it into the
Docker Postgres, runs the shipping `score_all` path (pctile normalization within
cohort → gem_score → vetoes → `adtech.advertiser_scores`), and asserts on
rank/threshold/veto properties.

This validates that the *algorithm correctly separates the archetypes it was
designed to separate*. It does not validate that those archetypes are calibrated
against real-world ground truth.

---

## 2. Corpus Design

Two cohorts (`Health & Fitness×US`, `Education×US`), 8 members each (≥4 per
cohort satisfies non-degenerate pctile). All series ≥ 8 weeks.

| Sentinel | Cohort | Label | Key trajectory |
|----------|--------|-------|---------------|
| BT01, BT11 | HF / Edu | true_breakout | convex-rising ×1.6/wk, tiny ratings, INSTALL_MOBILE_APP CTA, funnel |
| BT02, BT12 | HF / Edu | true_breakout | convex-rising ×1.8/wk, tiny ratings, INSTALL_MOBILE_APP CTA, funnel |
| BT03, BT13 | HF / Edu | incumbent | flat high series, 7–9 M ratings, old release, no funnel |
| BT04, BT14 | HF / Edu | flat_advertiser | constant mid series, mid ratings, no funnel |
| BT05, BT17 | HF / Edu | disguised_incumbent | good trajectory but `parent_company_id` set → vetoed |
| BT06, BT18 | HF / Edu | relaunch | convex series, is_emerging=True, rating_count > 100 k |
| BT07 | HF | seasonal_only | period-4 seasonal, no trend, ends on peak |
| BT08 | HF | seasonal_with_trend | same seasonal + linear rising trend |
| BT15 | Edu | one_week_blip | 1 metric row (weeks_observed = 1) |
| BT16 | Edu | short_longevity | longevity_days = 10 < 14-day floor |

---

## 3. Test Results

```
14 passed in 1.46s
```

All 14 property tests pass:

- `test_breakout_alerts_and_high_score` — all 4 true_breakouts: alert=True, gem_score ≥ 70
- `test_breakout_scores_higher_than_incumbent` — B > I in both cohorts
- `test_incumbent_does_not_alert` — incumbents do not alert
- `test_flat_advertiser_does_not_alert` — flat advertisers do not alert
- `test_seasonal_only_vetoed` — `seasonal_spike` veto fires on BT07
- `test_seasonal_with_trend_not_seasonal_spike_vetoed` — BT08 not vetoed for seasonal_spike
- `test_disguised_incumbent_vetoed` — both disguised incumbents hit `disguised_incumbent`
- `test_relaunch_vetoed` — both relaunch advertisers vetoed as `relaunch`
- `test_one_week_blip_vetoed` — `insufficient_persistence` veto fires
- `test_short_longevity_vetoed` — `longevity_below_floor` veto fires
- `test_gem_score_monotone_in_sv/ur/fi` — gem_score non-decreasing in each axis
- `test_none_axis_uses_floor_not_zero` — None axis uses floor=0.05, present-zero axis = 0.0

---

## 4. Calibration Report Numbers

### 4a. Per-Label GemScore Distribution

```
Label                      N     Min  Median     Max  Veto reason(s)
----------------------------------------------------------------------
true_breakout              4    72.2    74.3    78.5  none
incumbent                  2    14.3    15.6    17.0  none
flat_advertiser            2    23.3    25.4    27.6  none
seasonal_only              1    61.3    61.3    61.3  seasonal_spike
seasonal_with_trend        1    54.9    54.9    54.9  none
disguised_incumbent        2    63.9    65.2    66.5  disguised_incumbent
relaunch                   2    51.3    52.5    53.8  relaunch
one_week_blip              1    47.5    47.5    47.5  insufficient_persistence
short_longevity            1    46.9    46.9    46.9  longevity_below_floor
```

### 4b. Per-Advertiser Scores (sorted by gem_score desc)

```
PID     Label                  GemScore     SV     UR     FI  Alert  VetoReason
--------------------------------------------------------------------------------
BT11    true_breakout              78.5   0.69   0.84   1.00    YES  -
BT12    true_breakout              75.4   0.68   0.78   1.00    YES  -
BT01    true_breakout              73.3   0.57   0.91   1.00    YES  -
BT02    true_breakout              72.2   0.59   0.84   1.00    YES  -
BT05    disguised_incumbent        66.5   0.57   0.69   1.00     no  disguised_incumbent
BT17    disguised_incumbent        63.9   0.69   0.47   1.00     no  disguised_incumbent
BT07    seasonal_only              61.3   0.72   0.?    1.00     no  seasonal_spike
BT08    seasonal_with_trend        54.9   0.?    0.?    1.00     no  -
BT06    relaunch                   53.8   0.69   0.25   1.00     no  relaunch
BT18    relaunch                   51.3   0.69   0.25   1.00     no  relaunch
BT15    one_week_blip              47.5   0.33   0.59   1.00     no  insufficient_persistence
BT16    short_longevity            46.9   0.27   0.75   1.00     no  longevity_below_floor
BT14    flat_advertiser            27.6   0.33   0.25   0.20     no  -
BT04    flat_advertiser            23.3   0.23   0.25   0.20     no  -
BT13    incumbent                  17.0   0.33   0.06   0.20     no  -
BT03    incumbent                  14.3   0.23   0.06   0.20     no  -
```

### 4c. Precision / Recall Sweep

Positive class = `true_breakout` AND not vetoed (4 positives).

```
Threshold    TP    FP    FN  Precision     Recall
-------------------------------------------------------
       50     4     1     0      0.800      1.000
       60     4     0     0      1.000      1.000
       70     4     0     0      1.000      1.000  <-- shipping
       80     0     0     4        N/A      0.000
       90     0     0     4        N/A      0.000
```

The `seasonal_with_trend` advertiser (score 54.9) is the sole false positive at
threshold 50 — it has a plausible trajectory but is not labeled as a true
breakout. It disappears at threshold 60. At threshold 70 precision is 1.0 and
recall is 1.0 on this corpus.

### 4d. Veto-Confusion Table

```
Label                       disguised_incumbent  insufficient_persistence  longevity_below_floor  none  relaunch  seasonal_spike
---------------------------------------------------------------------------------------------------------------------------------
true_breakout                                 0                         0                      0     4         0               0
incumbent                                     0                         0                      0     2         0               0
flat_advertiser                               0                         0                      0     2         0               0
seasonal_only                                 0                         0                      0     0         0               1
seasonal_with_trend                           0                         0                      0     1         0               0
disguised_incumbent                           2                         0                      0     0         0               0
relaunch                                      0                         0                      0     0         2               0
one_week_blip                                 0                         1                      0     0         0               0
short_longevity                               0                         0                      1     0         0               0
```

Every archetype hits exactly its intended veto column. No cross-fires.

### 4e. Sensitivity — Alternate Exponent Sets

```
Exponent set                         Pair          Breakout  Incumbent   B>I?
-----------------------------------------------------------------------------
shipping (SV=.50 UR=.35 FI=.15)      BT01 vs BT03      73.3       14.3    YES
shipping (SV=.50 UR=.35 FI=.15)      BT11 vs BT13      78.5       17.0    YES
SV-heavy  (SV=.60 UR=.25 FI=.15)     BT01 vs BT03      70.0       16.3    YES
SV-heavy  (SV=.60 UR=.25 FI=.15)     BT11 vs BT13      77.0       20.0    YES
UR-heavy  (SV=.40 UR=.45 FI=.15)     BT01 vs BT03      76.7       12.6    YES
UR-heavy  (SV=.40 UR=.45 FI=.15)     BT11 vs BT13      80.0       14.4    YES
balanced  (SV=.40 UR=.40 FI=.20)     BT01 vs BT03      77.0       13.3    YES
balanced  (SV=.40 UR=.40 FI=.20)     BT11 vs BT13      80.7       15.3    YES
```

Breakout > incumbent ordering is robust across all tested exponent sets.

---

## 5. Verdict on the Priors

### 5a. What holds

- **SV/UR/FI weighting (0.50/0.35/0.15) is defensible on synthetic archetypes.**
  The multiplicative AND-gate collapses incumbents to ≈ 14–17 (UR ≈ 0.06 kills
  the product) while true breakouts sit at 72–79. The gap is ~55 points even
  with the SV exponent reduced from 0.5 to 0.4.

- **ALERT_THRESHOLD=70 gives perfect precision and recall on this corpus.**
  There is clean separation between true_breakout (72–79) and all other
  non-vetoed archetypes (flat: 23–28, incumbent: 14–17, seasonal_with_trend: 55).
  Threshold 60 also yields perfect precision/recall; 70 provides a safety margin
  against the seasonal_with_trend class (score 54.9).

- **All 5 vetoes fire exactly on their intended archetypes.** No cross-fires, no
  missed fires.

- **Monotonicity invariants hold**: gem_score is strictly non-decreasing in each
  axis; None-axis imputation to floor=0.05 correctly outscores a present-zero
  axis.

### 5b. Potential miscalibration — what to watch

1. **FI weight (0.15) may be too low to gatekeep non-app advertisers.**
   All true breakouts have FI=1.0 in this corpus (funnel + INSTALL_MOBILE_APP
   CTA). If real-world data contains advertisers with strong SV and UR but no
   funnel and a generic CTA, FI would drop to 0.20 (only the `monetize`
   component from `has_app`). With FI=0.20 and the 0.15 exponent the penalty is
   only `100 * 0.20^0.15 ≈ 78%` of the max — not a hard gate. This is unlikely
   to cause false positives in practice but warrants monitoring.

2. **Seasonal_with_trend scores 54.9 (unvetoed, unalerted).**
   This is correct behavior — the STL veto correctly passes it, but the score
   sits just below the 70 threshold, meaning it would never alert without a
   stronger SV. In a real-world scenario with noisier data, this class might
   occasionally score ≥ 70 if the trend component is strong. The 70 threshold
   provides a comfortable margin here (15 points), but the `seasonal_spike`
   veto is the primary guard.

3. **Precision/recall degrades sharply above 80.**
   True breakouts in this corpus peak at 78.5. This means the 70 threshold has
   only ~8.5 points of headroom before all positives are missed. If real-world
   cohorts are larger (more competitors) and normalization compresses scores
   downward, the true breakout scores may sit closer to 70. Threshold
   calibration should be re-run on real cohorts once data is available.

---

## 6. What a True Historical Backtest Would Require

To replace this synthetic validation with a real historical backtest:

1. **≥ 8 consecutive weekly Meta Ad Library snapshots** per advertiser, stored
   in `adtech.app_advertiser_metrics`. The repo schema already supports this;
   the data pipeline (`tracker/snapshots.py`) just needs to run weekly and
   accumulate rows.

2. **Ground-truth labels** for at least 20–50 advertisers per cohort: binary
   "did this app break out in the following 90 days?" labels, sourced from App
   Store chart position, revenue rank from Sensor Tower / data.ai, or direct
   advertiser revenue data.

3. **Run `score_all` on each historical weekly slice** (using the `as_of`
   parameter to freeze the feature window) and record the scores.

4. **Compute precision/recall against labels** using the historical alert
   decisions. This produces a real ROC curve and identifies whether 70 is still
   the optimal cut.

The synthetic harness in `scripts/backtest.py` is designed to be replaced
in-place: swap the corpus builder for a real-data loader, keep the same
precision/recall sweep and sensitivity analysis, and re-run the report.
