-- db/schema/120_funnel_candidates.sql
-- 120: web-funnel discovery pool (docs/research/2026-06-11-funnel-discovery-method.md).
-- Sibling to discovery.candidates (apps). store/store_id are NULLABLE here — a
-- web2subscribe funnel may have no app. Keyed on (page_id, landing_domain): one
-- advertiser can run several funnels. status: new -> confirmed|dropped (the confirm
-- stage, a later milestone, sets kind=install|subscribe and the store columns).
CREATE TABLE IF NOT EXISTS discovery.funnel_candidates (
  id              BIGSERIAL PRIMARY KEY,
  page_id         TEXT NOT NULL,
  page_name       TEXT,
  page_like_count INTEGER,
  landing_url     TEXT,
  landing_domain  TEXT NOT NULL,
  funnel_host     TEXT,
  cta_type        TEXT,
  utm_source      TEXT,
  utm_medium      TEXT,
  utm_campaign    TEXT,
  utm_content     TEXT,
  ad_start_date   DATE,
  source_keyword  TEXT,
  technique       TEXT,
  kind            TEXT NOT NULL DEFAULT 'unknown' CHECK (kind IN ('unknown','install','subscribe')),
  store           TEXT CHECK (store IN ('ios','android')),
  store_id        TEXT,
  times_seen      INTEGER NOT NULL DEFAULT 1,
  first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  status          TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','confirmed','dropped')),
  drop_reason     TEXT,
  confirmed_at    TIMESTAMPTZ,
  UNIQUE (page_id, landing_domain)
);
CREATE INDEX IF NOT EXISTS funnel_candidates_status_idx ON discovery.funnel_candidates (status);
CREATE INDEX IF NOT EXISTS funnel_candidates_domain_idx ON discovery.funnel_candidates (landing_domain);
