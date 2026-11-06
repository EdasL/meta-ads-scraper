CREATE TABLE IF NOT EXISTS frontend.research_products (
    id BIGSERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '' CHECK (char_length(description) <= 2000),
    page_id TEXT NOT NULL DEFAULT '',
    store TEXT NOT NULL DEFAULT '',
    store_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_email, identity_key)
);
ALTER TABLE frontend.saved_groups ADD COLUMN IF NOT EXISTS product_id BIGINT
    REFERENCES frontend.research_products(id) ON DELETE SET NULL;
ALTER TABLE frontend.hunts ADD COLUMN IF NOT EXISTS product_id BIGINT
    REFERENCES frontend.research_products(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_saved_groups_product ON frontend.saved_groups(product_id);
CREATE INDEX IF NOT EXISTS ix_ads_app ON public.ads(app_id) WHERE app_id IS NOT NULL;

-- Preserve descriptions entered before products were first-class records.
INSERT INTO frontend.research_products(user_email, identity_key, name, description)
SELECT user_email, 'legacy-folder:' || id, left(product, 200), product
FROM frontend.saved_groups WHERE trim(product) <> '' AND product_id IS NULL
ON CONFLICT (user_email, identity_key) DO NOTHING;
UPDATE frontend.saved_groups g SET product_id=p.id
FROM frontend.research_products p
WHERE p.user_email=g.user_email AND p.identity_key='legacy-folder:' || g.id
  AND g.product_id IS NULL;
UPDATE frontend.hunts h SET product_id=g.product_id
FROM frontend.saved_groups g WHERE g.id=h.folder_id AND g.user_email=h.user_email
  AND h.product_id IS NULL;

-- Product evidence remains live. Private MCP captures are visible only to their
-- owner; shared public discovery ads are available to all product owners.
CREATE OR REPLACE VIEW frontend.research_product_context AS
SELECT p.*, adv.id AS advertiser_id,
       COALESCE(NULLIF(adv.web_description,''), (
           SELECT a.description FROM public.apps a
           WHERE a.store=p.store AND a.store_id=p.store_id LIMIT 1
       ), (
           SELECT a.description FROM public.ads ad JOIN public.apps a ON a.id=ad.app_id
           WHERE ad.advertiser_id=adv.id ORDER BY a.updated_at DESC LIMIT 1
       ), '') AS source_description,
       COALESCE((SELECT jsonb_agg(sample.body_text) FROM (
           SELECT DISTINCT left(e.body_text, 1600) AS body_text FROM (
               SELECT c.body_text FROM public.ads ad JOIN public.creatives c ON c.ad_id=ad.id
               WHERE ad.advertiser_id=adv.id
               UNION ALL
               SELECT c.body_text FROM public.apps a
               JOIN public.ads ad ON ad.app_id=a.id JOIN public.creatives c ON c.ad_id=ad.id
               WHERE p.store<>'' AND a.store=p.store AND a.store_id=p.store_id
               UNION ALL
               SELECT r.body_text FROM frontend.ad_search_results r
               JOIN frontend.ad_searches s ON s.id=r.search_id
               WHERE s.created_by=p.user_email AND p.page_id<>'' AND r.page_id=p.page_id
           ) e WHERE NULLIF(trim(e.body_text),'') IS NOT NULL
           ORDER BY body_text LIMIT 3
       ) sample), '[]'::jsonb) AS ad_copy
FROM frontend.research_products p
LEFT JOIN public.advertisers adv ON adv.page_id=p.page_id;
