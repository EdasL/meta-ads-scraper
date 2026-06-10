-- 112: breadth-first discovery pool (spec 2026-06-10-breadth-first-discovery-design)
-- discovery.candidates: every app-linked advertiser the sweep has ever seen, with a
-- triage status that never regresses. discovery.sweep_runs: one audit row per daily run.
CREATE SCHEMA IF NOT EXISTS discovery;

CREATE TABLE IF NOT EXISTS discovery.candidates (
  id                 BIGSERIAL PRIMARY KEY,
  page_id            TEXT NOT NULL,
  page_name          TEXT,
  page_like_count    INTEGER,
  store              TEXT NOT NULL CHECK (store IN ('ios','android')),
  store_id           TEXT NOT NULL,
  link_url           TEXT,
  cta_type           TEXT,
  ad_start_date      DATE,
  source_keyword     TEXT,
  technique          TEXT,
  times_seen         INTEGER NOT NULL DEFAULT 1,
  first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  status             TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','kept','dropped')),
  drop_reason        TEXT,
  triaged_at         TIMESTAMPTZ,
  kept_advertiser_id BIGINT REFERENCES public.advertisers(id),
  UNIQUE (page_id, store, store_id)
);
CREATE INDEX IF NOT EXISTS candidates_status_idx ON discovery.candidates (status);

CREATE TABLE IF NOT EXISTS discovery.sweep_runs (
  id                 BIGSERIAL PRIMARY KEY,
  started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at        TIMESTAMPTZ,
  keywords_swept     INTEGER NOT NULL DEFAULT 0,
  serp_requests      INTEGER NOT NULL DEFAULT 0,
  candidates_new     INTEGER NOT NULL DEFAULT 0,
  candidates_seen    INTEGER NOT NULL DEFAULT 0,
  triaged_kept       INTEGER NOT NULL DEFAULT 0,
  triaged_dropped    INTEGER NOT NULL DEFAULT 0,
  drop_reasons       JSONB,
  enrich_requests    INTEGER NOT NULL DEFAULT 0,
  enriched           INTEGER NOT NULL DEFAULT 0,
  error              TEXT
);
