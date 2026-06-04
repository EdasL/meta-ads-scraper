-- Plan 3 — DB-as-queue staleness log (schema `logging`). Index frozen per master-plan §3.5.

CREATE TABLE IF NOT EXISTS logging.crawl_log (
    id           BIGSERIAL PRIMARY KEY,
    entity_type  TEXT NOT NULL,
    entity_id    INTEGER NOT NULL,
    crawled_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    crawl_result TEXT
);
CREATE INDEX IF NOT EXISTS ix_crawl_log_entity
    ON logging.crawl_log (entity_type, entity_id, crawled_at DESC);
