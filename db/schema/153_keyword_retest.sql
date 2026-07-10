-- discovery.dynamic_keywords.retested_at — stamps a deep-retest re-admission
-- (spec: docs/superpowers/specs/2026-07-10-parallel-flywheel-design.md).
-- A retired keyword judged only under pre-2026-07-08 shallow paging is
-- re-admitted as a trial exactly once; a non-null retested_at excludes it
-- from any future retest wave.
-- NOTE: applied via exec_driver_sql with NO params — never a bare percent
-- sign anywhere in this file, including comments (lesson from migration 147).
ALTER TABLE discovery.dynamic_keywords
    ADD COLUMN IF NOT EXISTS retested_at timestamptz;
