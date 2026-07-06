-- Content embedding (Gemini gemini-embedding-001 @768d, L2-normalized) of a dynamic
-- keyword phrase, cached so the redundancy gate never re-embeds a known keyword.
-- Mirrors discovery.candidate_embeddings (137).
CREATE TABLE IF NOT EXISTS discovery.dynamic_keyword_embeddings (
    keyword_id   BIGINT      PRIMARY KEY
                 REFERENCES discovery.dynamic_keywords(id) ON DELETE CASCADE,
    embedding    REAL[]      NOT NULL,
    content_hash TEXT        NOT NULL,
    dim          INT         NOT NULL,
    model        TEXT        NOT NULL DEFAULT '',
    embedded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
