-- Content embedding (Gemini gemini-embedding-001 @768d, L2-normalized) of a kept
-- candidate's FETCHED store listing (name + genre + description), written by
-- scripts/embed_candidates.py. The triage prior cosine-ranks these vs the user's
-- liked centroid to prioritize the enrich budget. CASCADE so it dies with the candidate.
CREATE TABLE IF NOT EXISTS discovery.candidate_embeddings (
    candidate_id BIGINT PRIMARY KEY
        REFERENCES discovery.candidates(id) ON DELETE CASCADE,
    embedding    REAL[]      NOT NULL,
    content_hash TEXT        NOT NULL,
    dim          INT         NOT NULL,
    model        TEXT        NOT NULL DEFAULT '',
    embedded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
