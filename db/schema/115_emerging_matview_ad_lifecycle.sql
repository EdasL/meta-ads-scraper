-- Rebuild frontend.emerging_scaling_apps adding per-advertiser ad-lifecycle
-- columns from a LEFT JOIN LATERAL aggregate over public.ads (growth-signals
-- redesign, 2026-06-10):
--   * first_ad_at       — date of the earliest stored ad (null when no ad has
--                         a start_date)
--   * longrun_ad_count  — active ads whose start_date is 30 or more days ago
--   * new_ads_4w        — ads launched in the last 28 days
--   * ads_seen          — all stored ads for the advertiser
-- The aggregate always returns a row, so the counts are 0 (never null) for an
-- advertiser with no stored ads. Values freeze at REFRESH time, like
-- refreshed_at. SELECT/joins copied verbatim from 113_matview_icon_url.sql
-- (which carries 111's quality WHERE clause); only the lifecycle lateral and
-- its four columns, appended at the end, are new. The matview is DROP/CREATE
-- (never edit an applied file). The UNIQUE index on advertiser_id is required
-- for REFRESH ... CONCURRENTLY.
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
    life.ads_seen
FROM adtech.advertiser_scores s
JOIN public.advertisers adv ON adv.id = s.advertiser_id
LEFT JOIN LATERAL (
    SELECT a.app_id AS app_id, ap.name AS app_name, ap.icon_url AS icon_url
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
WHERE s.vetoed = FALSE
  AND starts_with(adv.page_id, 'DEMO') IS NOT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_emerging_advertiser
    ON frontend.emerging_scaling_apps (advertiser_id);
