-- 162_saved_group_members.sql — folders become playlists (multi-membership).
-- A saved gem may live in many folders at once, so membership moves out of
-- saved_gems.group_id (one folder per gem) into its own table. The group_id
-- column stays behind as legacy, like stage: no longer read or written.
--
-- user_email is denormalised onto the membership row so a folder's contents can
-- be scoped to their owner without joining saved_groups on every read.
-- advertiser_id carries no FK, matching frontend.saved_gems.

CREATE TABLE IF NOT EXISTS frontend.saved_group_members (
    user_email    TEXT        NOT NULL,
    group_id      BIGINT      NOT NULL REFERENCES frontend.saved_groups(id) ON DELETE CASCADE,
    advertiser_id BIGINT      NOT NULL,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, advertiser_id)
);

-- Reading a user's memberships goes gem-first ("which folders is this in?"),
-- which the PK (group-first) cannot serve.
CREATE INDEX IF NOT EXISTS ix_saved_group_members_user
    ON frontend.saved_group_members (user_email, advertiser_id);

-- One-time backfill: every gem currently filed under a folder becomes a
-- membership row. Idempotent via the PK — a re-run adds nothing.
INSERT INTO frontend.saved_group_members (user_email, group_id, advertiser_id)
SELECT sg.user_email, sg.group_id, sg.advertiser_id
FROM frontend.saved_gems sg
WHERE sg.group_id IS NOT NULL
ON CONFLICT (group_id, advertiser_id) DO NOTHING;
