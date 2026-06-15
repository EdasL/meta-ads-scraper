-- Persist the funnel VERDICT on the captured row. Until now public.funnels
-- stored only the URL + screenshot, so the directory's "Quiz" link surfaced
-- ANY web landing an ad pointed at — homepages, deep app-links, store bounces.
-- The classifier (discovery.funnel_confirm.classify_funnel) already knows how
-- to tell a real quiz/paywall flow from a homepage; we just never wrote its
-- answer down. These two columns carry it so the matview can gate on it.
--
--   kind:       'install' | 'subscribe' | 'unknown' | NULL
--               install   = bounces to App/Play store (NOT a funnel)
--               subscribe = checkout/paywall present
--               unknown   = verified quiz (>=2 question screens) or paid-traffic
--                           funnel host; payoff sits behind it
--               NULL      = legacy rows captured before this gate existed
--   quiz_steps: distinct question screens the probe observed (0 = no quiz)

ALTER TABLE public.funnels
    ADD COLUMN IF NOT EXISTS kind       text,
    ADD COLUMN IF NOT EXISTS quiz_steps integer;
