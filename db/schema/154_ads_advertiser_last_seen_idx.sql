-- Composite index for the corpus-refresh producer's due query
-- (spec: docs/superpowers/specs/2026-07-13-corpus-refresh-queue-design.md).
-- corpus_due_for_refresh groups public.ads by advertiser to find each
-- advertiser's freshest last_seen; it now runs every worker tick (about 48
-- times a day) instead of once nightly, so the per-advertiser MAX(last_seen)
-- needs index support. On prod apply out-of-band with CREATE INDEX
-- CONCURRENTLY first; this file is the idempotent record for fresh DBs.
-- NOTE: applied via exec_driver_sql with NO params — never a bare percent
-- sign anywhere in this file, including comments (lesson from migration 147).
CREATE INDEX IF NOT EXISTS ix_ads_advertiser_last_seen
    ON public.ads (advertiser_id, last_seen DESC);
