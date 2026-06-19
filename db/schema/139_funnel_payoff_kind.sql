ALTER TABLE discovery.funnel_candidates
  ADD COLUMN IF NOT EXISTS payoff_kind TEXT;
