-- Accounts + personal API keys for the hosted Field Desk MCP.
-- Web login reads frontend.users (replaces the AUTH_USERS env seed).
-- api_keys.key_hash = SHA-256 hex of the full plaintext key ("fd_..."), which is
-- shown exactly once at mint time and never stored.
CREATE TABLE IF NOT EXISTS frontend.users (
    email         TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,               -- bcrypt
    name          TEXT,
    role          TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS frontend.api_keys (
    key_hash     TEXT PRIMARY KEY,
    user_email   TEXT NOT NULL REFERENCES frontend.users(email) ON DELETE CASCADE,
    label        TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON frontend.api_keys (user_email);
