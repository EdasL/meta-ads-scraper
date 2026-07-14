-- Hunt round intent + per-result relevance verdicts.
-- intent: one founder-facing sentence the round's results are judged against.
-- relevance: NULL (unjudged, treated as relevant) | 'relevant' | 'off_target'.
ALTER TABLE frontend.hunt_rounds
    ADD COLUMN IF NOT EXISTS intent TEXT;
ALTER TABLE frontend.hunt_results
    ADD COLUMN IF NOT EXISTS relevance TEXT,
    ADD COLUMN IF NOT EXISTS relevance_note TEXT;
