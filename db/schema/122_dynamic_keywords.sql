-- db/schema/122_dynamic_keywords.sql
-- 122: the keyword flywheel. discovery.dynamic_keywords holds sweep keywords mined
-- from the ad copy of scored gems (taxonomy.py is the static layer; this is the
-- self-expanding one). Lifecycle: trial -> active (earned its keep) | retired (dud,
-- never re-tried). discovery.flywheel_runs gates the daily mining+sweep stage and
-- records its stats, same pattern as funnel_sweep_runs.
CREATE TABLE IF NOT EXISTS discovery.dynamic_keywords (
  id                   BIGSERIAL PRIMARY KEY,
  keyword              TEXT NOT NULL UNIQUE,
  source               TEXT NOT NULL DEFAULT 'gem_copy',
  status               TEXT NOT NULL DEFAULT 'trial'
                       CHECK (status IN ('trial','active','retired')),
  sweeps_used          INTEGER NOT NULL DEFAULT 0,
  new_candidates_total INTEGER NOT NULL DEFAULT 0,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_swept_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS dynamic_keywords_status_idx
  ON discovery.dynamic_keywords (status);

CREATE TABLE IF NOT EXISTS discovery.flywheel_runs (
  id          BIGSERIAL PRIMARY KEY,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  stats       JSONB,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS flywheel_runs_started_idx
  ON discovery.flywheel_runs (started_at);
