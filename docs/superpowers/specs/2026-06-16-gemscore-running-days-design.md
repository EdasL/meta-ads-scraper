# GemScore v3 — running-days over ad-volume

**Date:** 2026-06-16
**Status:** approved (design), pending implementation plan

## Problem

GemScore v2 rewards rising **ad count** and punishes falling ad count. The
"scaling velocity" axis (SV, 40% of the score) draws ~65% of its weight from
week-over-week growth in active ad count (ramp .30, wow .15, accel .10, ewma
.10).

This is backwards for the cohort we hunt. A healthy emerging app tests ~20 ads,
finds 2 winners, and kills the other 18. Its active ad count drops 20 → 2. Today
that reads as *negative velocity* and the app's score falls — even though the 2
survivors are the long-running, money-making winners. An app spraying 20
unproven ads outscores an app that found its winners. Wrong.

The real signal of a winner is **how long an ad has been running** (and how many
durable ads the app has), plus store traction (installs, reviews). Ad count is
at best a weak positive and must never be a negative.

## Goal

Re-tilt GemScore so its three pillars — **ad running-days, installs, reviews** —
drive ~70% of the score, and so that *dropping* ad count can never lower a
score.

## Data available (and a limitation)

Per ad (`public.ads`): `start_date`, `is_active`. There is **no `end_date`**.
So running-days is computable only for **live** ads: `now − start_date`. For a
dead ad we cannot know how long it ran. This is acceptable: the winners we care
about are the live, long-running ones. All running-days signals below are
computed over **currently-active ads only**.

`longevity_days(start_epoch, now_epoch)` already exists in `src/agf/scale.py`.

## Design

### Axis weights (multiplicative, exponents sum to 1.0)

```
GemScore = 100 · SV^.42 · D^.38 · UR^.12 · FI^.03 · W^.05
```

| axis | v2 | v3 | meaning |
|------|-----|-----|---------|
| SV — winner durability | .40 | **.42** | rebuilt: running-days driven (was ad-count driven) |
| D — demand | .20 | **.38** | installs + reviews (D_WEIGHTS unchanged: installs .55, reviews .45) |
| UR — under-radar | .25 | .12 | inverse brand size (incumbent brake) |
| FI — funnel intent | .05 | .03 | monetization signal |
| W — whitespace | .10 | .05 | niche crowding |

Resulting pillar shares of the total exponent:
- running-days ≈ .42 × .70 = **.29**
- installs ≈ .38 × .55 = **.21**
- reviews ≈ .38 × .45 = **.17**
- pillars total ≈ **.67** (~70%)

### SV internals (the rebuild)

Replace `SV_WEIGHTS` (currently wow/ramp/accel/ewma/longevity/iteration) with:

| component | weight | definition |
|-----------|--------|------------|
| `winner_age` | .40 | max running-days across the app's **live** ads (the single oldest battle-tested winner) |
| `winner_depth` | .30 | count of **live** ads with running-days ≥ `PROVEN_WINNER_DAYS` (21) |
| `count_growth` | .15 | rising active-ad count, **clamped at 0** — a falling count contributes 0, never negative |
| `iteration` | .15 | new-ad launches per week, EWMA-smoothed (keep existing `iteration` computation — "still launching, not abandoned") |

Winner signal (`winner_age` + `winner_depth`) = **70% of SV**.

`winner_age` and `winner_depth` are normalized through the **same cohort
percentile** path as every other feature (`run_scoring.py` percentile
normalization) — no hand-tuned day-to-score curves.

### The core behavioral fix: clamped count_growth

`count_growth` collapses the old wow/ramp/accel/ewma count-velocity into a
single component, then **clamps it at 0**:

```
count_growth = max(0, <rising-count velocity signal>)
```

When active ad count falls (e.g. 20 → 2 winner consolidation), `count_growth`
= 0 and the app loses nothing on that component. When count rises, it adds a
small bonus. Ad count can only ever *help*, never *hurt*. This is the crux of
the fix.

### Constants

```python
PROVEN_WINNER_DAYS = 21   # a live ad running >= this many days counts as a "winner"
WINNER_AGE_CAP_DAYS = 35  # winner_age saturates here (see refinement below)
```

(`PROVEN_WINNER_DAYS` was an implicit 14-day floor; raised to 21 for the depth
threshold. Single knob, easy to retune.)

### Refinement (2026-06-16, found during implementation): cap `winner_age`

