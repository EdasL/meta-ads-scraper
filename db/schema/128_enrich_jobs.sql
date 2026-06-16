-- On-demand single-app enrichment jobs. One row per "Refresh data" click.
-- Go inserts a 'running' row and spawns a Python subprocess; the subprocess
-- updates the row to 'done'/'error'. The API reads the latest row per
-- advertiser for live status polling.
CREATE TABLE IF NOT EXISTS public.enrich_jobs (
    id            bigserial PRIMARY KEY,
    advertiser_id bigint      NOT NULL,
    status        text        NOT NULL DEFAULT 'running',  -- running | done | error
    error_text    text,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);

CREATE INDEX IF NOT EXISTS enrich_jobs_adv_started_idx
    ON public.enrich_jobs (advertiser_id, started_at DESC);
