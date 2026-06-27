-- 143: dry_streak counter for active-keyword retirement (flywheel efficiency).
-- Counts consecutive 0-new sweeps; an ACTIVE keyword retires once the streak crosses
-- AGF_FLYWHEEL_RETIRE_DRY_STREAK so the flywheel stops re-walking harvested basins.
-- Additive and idempotent; existing actives start at 0 and retire naturally.
ALTER TABLE discovery.dynamic_keywords
  ADD COLUMN IF NOT EXISTS dry_streak INTEGER NOT NULL DEFAULT 0;
