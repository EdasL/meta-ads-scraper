-- Keep the ads that explain a hunt result, independently of store triage/enrich.
ALTER TABLE frontend.hunt_results
    ADD COLUMN IF NOT EXISTS ad_evidence JSONB NOT NULL DEFAULT '[]'::jsonb;
