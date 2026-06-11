-- 116: GemScore v2 (spec docs/superpowers/specs/2026-06-11-gemscore-v2-design.md).
-- New axes on adtech.advertiser_scores plus their evidence columns: the raw
-- 28-day traction deltas (shown verbatim in the UI — never re-derived) and the
-- niche lineage behind the whitespace axis. All nullable: missing data floors
-- the axis to neutral, it never blocks a score row.
ALTER TABLE adtech.advertiser_scores
    ADD COLUMN IF NOT EXISTS demand REAL,
    ADD COLUMN IF NOT EXISTS whitespace REAL,
    ADD COLUMN IF NOT EXISTS niche_keyword TEXT,
    ADD COLUMN IF NOT EXISTS niche_advertiser_count INTEGER,
    ADD COLUMN IF NOT EXISTS installs_delta_28d BIGINT,
    ADD COLUMN IF NOT EXISTS rating_count_delta_28d BIGINT;
