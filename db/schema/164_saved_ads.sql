-- 164_saved_ads.sql — per-user saved ads (the ad-level sibling of saved_gems).
-- Snapshot columns are denormalized so a saved ad still renders if the
-- underlying public.ads row is pruned or re-imported; media_s3_key points at
-- our stored media (Meta CDN URLs expire). No FKs for the same reason.
CREATE TABLE IF NOT EXISTS frontend.saved_ads (
    user_email      TEXT NOT NULL,
    external_ad_id  TEXT NOT NULL,
    advertiser_id   BIGINT,
    page_name       TEXT,
    app_name        TEXT,
    title           TEXT,
    body_text       TEXT,
    media_type      TEXT,
    media_s3_key    TEXT,
    link_url        TEXT,
    cta_text        TEXT,
    ad_started_on   DATE,
    ad_active       BOOLEAN,
    saved_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_email, external_ad_id)
);

CREATE INDEX IF NOT EXISTS saved_ads_user_saved_idx
    ON frontend.saved_ads (user_email, saved_at DESC);
