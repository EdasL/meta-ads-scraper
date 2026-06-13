-- db/schema/121_funnel_quiz_steps.sql
-- 121: quiz click-probe evidence on funnel candidates (quiz detection, 2026-06-12).
-- quiz_steps = distinct question screens the confirm-stage probe observed (landing
-- included); NULL = not probed (legacy rows or a probe failure). >= 2 confirms a
-- real multi-step quiz even when the landing page shows no payoff tech.
ALTER TABLE discovery.funnel_candidates
    ADD COLUMN IF NOT EXISTS quiz_steps SMALLINT;
