-- Hunt: on-demand AI-assisted discovery (spec 2026-07-13).
-- hunts = chat thread; hunt_messages = transcript; hunt_rounds = one approved
-- run; hunt_results = one row per (round, found page/store listing).

CREATE TABLE IF NOT EXISTS frontend.hunts (
    id          BIGSERIAL PRIMARY KEY,
    user_email  TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_hunts_user ON frontend.hunts (user_email, id DESC);

CREATE TABLE IF NOT EXISTS frontend.hunt_messages (
    id          BIGSERIAL PRIMARY KEY,
    hunt_id     BIGINT NOT NULL REFERENCES frontend.hunts(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL DEFAULT '',
    proposal    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_hunt_messages_hunt ON frontend.hunt_messages (hunt_id, id);

CREATE TABLE IF NOT EXISTS frontend.hunt_rounds (
    id               BIGSERIAL PRIMARY KEY,
    hunt_id          BIGINT NOT NULL REFERENCES frontend.hunts(id) ON DELETE CASCADE,
    message_id       BIGINT REFERENCES frontend.hunt_messages(id),
    terms            JSONB NOT NULL,
    competitor_apps  JSONB NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'done', 'error')),
    progress         JSONB NOT NULL DEFAULT '{}',
    error_text       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_hunt_rounds_hunt ON frontend.hunt_rounds (hunt_id, id);

CREATE TABLE IF NOT EXISTS frontend.hunt_results (
    id                 BIGSERIAL PRIMARY KEY,
    round_id           BIGINT NOT NULL REFERENCES frontend.hunt_rounds(id) ON DELETE CASCADE,
    candidate_id       BIGINT,
    page_id            TEXT NOT NULL,
    page_name          TEXT,
    store              TEXT NOT NULL,
    store_id           TEXT NOT NULL,
    link_url           TEXT,
    source_term        TEXT NOT NULL,
    was_new            BOOLEAN NOT NULL,
    triage             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (triage IN ('pending', 'kept', 'vetoed')),
    kept_advertiser_id BIGINT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (round_id, page_id, store, store_id)
);

CREATE INDEX IF NOT EXISTS ix_hunt_results_round ON frontend.hunt_results (round_id, id);
