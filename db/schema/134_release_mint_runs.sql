-- db/schema/134_release_mint_runs.sql
-- 134: the App Store new-releases keyword-mint stage. discovery.release_mint_runs
-- gates the daily mint (fetch new releases per genre -> mine names -> trial keywords)
-- and records its stats, same pattern as flywheel_runs / funnel_sweep_runs. A FINISHED
-- row on a given day blocks re-running that day even when the day minted nothing.
CREATE TABLE IF NOT EXISTS discovery.release_mint_runs (
  id          BIGSERIAL PRIMARY KEY,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  stats       JSONB,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS release_mint_runs_started_idx
  ON discovery.release_mint_runs (started_at);
