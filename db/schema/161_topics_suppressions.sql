-- 161: topic diversity (spec docs/superpowers/specs/2026-08-03-topic-diversity-design.md).
-- frontend.app_topics: per-advertiser embedding-cluster topic, recomputed each compute
-- cycle by src/agf/scoring/topics.py (k-means over frontend.app_embeddings). Consumed by
-- the whitespace axis (topic_size percentile) and the review-queue per-niche cap
-- ("t:<topic_id>"). Whole-corpus swap each refresh; no staleness bookkeeping.
CREATE TABLE IF NOT EXISTS frontend.app_topics (
  advertiser_id INTEGER PRIMARY KEY REFERENCES public.advertisers(id) ON DELETE CASCADE,
  topic_id      INTEGER NOT NULL,
  topic_size    INTEGER NOT NULL,
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- discovery.keyword_suppressions: static taxonomy keywords whose kept catch is
-- redundant (crowded share >= AGF_KW_REDUNDANT_SHARE with >= AGF_KW_REDUNDANT_MIN_KEPT
-- embedded kept candidates). Sweep entry builders filter these out (fail-open).
-- DELETE a row to un-suppress (founder override). Dynamic keywords are handled by
-- lifecycle retirement instead, never by this table.
CREATE TABLE IF NOT EXISTS discovery.keyword_suppressions (
  keyword       TEXT PRIMARY KEY,
  reason        TEXT NOT NULL,
  kept_n        INTEGER NOT NULL,
  crowded_share REAL NOT NULL,
  suppressed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
