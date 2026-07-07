-- 147: adaptive sweep lifecycle — a retired keyword is no longer a dead end.
-- A dud keyword that once yielded candidates can REST and re-enter the sweep after a
-- cooldown instead of being permanently retired. next_eligible_at gates when a resting
-- keyword may be re-swept; rest_count tracks how many cooldowns it has served.
-- candidates.first_seen_depth records the pagination depth at which the sweep first saw
-- a candidate (feeds the adaptive-depth heuristic). Additive and idempotent.
ALTER TABLE discovery.dynamic_keywords
  ADD COLUMN IF NOT EXISTS next_eligible_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS rest_count       INTEGER NOT NULL DEFAULT 0;

-- Extend the status CHECK (created in 136) to admit the 'resting' cooldown state.
ALTER TABLE discovery.dynamic_keywords DROP CONSTRAINT IF EXISTS dynamic_keywords_status_check;
ALTER TABLE discovery.dynamic_keywords ADD CONSTRAINT dynamic_keywords_status_check
    CHECK (status = ANY (ARRAY['pending','trial','active','retired','resting']));

ALTER TABLE discovery.candidates
  ADD COLUMN IF NOT EXISTS first_seen_depth INTEGER;

-- Re-activate retired keywords that ever produced candidates: rest them now so the
-- cooldown gate lets the sweep re-try their basins instead of leaving them dead.
UPDATE discovery.dynamic_keywords
   SET status = 'resting', next_eligible_at = now(), rest_count = 0
 WHERE status = 'retired' AND new_candidates_total > 0;
