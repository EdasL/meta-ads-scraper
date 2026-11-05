-- Ad-first research, isolated from app discovery and scoped to the requesting user.
CREATE TABLE IF NOT EXISTS frontend.ad_searches (
    id BIGSERIAL PRIMARY KEY,
    created_by TEXT NOT NULL,
    request JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'error')),
    progress JSONB NOT NULL DEFAULT '{}',
    error_text TEXT,
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE (created_by, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_ad_searches_owner
    ON frontend.ad_searches (created_by, id DESC);

CREATE TABLE IF NOT EXISTS frontend.ad_search_results (
    id BIGSERIAL PRIMARY KEY,
    search_id BIGINT NOT NULL REFERENCES frontend.ad_searches(id) ON DELETE CASCADE,
    ad_archive_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    page_name TEXT NOT NULL,
    source_term TEXT NOT NULL,
    country TEXT NOT NULL,
    is_active BOOLEAN NOT NULL,
    start_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    collation_count INTEGER NOT NULL DEFAULT 1,
    page_like_count BIGINT,
    cta_type TEXT,
    cta_text TEXT,
    link_url TEXT,
    body_text TEXT,
    image_urls JSONB NOT NULL DEFAULT '[]',
    video_urls JSONB NOT NULL DEFAULT '[]',
    creative_hash TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (search_id, ad_archive_id)
);
CREATE INDEX IF NOT EXISTS ix_ad_search_results_order
    ON frontend.ad_search_results (search_id, id);
