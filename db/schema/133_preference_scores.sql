-- db/schema/133_preference_scores.sql
-- Per-user content-based preference scores, written by scripts/score_preferences.py.
-- p_like = P(user likes this app) from a statsmodels Logit fit on app_ratings;
-- uncertainty = 1 - |2*p_like - 1| (peaks at p_like = 0.5); queue_rank = the
-- precomputed 75/25 exploit/explore review order; why = short "why shown" string.
CREATE TABLE IF NOT EXISTS frontend.preference_scores (
    user_email    TEXT   NOT NULL,
    advertiser_id BIGINT NOT NULL,
    p_like        DOUBLE PRECISION NOT NULL,
    uncertainty   DOUBLE PRECISION NOT NULL,
    queue_rank    INTEGER NOT NULL,
    why           TEXT   NOT NULL DEFAULT '',
    model_version TEXT   NOT NULL DEFAULT '',
    scored_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_email, advertiser_id)
);
CREATE INDEX IF NOT EXISTS preference_scores_user_rank_idx
    ON frontend.preference_scores (user_email, queue_rank);
