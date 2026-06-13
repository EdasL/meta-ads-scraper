-- Fold the funnel INTO the app row: add funnel_url, the quiz/landing page the
-- app's own ads drive traffic to. Source is public.funnels (captured landing
-- per ad). We take the most recently captured row for the advertiser whose
-- RESOLVED url (final_url_after_redirect) is a real web page — i.e. an actual
-- landing/quiz, not a tracker that just bounces to the App/Play store. When the
-- only funnels resolve straight to a store there is no quiz to link, so
-- funnel_url is NULL and the table shows nothing.
--
-- This replaces the standalone /funnels page: a funnel is a property of an app,
-- surfaced as a clickable quiz link in the directory. Same shape as 124 — one
-- new LATERAL + column.

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
    -- Most recent captured funnel whose resolved URL is a real web landing/quiz:
    -- not a tracker bouncing straight to a store, and not a social profile page
    -- (those are where some ads ultimately point, but they aren't funnels).
    -- coalesce: prefer the resolved final URL, fall back to the raw landing.
    SELECT coalesce(f.final_url_after_redirect, f.landing_url) AS funnel_url
    FROM public.funnels f
    JOIN public.ads a ON a.id = f.ad_id
    WHERE a.advertiser_id = s.advertiser_id
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
