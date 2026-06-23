-- db/schema/140_advertiser_web_identity.sql
-- Web-sub funnels have no app-store listing; carry a web-sourced identity
-- (OG title/description, favicon, landing URL) and a payoff marker on the
-- advertiser so the no_app veto and the feed matview can treat them correctly.
ALTER TABLE public.advertisers
  ADD COLUMN IF NOT EXISTS web_payoff_kind  text,
  ADD COLUMN IF NOT EXISTS web_name         text,
  ADD COLUMN IF NOT EXISTS web_description  text,
  ADD COLUMN IF NOT EXISTS web_icon_url     text,
  ADD COLUMN IF NOT EXISTS web_landing_url  text;
