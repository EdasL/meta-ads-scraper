-- 160_saved_groups.sql — user-defined board columns for /saved.
-- Groups replace the fixed stage columns on the board; saved_gems.stage stays
-- (legacy readers: hosted MCP my_saved_gems).

CREATE TABLE IF NOT EXISTS frontend.saved_groups (
    id         BIGSERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    name       TEXT NOT NULL,
    position   INT  NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_saved_groups_user
    ON frontend.saved_groups (user_email, position);

ALTER TABLE frontend.saved_gems
    ADD COLUMN IF NOT EXISTS group_id BIGINT
        REFERENCES frontend.saved_groups(id) ON DELETE SET NULL;

-- One-time backfill: each existing user gets 4 groups mirroring the stage
-- pipeline, and every saved gem lands in the group matching its stage.
-- Idempotent: skips users who already have groups.
WITH stage_names(stage, name, position) AS (
    VALUES ('just_saw', 'Just saw', 0),
           ('watching', 'Watching', 1),
           ('looks_real', 'Looks real', 2),
           ('building', 'Building', 3)
),
users AS (
    SELECT DISTINCT user_email FROM frontend.saved_gems
    WHERE user_email NOT IN (SELECT DISTINCT user_email FROM frontend.saved_groups)
)
INSERT INTO frontend.saved_groups (user_email, name, position)
SELECT u.user_email, s.name, s.position FROM users u CROSS JOIN stage_names s;

-- CTEs don't span statements; the UPDATE re-joins through a VALUES mapping.
UPDATE frontend.saved_gems sg
SET group_id = gr.id
FROM frontend.saved_groups gr,
     (VALUES ('just_saw', 'Just saw'),
             ('watching', 'Watching'),
             ('looks_real', 'Looks real'),
             ('building', 'Building')) AS m(stage, name)
WHERE sg.group_id IS NULL
  AND sg.stage = m.stage
  AND gr.user_email = sg.user_email
  AND gr.name = m.name;
