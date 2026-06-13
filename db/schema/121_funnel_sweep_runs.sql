-- db/schema/121_funnel_sweep_runs.sql
-- 121: run log for the daily funnel stage (sweep + confirm), sibling to
-- discovery.sweep_runs (apps). The worker gates the funnel stage on a FINISHED
-- row per day (a crashed run doesn't block the day); stats holds the sweep +
-- confirm counter dicts as jsonb.
CREATE TABLE IF NOT EXISTS discovery.funnel_sweep_runs (
  id          BIGSERIAL PRIMARY KEY,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  stats       JSONB,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS funnel_sweep_runs_started_idx
  ON discovery.funnel_sweep_runs (started_at);
