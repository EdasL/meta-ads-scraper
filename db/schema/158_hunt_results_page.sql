-- 158_hunt_results_page.sql
-- Record the SERP page each hunt result was first found on, so depth-yield is
-- measurable per hunt (design 2026-07-15: hunts never read past page 5 because
-- repeat-incumbent pages tripped the dry exit; from now on we measure depth).
-- Nullable: rows written before this migration carry no depth. No backfill.
ALTER TABLE frontend.hunt_results ADD COLUMN IF NOT EXISTS page INT;
