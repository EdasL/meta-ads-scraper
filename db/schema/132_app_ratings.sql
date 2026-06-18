-- Per-user like/neutral/dislike + comment on a directory app. A labeled dataset
-- Claude analyzes to tune GemScore / vetoes / scraping. features = a snapshot of the
-- app's scoring signals at rating time (scoring runs 24/7, so the live row drifts).
CREATE TABLE IF NOT EXISTS frontend.app_ratings (
    user_email    TEXT        NOT NULL,
    advertiser_id BIGINT      NOT NULL,
    rating        TEXT        NOT NULL CHECK (rating IN ('like','neutral','dislike')),
    comment       TEXT        NOT NULL DEFAULT '',
    features      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    rated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_email, advertiser_id)
);
CREATE INDEX IF NOT EXISTS idx_app_ratings_rating ON frontend.app_ratings (rating);
