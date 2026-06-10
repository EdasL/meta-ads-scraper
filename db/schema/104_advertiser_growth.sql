-- Per-advertiser growth summary (schema `adtech`), derived from the weekly
-- adtech.app_advertiser_metrics series by agf.tracker.growth.compute_advertiser_growth.
-- Cheap denormalized rollup so the product card can show first/latest ad counts and
-- pct change without re-aggregating the series per request. One row per advertiser; recomputed
-- in the same transaction as scoring (run_scoring), before the matview refresh.
CREATE TABLE IF NOT EXISTS adtech.advertiser_growth (
    advertiser_id  INTEGER PRIMARY KEY REFERENCES public.advertisers(id),
    first_count    INTEGER,
    latest_count   INTEGER,
    weeks_observed INTEGER,
    pct_change     REAL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