Using the **raw** oldest-live-ad age for `winner_age` rewards incumbency: a big
brand that has run one ad for 500+ days would top the axis, and in cohort
percentile normalization those ancient ads squash every genuinely-emerging app's
month-old winner down to the bottom. v2 avoided this by using a *bounded*
longevity-share; v3 keeps "longer = better" but **caps the signal at
`WINNER_AGE_CAP_DAYS = 35`** (user decision 2026-06-16: ~a month of running already
proves a winner; longer just signals incumbency): an ad running ≥35 days is "fully
proven", extra age adds nothing, and a young app's ~month-old winner reaches the cap
and sits at the top of the axis. The cap is applied only to the SV `winner_age`
*component*
(both the app's value and the cohort members it is ranked against are capped);
the stored/displayed `max_longevity_days` stays raw (it is a real reported number
and is written to `advertiser_scores`). Incumbents are still held back by the
under-radar axis (UR≈0 collapses the gate) and the existing vetoes — not by
`winner_age`.

### Test corpus is v3-relabeled (not just renumbered)

`tests/scoring/test_backtest_synthetic.py` encoded the v2 worldview: "breakout" =
young app with an exploding ad **count** and a single short-lived ad; "incumbent"
= oldest ad. Under v3 that is backwards, so the corpus is updated to v3 semantics:
a v3 breakout is an emerging app (low ratings/reach → high UR) that has found
**multiple proven winners** — several live ads running ≥21 days → high
`winner_depth`. Incumbents stay distinguished by ratings/reach (UR≈0 +
`established_traction`/`relaunch` vetoes), NOT by ad age. The property tests
(rank/threshold/veto) keep their original intent; only the trajectories that feed
them are re-labeled.

The backtest does **not** seed `adtech.app_metrics` snapshots, so the Demand axis
floors to the neutral 0.5 for every member (this has always been true of this
corpus — demand was never seeded, pre- or post-v3). The result is actually the
stronger statement: with Demand neutralized, the breakouts still clear
`ALERT_THRESHOLD=60` (they land ~63, ~10 points above the next non-breakout) on
the **running-days winner signal alone**. The Demand-axis computation itself
(install/review velocity from `app_metrics` deltas) is covered by unit tests in
`tests/scoring/test_build_features.py`; the backtest's job is rank/threshold/veto
properties, not demand end-to-end. Because Demand's exponent rose (.20→.38), a
real production gem with genuine install/review growth scores *above* this
floor-only baseline — flooring is the conservative case.

### Vetoes

- **Longevity-floor veto stays OFF.** Do not hard-kill an app because no ad has
  run 14/21 days. A freshly-consolidated app must survive; the soft `winner_age`
  / `winner_depth` signals handle weakness. If `longevity_floor` is still wired
  into the active veto registry, confirm it is excluded.
- **Incumbent ad-volume veto stays ON** (`MAX_ACTIVE_ADS_FOR_GEM = 200`). Still
  want a brake on giants.

## Files in scope

- `src/agf/scoring/gemscore.py` — axis exponents, `SV_WEIGHTS`, add
  `PROVEN_WINNER_DAYS`.
- `src/agf/scoring/features.py` — build `winner_age`, `winner_depth`,
  `count_growth` (clamped), reuse `iteration`; drop the count-velocity-only SV
  features that are no longer referenced.
- `src/agf/scoring/run_scoring.py` — ensure new SV features flow through
  percentile normalization.
- `src/agf/scoring/vetoes.py` — confirm longevity-floor veto is not active.
- `src/agf/scale.py` — `longevity_days` already exists; reuse.

## Out of scope

- No DB migration; no new columns (we have `start_date` + `is_active`).
- No change to D internals (installs .55 / reviews .45 stay), UR, FI, W internals.
- No change to discovery / enrichment pipeline.

## Testing

- Unit: an app whose live-ad count fell from 20 → 2 with two 60-day-old live ads
  scores **higher** than an app with 20 live ads all < 7 days old. (Direct
  regression for the reported bug.)
- Unit: `count_growth` clamps — identical apps differing only in a falling vs
  flat count get the same SV contribution from that component.
- Unit: `winner_depth` counts only live ads ≥ 21 days.
- Existing scoring suite stays green; reweighting will shift absolute scores, so
  any snapshot/threshold assertions (alert=60) get reviewed, not blindly updated.
- Re-score the live feed and eyeball: winner-consolidated apps should rise.

## Open follow-ups (not this change)

- If/when we capture ad `end_date`, extend running-days to dead ads for a
  fuller durability picture.
