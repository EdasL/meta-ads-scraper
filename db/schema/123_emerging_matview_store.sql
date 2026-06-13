-- Rebuild frontend.emerging_scaling_apps adding store + store_id from the
-- linked app. The Discover table needs `store` to pick the right traction
-- signal per row: Play exposes installs (near-exact), iOS never does, so iOS
-- rows show rating_count instead. Without `store` the UI can't tell an iOS app
-- with 0 installs ("no data") from an Android app that genuinely has few.
-- Same shape as 118 otherwise — only the two columns are added.

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
    app.store_id
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
WHERE s.vetoed = FALSE
  AND starts_with(adv.page_id, 'DEMO') IS NOT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_emerging_advertiser
    ON frontend.emerging_scaling_apps (advertiser_id);
