-- Per-app content embedding (Gemini gemini-embedding-001 @768d, L2-normalized),
-- written by scripts/embed_apps.py. REAL[] (not pgvector) — numpy at read time,
-- ~1k apps. content_hash gates re-embedding to changed content only.
CREATE TABLE IF NOT EXISTS frontend.app_embeddings (
    advertiser_id BIGINT PRIMARY KEY,
    embedding     REAL[]      NOT NULL,
    content_hash  TEXT        NOT NULL,
    dim           INT         NOT NULL,
    model         TEXT        NOT NULL DEFAULT '',
    embedded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
