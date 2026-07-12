# Keeper absorption at discovery speed — 10,000-row Discover database

**Date:** 2026-07-12
**Goal:** the Discover database (frontend.emerging_scaling_apps population) reaches
10,000+ rows as fast as intake allows (honest math below: ~4,000 within a day of
the drain, 10,000 in ~1–2 weeks depending on intake and the lane raise), and a
newly kept app becomes user-visible within ~1 hour instead of waiting weeks or
forever. No changes to keywords, minting,
triage, vetoes, the GemScore formula, or the UI.

## Problem (measured 2026-07-12, prod)

Last week's discovery work multiplied intake (~275 keepers/day this week, peaks
~500), but the stage that turns a keeper into a database row did not scale with it:

- A kept candidate only becomes scoreable after `pull_keeper_history` pulls its
  ads and reconstructs its 16-week metric series (12 proxy requests per keeper).
- That stage runs once daily on **half of AGF_DISCOVERY_ENRICH_BUDGET (600) = 300
  requests/day = 25 keepers/day**, serially, inside the worker tick loop.
- Result: **2,059 kept apps (46% of all keepers ever) have an advertiser row but
  zero ads and no score — invisible to users.** 1,708 of them are from this week
  alone (89% of the week's finds). June cohorts show ~0 waiting: absorption kept
  up until intake was multiplied.

Database today: 1,928 rows (2,073 scored minus 145 vetoed).

## Fix

### 1. One-time backlog drain (~2,059 keepers)

A bounded, resumable drain script (`scripts/drain_keeper_backlog.py`) that runs
the existing `refresh_one_advertiser` path over `keepers_missing_history()`
(oldest-first, unchanged) with **3–4 concurrent lanes** via the existing
extractor-pool + page-pool machinery. Runs out-of-loop in the prod worker
container (the proven one-off pattern), chunked with progress prints, safe to
interrupt and re-run (the missing-history query self-heals: done keepers drop
out). Cost ≈ 25k proxy requests; wall clock ≈ 5–10h at 3–4 lanes.

Scoring is already retroactive: `refresh_one_advertiser` reconstructs the
16-week series, so drained keepers pass the `weeks_observed >= 2` persistence
gate immediately and appear in the database after the next 30-min scoring pass +
matview refresh. Expected DB after drain: ~4,000 rows.

### 2. Continuous keeper absorption (permanent)

Requirement: absorb ≥ 1,000 keepers/day, without blocking the worker tick loop
for hours, with a per-keeper heartbeat (watchdog lesson 2026-07-11) and a
failure marker so a permanently-failing keeper cannot hot-loop the queue
(enrich-TTL lesson 2026-07-10).

Preferred shape (decide final wiring at plan time after reading the consumer
code): enqueue keeper-history jobs into the existing `public.pipeline_jobs`
queue from the worker tick (same pattern as continuous enrich, step-3 refactor)
and let the consumer daemon drain them continuously. Fallback shape if the
consumer contract doesn't fit: keep the stage in-tick but run it over the
dynamic page pool with its own budget knob (`AGF_KEEPER_HISTORY_BUDGET`,
default sized to 1,000 keepers/day = 12,000 requests) and per-keeper heartbeat.

Splitting the knob is part of the fix: today `AGF_DISCOVERY_ENRICH_BUDGET`
funds two different jobs (keeper history AND top-N gem enrich). Raising the
combined knob 20x to feed keepers would silently 20x gem enrich too — the
capacity-limit-hides-a-second-job lesson (2026-07-11). Keeper history gets its
own budget; the gem-enrich half keeps its current effective rate.

### 3. Optional accelerator (only if the founder wants the week shortened)

Sweep lanes 3 → 5 on the just-activated dynamic pool, ramped one step at a time
watching per-lane breaker/challenge telemetry. Honest intake math: at this
week's ~275 keepers/day, 4,000 → 10,000 takes ~3 weeks; intake is still
climbing week-over-week from the keyword work, and the lane raise multiplies
pages swept per day, so ~1–2 weeks is the realistic range with this lever on.

## Scale checks owed at plan time (not hand-waved)

- Scoring cron runtime and matview refresh time at 4k and 10k advertisers
  (O(n) surprises precedent: review-queue index, 2026-07-02).
- Review-queue diversity blend behavior at 5x population.
- Embedding coverage (candidate/store-listing embeds) keeping up at 10x inflow.
- DataImpulse GB and VM disk headroom (drain ≈ 25k requests; steady state
  ≈ 12–15k requests/day, up from ~3–4k).

## Verification gates

1. Drain: progress counts live; after completion `keepers_missing_history() ≈ 0`
   and DB ≥ ~3,800 rows.
2. Steady state: one full day where absorbed ≥ intake (both measured from
   discovery.candidates vs adtech.app_advertiser_metrics), backlog stays ~0.
3. Latency: sample of new keepers found→visible ≤ 1h.
4. Health: worker heartbeat stable, no watchdog kills, Meta challenge rate at
   baseline, scoring cron + matview timings recorded at the new corpus size.

## Explicitly out of scope

Keyword/mint changes, triage/veto changes, GemScore changes, UI changes,
page-ID enumeration (separate future project).
