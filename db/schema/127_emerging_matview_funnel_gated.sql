-- Gate the directory's "Quiz" link on a REAL funnel verdict.
--
-- 125 surfaced funnel_url by host-excluding stores/social, but it still linked
-- ANY remaining web landing — homepages and deep app-links slipped through,
-- so most "Quiz" links didn't open a quiz. 126 added funnels.kind/quiz_steps;
-- here we require the captured row to be a classified funnel:
--
--   kind IN ('subscribe','unknown')  -- checkout/paywall, or a verified quiz
--
-- 'install' (store bounce) and NULL (legacy, unverified) are excluded. Legacy
-- rows therefore show NO funnel link until the enrich gate re-confirms them on
-- the next sweep — correct: we'd rather show nothing than a wrong link. The
-- host-exclusion regex stays as defense-in-depth.
--
-- Identical to 125 except the funnel LATERAL adds the kind filter.

DROP MATERIALIZED VIEW IF EXISTS frontend.emerging_scaling_apps;
CREATE MATERIALIZED VIEW frontend.emerging_scaling_apps AS
SELECT
    s.advertiser_id,
    s.cohort,
    adv.page_name,
    app.app_id,
    app.app_name,
    app.icon_url,
    s.gem_score,
    s.scaling_velocity,
    s.under_radar,
    s.funnel_intent,
    s.active_ad_count,
    s.max_longevity_days,
    s.alert,
    g.first_count,
    g.latest_count,
    g.weeks_observed,
    g.pct_change,
    clock_timestamp() AS refreshed_at,
    life.first_ad_at,
    life.longrun_ad_count,
    life.new_ads_4w,
    life.ads_seen,
    s.demand,
    s.whitespace,
    s.niche_keyword,
    s.niche_advertiser_count,
    s.installs_delta_28d,
    s.rating_count_delta_28d,
    app.installs,
    app.rating_count,
    app.rating,
    app.tagline,
    app.store,
    app.store_id,
    (s.vetoed AND s.veto_reason = 'longevity_below_floor') AS incubating,
    fn.funnel_url
FROM adtech.advertiser_scores s
JOIN public.advertisers adv ON adv.id = s.advertiser_id
LEFT JOIN LATERAL (
    SELECT a.app_id AS app_id, ap.name AS app_name, ap.icon_url AS icon_url,
           ap.installs AS installs, ap.rating_count AS rating_count,
           ap.rating AS rating,
           ap.store AS store, ap.store_id AS store_id,
           nullif(left(regexp_replace(
               split_part(ap.description, E'\n', 1), '\s+', ' ', 'g'
           ), 200), '') AS tagline
    FROM public.ads a
    JOIN public.apps ap ON ap.id = a.app_id
    WHERE a.advertiser_id = s.advertiser_id AND a.app_id IS NOT NULL
    ORDER BY a.last_seen DESC NULLS LAST
    LIMIT 1
) app ON TRUE
LEFT JOIN adtech.advertiser_growth g ON g.advertiser_id = s.advertiser_id
LEFT JOIN LATERAL (
    SELECT min(a.start_date)::date AS first_ad_at,
           count(*) FILTER (WHERE a.is_active
             AND a.start_date <= now() - interval '30 days') AS longrun_ad_count,
           count(*) FILTER (WHERE a.start_date >= now() - interval '28 days') AS new_ads_4w,
           count(*) AS ads_seen
    FROM public.ads a
    WHERE a.advertiser_id = s.advertiser_id
) life ON TRUE
LEFT JOIN LATERAL (
    -- Most recent captured funnel that the classifier VERIFIED as a real funnel:
    -- a checkout/paywall ('subscribe') or a confirmed quiz ('unknown'). Store
    -- bounces ('install') and unverified legacy rows (kind NULL) are excluded.
    -- Host-exclusion regex kept as a second line of defense.
    -- coalesce: prefer the resolved final URL, fall back to the raw landing.
    SELECT coalesce(f.final_url_after_redirect, f.landing_url) AS funnel_url
    FROM public.funnels f
    JOIN public.ads a ON a.id = f.ad_id
    WHERE a.advertiser_id = s.advertiser_id
      AND f.kind IN ('subscribe', 'unknown')
      AND coalesce(f.final_url_after_redirect, f.landing_url) ~* '^https?://'
      AND coalesce(f.final_url_after_redirect, f.landing_url)
          !~* '^https?://([a-z0-9.-]+\.)?(apps\.apple\.com|itunes\.apple\.com|play\.google\.com|facebook\.com|instagram\.com|twitter\.com|x\.com|tiktok\.com|youtube\.com|youtu\.be|t\.me|linktr\.ee)'
    ORDER BY f.captured_at DESC NULLS LAST
    LIMIT 1
) fn ON TRUE
WHERE (s.vetoed = FALSE OR s.veto_reason = 'longevity_below_floor')
  AND starts_with(adv.page_id, 'DEMO') IS NOT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_emerging_advertiser
    ON frontend.emerging_scaling_apps (advertiser_id);
