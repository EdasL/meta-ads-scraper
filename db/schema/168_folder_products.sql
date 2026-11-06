-- The researched product is distinct from competitors saved inside the folder.
ALTER TABLE frontend.saved_groups
    ADD COLUMN IF NOT EXISTS product TEXT NOT NULL DEFAULT ''
    CHECK (char_length(product) <= 800);

ALTER TABLE frontend.hunts
    ADD COLUMN IF NOT EXISTS folder_id BIGINT
    REFERENCES frontend.saved_groups(id) ON DELETE SET NULL;

-- Shared evidence shape for the Go planner and Python judge. Every caller
-- filters by the authenticated owner's email and the selected folder ID.
CREATE OR REPLACE VIEW frontend.folder_reference_brands AS
SELECT gr.id AS folder_id, gr.user_email, sg.advertiser_id,
       COALESCE(NULLIF(sg.app_name, ''), sg.page_name, '') AS name,
       left(sg.note, 800) AS note,
       COALESCE((
           SELECT left(a.description, 1200)
           FROM frontend.emerging_scaling_apps m
           JOIN public.apps a ON a.id = m.app_id
           WHERE m.advertiser_id = sg.advertiser_id
           ORDER BY a.id LIMIT 1
       ), '') AS description,
       COALESCE((
           SELECT jsonb_agg(sample.body_text) FROM (
               SELECT DISTINCT left(c.body_text, 1200) AS body_text
               FROM public.ads ad JOIN public.creatives c ON c.ad_id = ad.id
               WHERE ad.advertiser_id = sg.advertiser_id
                 AND NULLIF(trim(c.body_text), '') IS NOT NULL
               ORDER BY body_text LIMIT 2
           ) sample
       ), '[]'::jsonb) AS ad_copy,
       gm.added_at
FROM frontend.saved_groups gr
JOIN frontend.saved_group_members gm ON gm.group_id = gr.id AND gm.user_email = gr.user_email
JOIN frontend.saved_gems sg ON sg.advertiser_id = gm.advertiser_id AND sg.user_email = gr.user_email;
