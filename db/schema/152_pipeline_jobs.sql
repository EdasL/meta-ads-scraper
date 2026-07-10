-- public.pipeline_jobs — the continuous-enrich work queue (spec:
-- docs/superpowers/specs/2026-07-10-pipeline-jobs-continuous-enrich-design.md).
-- A discovered gem is ENQUEUED here by the worker tick and drained by the
-- `pipeline` consumer daemon, decoupling enrich execution from the ~40-min loop.
-- NOTE: applied via exec_driver_sql with NO params — never a bare percent sign;
-- use make_interval for intervals (lesson from migration 147).
CREATE TABLE IF NOT EXISTS public.pipeline_jobs (
    id            bigserial PRIMARY KEY,
    kind          text        NOT NULL,               -- 'enrich' (future: score, embed, …)
    advertiser_id bigint,                              -- the entity (null-ok for future payload kinds)
    payload       jsonb       NOT NULL DEFAULT '{}',
    status        text        NOT NULL DEFAULT 'queued', -- queued|running|done|error
    run_after     timestamptz NOT NULL DEFAULT now(),
    attempts      int         NOT NULL DEFAULT 0,
    max_attempts  int         NOT NULL DEFAULT 3,
    lease_expires_at timestamptz,
    error_text    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    claimed_at    timestamptz,
    finished_at   timestamptz
);
CREATE INDEX IF NOT EXISTS pipeline_jobs_claim_idx
    ON public.pipeline_jobs (kind, run_after) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS pipeline_jobs_lease_idx
    ON public.pipeline_jobs (lease_expires_at) WHERE status = 'running';
-- at most ONE active job per (kind, advertiser) — the enqueue-dedup arbiter
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_jobs_active_uq
    ON public.pipeline_jobs (kind, advertiser_id) WHERE status IN ('queued','running');
-- churn table: keep autovacuum aggressive so the partial indexes don't bloat
ALTER TABLE public.pipeline_jobs SET (autovacuum_vacuum_scale_factor = 0.02,
                                      autovacuum_analyze_scale_factor = 0.02);
