-- db/schema/148_corpus_refresh_runs.sql
-- 148: run log for the recurring corpus-refresh stage (re-scrape found advertisers'
-- live Meta listings so GemScore's running-days/is_active stay accurate), sibling to
-- discovery.funnel_sweep_runs. The worker gates the stage on a FINISHED row per day
-- (a crashed run doesn't block the day); stats holds refresh_corpus's counter dict as
-- jsonb.
CREATE TABLE IF NOT EXISTS discovery.corpus_refresh_runs (
  id          BIGSERIAL PRIMARY KEY,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  stats       JSONB,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS corpus_refresh_runs_started_idx
  ON discovery.corpus_refresh_runs (started_at);
