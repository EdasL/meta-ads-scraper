-- Denormalize the app store icon onto saved gems, captured at save time (like
-- the identity + score columns in 102): a saved gem keeps its icon even if the
-- advertiser later drops out of the frontend.emerging_scaling_apps matview.
ALTER TABLE frontend.saved_gems ADD COLUMN IF NOT EXISTS icon_url TEXT;
