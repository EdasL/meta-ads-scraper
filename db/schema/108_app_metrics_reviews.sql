-- Per-app metric snapshots (S6): the app-side growth series, one row per app per day.
-- installs = Google Play maxInstalls (near-exact live count); NULL for iOS (Apple
-- publishes no download counts — rating_count delta is the iOS trend proxy).
CREATE TABLE IF NOT EXISTS adtech.app_metrics (
    app_id        INTEGER NOT NULL REFERENCES public.apps(id) ON DELETE CASCADE,
    snapshot_date DATE    NOT NULL,
    rating        REAL,
    rating_count  BIGINT,
    installs      BIGINT,
    PRIMARY KEY (app_id, snapshot_date)
);

-- Recent store reviews per app (iOS customer-reviews RSS / google-play-scraper).
CREATE TABLE IF NOT EXISTS public.app_reviews (
    app_id      INTEGER NOT NULL REFERENCES public.apps(id) ON DELETE CASCADE,
    review_id   TEXT    NOT NULL,
    author      TEXT,
    rating      SMALLINT,
    title       TEXT,
    content     TEXT,
    reviewed_at TIMESTAMPTZ,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (app_id, review_id)
);

CREATE INDEX IF NOT EXISTS app_reviews_app_recent
    ON public.app_reviews (app_id, reviewed_at DESC);
