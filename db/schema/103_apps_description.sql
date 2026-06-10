-- App-store long description (iTunes Lookup / google-play sidecar). Source material
-- for the Gemini explainer (what the app does / problem solved). Nullable; capped to
-- ~1500 chars on write so a single long listing can't bloat the row.
ALTER TABLE public.apps ADD COLUMN IF NOT EXISTS description TEXT;
