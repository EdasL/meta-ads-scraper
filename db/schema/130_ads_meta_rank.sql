-- meta_rank: an ad's position in Meta's impressions-ordered per-advertiser listing.
-- The Ad Library's per-page (view_all_page_id) query returns ads sorted "Impressions:
-- high to low" by default (browser-verified: the GraphQL request sends sortData=null and
-- the UI labels that order as impressions-desc). Our scraper already fetches in this order;
-- meta_rank records the 1-based position so the dossier can show the highest-impression
-- ads first. NULL until an advertiser is (re-)scraped via refresh_one_advertiser.
ALTER TABLE public.ads ADD COLUMN IF NOT EXISTS meta_rank INTEGER;
