-- Advertiser-level Meta signals (S1/S8):
--   page_like_count        — snapshot.page_like_count from Meta GraphQL (incumbent-size signal)
--   meta_active_ads        — true active-ad total counted via view_all_page_id bounded
--                            pagination (the stored-ads count is a known undercount)
--   meta_active_ads_capped — TRUE when pagination hit the cap (count is "cap+", incumbent veto)
ALTER TABLE public.advertisers
    ADD COLUMN IF NOT EXISTS page_like_count            BIGINT,
    ADD COLUMN IF NOT EXISTS meta_active_ads            INTEGER,
    ADD COLUMN IF NOT EXISTS meta_active_ads_capped     BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS meta_active_ads_checked_at TIMESTAMPTZ;
