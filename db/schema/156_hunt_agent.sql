-- Peon agent tools (spec 2026-07-13-peon-agent-tools): per-round scrape
-- settings + assistant tool trace.
ALTER TABLE frontend.hunt_rounds
    ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL DEFAULT '{}';
ALTER TABLE frontend.hunt_messages
    ADD COLUMN IF NOT EXISTS tool_log JSONB;
