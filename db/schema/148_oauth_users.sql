-- OAuth-capable accounts: password becomes optional (Google/GitHub users have
-- none), the identity provider is recorded, and onboarding is tracked.
-- Existing users are backfilled as already onboarded so they are never
-- funneled into the new-user flow.
ALTER TABLE frontend.users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE frontend.users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'password';
ALTER TABLE frontend.users ADD CONSTRAINT users_auth_provider_check
    CHECK (auth_provider IN ('password', 'google', 'github'));
ALTER TABLE frontend.users ADD COLUMN avatar_url TEXT;
ALTER TABLE frontend.users ADD COLUMN onboarded_at TIMESTAMPTZ;
UPDATE frontend.users SET onboarded_at = now();
