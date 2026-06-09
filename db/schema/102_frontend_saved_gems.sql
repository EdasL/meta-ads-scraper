-- Saved gems (schema `frontend`): a user's watchlist. The GemScore + axes are
-- FROZEN at save time ("why it was emerging then"); the user's own tags / note /
-- stage / verdict are mutable. Keyed (user_email, advertiser_id) — one row per
-- user per gem; re-saving upserts. Identity + score columns are denormalized at
-- save time so the saved record survives even if the gem later drops out of the
-- frontend.emerging_scaling_apps matview. Mirrors frontend.review_decisions style.
CREATE TABLE IF NOT EXISTS frontend.saved_gems (
    user_email       TEXT    NOT NULL,
    advertiser_id    INTEGER NOT NULL REFERENCES public.advertisers(id),
    page_name        TEXT,
    app_name         TEXT,
    cohort           TEXT    NOT NULL DEFAULT '',
    gem_score        REAL    NOT NULL,
    scaling_velocity REAL,
    under_radar      REAL,
    funnel_intent    REAL,
    active_ad_count  INTEGER,
    tags             TEXT    NOT NULL DEFAULT '[]',  -- JSON array of strings
    note             TEXT    NOT NULL DEFAULT '',
    stage            TEXT    NOT NULL DEFAULT 'just_saw'
                       CHECK (stage IN ('just_saw', 'watching', 'looks_real', 'building')),
    verdict          TEXT    CHECK (verdict IN ('build', 'clone', 'pass', 'revisit')),
    confidence       SMALLINT CHECK (confidence BETWEEN 1 AND 5),
    saved_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_email, advertiser_id)
);

CREATE INDEX IF NOT EXISTS saved_gems_user_saved_idx
    ON frontend.saved_gems (user_email, saved_at DESC);
