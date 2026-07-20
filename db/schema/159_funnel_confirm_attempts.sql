-- 159_funnel_confirm_attempts.sql
-- Give funnel_confirm a per-candidate probe-attempt counter so a permanently
-- unprobeable landing (dead domain, 30s timeout, or an MMP app-scheme deeplink that
-- can't render in a headless browser) can be capped and dropped instead of sitting at
-- status='new' forever. Root cause of the 2026-07-12..07-20 stall: confirm selects the
-- OLDEST 50 'new' rows each run and leaves every probe failure 'new' with no cap, so a
-- band of unprobeable URLs at the head of the queue re-ran nightly and blocked all newer
-- candidates (24k backlog, 0 web_sub advertisers minted for a month). Nullable-safe:
-- existing rows default 0.
ALTER TABLE discovery.funnel_candidates
  ADD COLUMN IF NOT EXISTS confirm_attempts INTEGER NOT NULL DEFAULT 0;
