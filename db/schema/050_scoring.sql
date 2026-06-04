-- Plan 5 — per-advertiser scores (schema `adtech`). Feeds the product matview (051).
CREATE TABLE IF NOT EXISTS adtech.advertiser_scores (
    advertiser_id      INTEGER PRIMARY KEY REFERENCES public.advertisers(id),
    cohort             TEXT NOT NULL,
    gem_score          REAL NOT NULL,
    scaling_velocity   REAL,
    under_radar        REAL,
    funnel_intent      REAL,
    active_ad_count    INTEGER,
    max_longevity_days INTEGER,
    alert              BOOLEAN NOT NULL DEFAULT FALSE,
    vetoed             BOOLEAN NOT NULL DEFAULT FALSE,
    veto_reason        TEXT,
    scored_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
