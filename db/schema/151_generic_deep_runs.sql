-- db/schema/151_generic_deep_runs.sql
-- 151: run log for the weekly generic-deep copy-phrase sweep (spec
-- docs/superpowers/specs/2026-07-10-generic-deep-keyword-lane-design.md), sibling to
-- discovery.corpus_refresh_runs. The worker gates the stage on a FINISHED row within
-- AGF_GENERIC_DEEP_EVERY_DAYS; stats holds the stage's counter dict as jsonb.
CREATE TABLE IF NOT EXISTS discovery.generic_deep_runs (
  id          BIGSERIAL PRIMARY KEY,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  stats       JSONB,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS generic_deep_runs_started_idx
  ON discovery.generic_deep_runs (started_at);
