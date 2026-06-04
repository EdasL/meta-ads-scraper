-- Plan 3 — core entities (schema `public`). Columns frozen per master-plan §3.5.

CREATE TABLE IF NOT EXISTS public.domains (
    id          SERIAL PRIMARY KEY,
    domain      TEXT UNIQUE NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.advertisers (
    id                 SERIAL PRIMARY KEY,
    page_id            TEXT UNIQUE NOT NULL,
    page_name          TEXT,
    domain_id          INTEGER REFERENCES public.domains(id),
    parent_company_id  INTEGER REFERENCES public.advertisers(id),   -- self-FK (disguised-incumbent)
    page_category      TEXT,
    ig_username        TEXT,
    ig_followers       INTEGER,
    first_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.apps (
    id            SERIAL PRIMARY KEY,
    store         TEXT NOT NULL,                 -- 'ios' | 'android'
    store_id      TEXT NOT NULL,
    name          TEXT,
    developer     TEXT,
    genre         TEXT,
    release_date  DATE,
    rating        REAL,
    rating_count  INTEGER,
    installs      BIGINT,
    free          BOOLEAN,
    icon_url      TEXT,
    is_emerging   BOOLEAN,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (store, store_id)
);

CREATE TABLE IF NOT EXISTS public.ads (
    id                  SERIAL PRIMARY KEY,
    external_ad_id      TEXT UNIQUE NOT NULL,
    advertiser_id       INTEGER REFERENCES public.advertisers(id),
    app_id              INTEGER REFERENCES public.apps(id),          -- NULLABLE (web funnels)
    link_url            TEXT,
    cta_type            TEXT,
    cta_text            TEXT,
    publisher_platforms TEXT[],
    is_active           BOOLEAN,
    start_date          TIMESTAMPTZ,
    end_date            TIMESTAMPTZ,
    collation_count     INTEGER,
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ads_advertiser ON public.ads(advertiser_id);

CREATE TABLE IF NOT EXISTS public.creatives (
    id                 SERIAL PRIMARY KEY,
    ad_id              INTEGER NOT NULL REFERENCES public.ads(id),
    type               TEXT,                      -- 'image' | 'video'
    s3_key             TEXT,
    content_type       TEXT,
    file_size          BIGINT,
    sha256             TEXT,
    phash              TEXT,
    body_text          TEXT,
    title              TEXT,
    snapshot_image_url TEXT,
    first_seen         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- byte-identical dedup: one row per sha256
CREATE UNIQUE INDEX IF NOT EXISTS uq_creatives_sha256 ON public.creatives(sha256) WHERE sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.funnels (
    id                      SERIAL PRIMARY KEY,
    ad_id                   INTEGER UNIQUE NOT NULL REFERENCES public.ads(id),  -- one (upserted) capture per ad
    landing_url             TEXT,
    final_url_after_redirect TEXT,
    screenshot_s3_key       TEXT,
    captured_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
