-- Game classification flag, set during store enrichment from authoritative fields
-- (iTunes primaryGenreName/genreIds, Google Play genreId GAME_*). NULL = unknown;
-- vetoes treat NULL as pass (never veto on missing data).
ALTER TABLE public.apps ADD COLUMN IF NOT EXISTS is_game BOOLEAN;
