-- Ungate the directory's "Quiz" link: an app's web ad IS the funnel.
--
-- 127 gated funnel_url on a classifier verdict (kind IN ('subscribe','unknown')).
-- In practice the classifier is too strict — it only confirms a literal quiz
-- question screen, so real onboarding/checkout funnels that open on a value-prop
-- or plan screen (gocalmer.com/onboarding, foodhealth.co/join, jovoapp.com/sign-up)
-- came back 'unknown'-or-NULL and the gate hid them. That dropped ~half the real
-- funnels.
--
-- New definition (user directive): if we found an ad for the app and the ad
-- resolves to a web page that is not a store / social / MMP redirect, that page
-- IS the funnel. Show the link; let the human judge it. The classifier still
-- runs and stores its verdict (funnels.kind) — it just no longer gatekeeps the
-- link.
--
-- Identical to 127 except the funnel LATERAL:
--   * drops the f.kind filter
--   * extends the host-exclusion regex with store-equivalents that slipped
--     through (chromewebstore.google.com, appstore-download.com).

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
    -- Most recent captured funnel whose resolved URL is a real web page (not a
    -- store / social / MMP redirect). No classifier-verdict filter: a web ad for
    -- the app IS the funnel. coalesce: prefer the resolved final URL, fall back
    -- to the raw landing.
    SELECT coalesce(f.final_url_after_redirect, f.landing_url) AS funnel_url
    FROM public.funnels f
    JOIN public.ads a ON a.id = f.ad_id
    WHERE a.advertiser_id = s.advertiser_id
      AND coalesce(f.final_url_after_redirect, f.landing_url) ~* '^https?://'
      AND coalesce(f.final_url_after_redirect, f.landing_url)
          !~* '^https?://([a-z0-9.-]+\.)?(apps\.apple\.com|itunes\.apple\.com|play\.google\.com|chromewebstore\.google\.com|appstore-download\.com|facebook\.com|instagram\.com|twitter\.com|x\.com|tiktok\.com|youtube\.com|youtu\.be|t\.me|linktr\.ee)'
    ORDER BY f.captured_at DESC NULLS LAST
    LIMIT 1
) fn ON TRUE
WHERE (s.vetoed = FALSE OR s.veto_reason = 'longevity_below_floor')
  AND starts_with(adv.page_id, 'DEMO') IS NOT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_emerging_advertiser
    ON frontend.emerging_scaling_apps (advertiser_id);
