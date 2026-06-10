-- App Store preview assets (S5): screenshots + price label.
-- icon_url and description already exist (045/103); this completes the preview set.
ALTER TABLE public.apps
    ADD COLUMN IF NOT EXISTS screenshots JSONB,
    ADD COLUMN IF NOT EXISTS price_label TEXT;
