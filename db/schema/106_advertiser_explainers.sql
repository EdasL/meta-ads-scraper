-- LLM-generated gem explainer (schema `adtech`), grounded in the scraped ad copy +
-- app-store description. Written by the Go backend (cmd/explainers); this migration
-- only owns the schema (backend/db forbids Go DDL). One row per advertiser.
-- source_fingerprint is a sha256 of the canonical inputs so a re-run skips unchanged
-- gems instead of re-calling the model.
CREATE TABLE IF NOT EXISTS adtech.advertiser_explainers (
    advertiser_id      INTEGER PRIMARY KEY REFERENCES public.advertisers(id),
    what_it_does       TEXT NOT NULL,
    problem_solved     TEXT NOT NULL,
    why_working        TEXT NOT NULL,
    model              TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    generated_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
