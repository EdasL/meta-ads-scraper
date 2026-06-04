-- Plan 3 — metrics (schema `adtech`). Composite PKs frozen per master-plan §3.5.

CREATE TABLE IF NOT EXISTS adtech.ad_metrics (
    ad_id          INTEGER NOT NULL REFERENCES public.ads(id),
    snapshot_date  DATE NOT NULL,
    eu_total_reach BIGINT,
    active         BOOLEAN,
    PRIMARY KEY (ad_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS adtech.app_advertiser_metrics (
    advertiser_id          INTEGER NOT NULL REFERENCES public.advertisers(id),
    week_start             DATE NOT NULL,
    active_ad_count        INTEGER,
    distinct_creative_count INTEGER,
    total_eu_reach         BIGINT,
    PRIMARY KEY (advertiser_id, week_start)
);
