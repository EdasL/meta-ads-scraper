# Flywheel active-keyword retirement — design

**Date:** 2026-06-27
**Status:** approved (design), pending implementation plan
**Scope:** P2 "make the flywheel renew, not just deepen" — efficiency half only.
Renewal levers (epsilon-greedy explore lane, MMR, app-graph) are explicitly deferred.
**Related:** [keyword-seed design](../../research/2026-06-18-keyword-seed-design.md) §5 P2.

## Problem (measured 2026-06-27, prod `agf-db`)

A mined keyword enters as `trial`, is swept for `TRIAL_SWEEPS` (4) sweeps, then either
promotes to `active` (≥`PROMOTE_MIN_NEW`=1 new candidate) or `retired`. **Actives are
then swept forever — `dynamic_keywords.py` keeps them "for good" and never re-evaluates
them.** The result:

- `gem_copy` flywheel: 420 keywords, **2,250 sweeps → 336 new candidates (~0.15/sweep)**;
  41 actives still in daily rotation, most long saturated.
- `funnel_copy`: 118 keywords, 559 sweeps → 641 new (~1.15/sweep) — healthier, but the
  same structural gap applies.

The keyword's own ad copy stops yielding new advertisers once its basin is harvested, yet
the sweep keeps paying proxy + Meta-GraphQL budget to re-walk it. There is no recent-window
signal to detect this: the table stores only cumulative `sweeps_used` /
`new_candidates_total`, and the per-keyword *re-seen* count is never captured.

## Goal / non-goals

**Goal:** stop re-sweeping saturated active keywords, reclaiming that budget. Success =
saturated `gem_copy`/`funnel_copy` actives transition to `retired` within a few sweeps of
going dry, and `flywheel_runs.stats.retired` reflects it.

**Non-goals (this increment):** epsilon-greedy budget governor, MMR keyword selection,
app-graph traversal, store-new-releases category frame (all P1/renewal), and any change to
promotion criteria, trial cadence, or what gets mined. Pure pruning.

## Mechanism — consecutive dry-streak retirement

Chosen over a novelty-rate floor (doc §6) because the rate signal needs per-keyword
*returned* counts we do not capture and a rolling-window store we do not have; the
dry-streak uses only the new-candidate count already passed to `record_keyword_sweep`. It
is coarser (a keyword finding 1 new every few sweeps never retires) but that keyword is
still producing, so leaving it alive is acceptable. Novelty-rate remains a future upgrade
if dry-streak proves too coarse.

### Schema — `db/schema/143_dynamic_keywords_dry_streak.sql`

```sql
ALTER TABLE discovery.dynamic_keywords
  ADD COLUMN dry_streak INTEGER NOT NULL DEFAULT 0;
```

Applied by `agf.db.migrate.apply_migrations` (lexical order, tracked in
`public.schema_migrations`). Existing actives start at 0 and retire naturally once they
accrue K dry sweeps — no backfill.

### Constant — `dynamic_keywords.py`

```python
def retire_dry_streak() -> int:   # consecutive 0-new sweeps before an ACTIVE retires
    return _env_int("AGF_FLYWHEEL_RETIRE_DRY_STREAK", 5)
```

Read at call time (matches `trial_sweeps()` / `promote_min_new()`). Set very high to
disable (rollback lever).

### Logic — `record_keyword_sweep(conn, keyword_id, *, new_candidates)`

The existing UPDATE additionally maintains the streak and returns it:

```
SET sweeps_used = sweeps_used + 1,
    new_candidates_total = new_candidates_total + :n,
    dry_streak = CASE WHEN :n > 0 THEN 0 ELSE dry_streak + 1 END,
    last_swept_at = now()
RETURNING status, sweeps_used, new_candidates_total, dry_streak
```

Verdict order after the UPDATE:

1. **Active retirement (new):** `if row.status == "active" and row.dry_streak >=
   retire_dry_streak(): set status='retired'; return "retired"`.
2. **Trial verdict (unchanged):** trials below `trial_sweeps()` stay `trial`; at budget,
   promote to `active` if `new_candidates_total >= promote_min_new()` else `retired`.

A non-trial, non-saturated active returns its current status, as today.

### Observability — no new code

`workers/main.py` (app sweep ~L293-301, funnel sweep ~L401-408) already increments
`stats["retired"]` when `record_keyword_sweep` returns `"retired"`. Active retirements
therefore surface in `flywheel_runs.stats` / `funnel_sweep_runs.stats` for free, for both
pools.

## Semantics & edge cases

- **Permanent.** A retired active joins the never-re-mint `tried` set
  (`tried_keywords` is status-agnostic). Matches retired-trial semantics. A genuinely
  productive basin re-enters via fresh gem-copy mining, so no revival state machine.
- **Both pools.** `kind` is untouched; logic applies to `app` and `funnel` identically.
- **`apply_verdicts` stays trial-only.** It is a backlog recycler for trials; existing
  actives are at `dry_streak=0`, so a batch could not retire them anyway. The per-sweep
  path is the live mechanism.
- **Interaction with promotion:** a trial promoted on one lucky hit that then goes dry now
  retires after K sweeps — the streak doubles as a safety net for over-eager promotion.

## Testing (TDD, DB-backed via the ephemeral pg fixture)

1. active at `dry_streak = K-1`, sweep with `new_candidates=0` → status `retired`.
2. active at `dry_streak = K-1`, sweep with `new_candidates>0` → `dry_streak` resets to 0,
   stays `active`.
3. active below the threshold → stays `active`, `dry_streak` increments.
4. trial promotion / trial retirement paths unchanged (regression).
5. `dry_streak` increment-and-reset arithmetic across a sequence of sweeps.

## Rollback

`AGF_FLYWHEEL_RETIRE_DRY_STREAK` to a large value disables retirement without a deploy
(env read at call time; set in the VM `/opt/agf/.env` + `docker compose up -d worker`).
The column is additive and harmless if unused.

## Deploy

The worker does **not** auto-migrate. Order matters:

1. Build + push: `ops/prod/02_build_image.sh`.
2. **Apply migration 143 explicitly** (RUNBOOK §6/§8 one-liner) against prod — on the VM:
   `sudo docker compose -f /opt/agf/docker-compose.yml run --rm worker python -c "from
   agf.db.engine import get_engine; from agf.db.migrate import apply_migrations;
   print(apply_migrations(get_engine()))"`. The new image must already be pulled so the
   `db/schema/143_*.sql` file is present in the container.
3. Restart: VM `pull && up -d worker cron`.

The migration is additive (one nullable-default column) and idempotent via
`public.schema_migrations`. No matview change, no API/web change.
