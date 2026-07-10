-- 150: embedding-space crowding score, stored per embedding row and recomputed
-- every cycle by the worker (src/agf/discovery/crowding_refresh.py). crowding =
-- mean cosine similarity to the k nearest neighbors in the kept-candidate / app
-- corpus (self excluded). The crowded tail (>= AGF_CROWDING_TAIL, 0.80) is where
-- like-rate collapses (measured 2026-07-10: 2.8 vs 8-12 pct), so the penalty gates
-- promotion, enrich priority, and the review queue on it. NULL = not yet computed
-- = degrade open (never crowded). No index: always read row-wise with the embedding.
ALTER TABLE discovery.candidate_embeddings ADD COLUMN IF NOT EXISTS crowding real;
ALTER TABLE frontend.app_embeddings       ADD COLUMN IF NOT EXISTS crowding real;
