-- Funnel keyword flywheel: let the funnel sweep grow its own vocabulary the way the
-- app sweep does, kept in a SEPARATE keyword pool so funnel trial keywords never get
-- swept into the app candidate pool.
--
-- 1. discovery.dynamic_keywords gains `kind` ('app' | 'funnel'). Existing rows are all
--    app-mined, so the default backfills them correctly. The uniqueness is now per
--    (keyword, kind) so the same phrase can live in both pools independently.
-- 2. discovery.funnel_candidates gains `body_text` — the ad copy the flywheel mines
--    for new funnel phrases (we only stored the landing/host before).

ALTER TABLE discovery.dynamic_keywords
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'app';

ALTER TABLE discovery.dynamic_keywords
    DROP CONSTRAINT IF EXISTS dynamic_keywords_keyword_key;

ALTER TABLE discovery.dynamic_keywords
    ADD CONSTRAINT dynamic_keywords_keyword_kind_key UNIQUE (keyword, kind);

ALTER TABLE discovery.funnel_candidates
    ADD COLUMN IF NOT EXISTS body_text TEXT;
