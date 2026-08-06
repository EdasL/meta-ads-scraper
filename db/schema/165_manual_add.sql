-- Migration 165: manually-added advertisers.
--
-- A founder can paste a Meta Ad Library link and add that advertiser on demand
-- (spec docs/superpowers/specs/2026-08-06-add-gem-from-ad-library-link-design.md).
-- Those advertisers must surface even when scoring vetoes them — the vetoes
-- exist to filter breadth-sweep noise, not to overrule an explicit request.
-- This mirrors the existing 'incubating' escape hatch (migrations 124/125):
-- surfaced, marked, reason visible. The veto thresholds themselves are
-- untouched, so the automated sweeps keep filtering exactly as they do today.
--
-- Structure otherwise VERBATIM from 163 (single-pass funnel CTE — do not
-- reintroduce the per-advertiser LATERAL, it caused the 08-04 refresh death
-- spiral). Three changes only: the ALTER below, two new SELECT columns, and the
-- extended WHERE. The dependent view frontend.emerging_scaling_products must be
-- dropped first and is recreated below with manual_add + veto_reason passed
-- through. The unique index on advertiser_id is preserved for REFRESH
-- CONCURRENTLY.
-- NOTE: applied via exec_driver_sql with NO params — never a bare percent sign;
-- use make_interval for intervals (lesson from migration 147).

ALTER TABLE public.advertisers
    ADD COLUMN IF NOT EXISTS manual_add BOOLEAN NOT NULL DEFAULT FALSE;

DROP VIEW IF EXISTS frontend.emerging_scaling_products;
DROP MATERIALIZED VIEW IF EXISTS frontend.emerging_scaling_apps;
CREATE MATERIALIZED VIEW frontend.emerging_scaling_apps AS
WITH web_funnels AS MATERIALIZED (
    -- Most recent captured funnel per advertiser whose resolved URL is a real
    -- web page (not a store / social / MMP redirect). coalesce: prefer the
    -- resolved final URL, fall back to the raw landing. One pass over funnels;
    -- the regexes run once per funnel row, never per advertiser.
    SELECT DISTINCT ON (a.advertiser_id)
           a.advertiser_id,
           coalesce(f.final_url_after_redirect, f.landing_url) AS funnel_url
    FROM public.funnels f
    JOIN public.ads a ON a.id = f.ad_id
    WHERE coalesce(f.final_url_after_redirect, f.landing_url) ~* '^https?://'
      AND coalesce(f.final_url_after_redirect, f.landing_url)
          !~* '^https?://([a-z0-9.-]+\.)?(apps\.apple\.com|itunes\.apple\.com|play\.google\.com|chromewebstore\.google\.com|appstore-download\.com|facebook\.com|instagram\.com|twitter\.com|x\.com|tiktok\.com|youtube\.com|youtu\.be|t\.me|linktr\.ee|appstack\.link|onelink\.me|onelink\.to|app\.link|go\.link|adj\.st|bnc\.lt|adjust\.com|appsflyer\.com|kochava\.com|singular\.net|lnk\.to|apple\.co)'
    ORDER BY a.advertiser_id, f.captured_at DESC NULLS LAST
)
SELECT
    s.advertiser_id,
    s.cohort,
    adv.page_name,
    app.app_id,
    COALESCE(app.app_name, adv.web_name) AS app_name,
    COALESCE(app.icon_url, adv.web_icon_url) AS icon_url,
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
    COALESCE(app.tagline, NULLIF(left(regexp_replace(adv.web_description, '\s+', ' ', 'g'), 200), '')) AS tagline,
    app.store,
    app.store_id,
    (s.vetoed AND s.veto_reason = 'longevity_below_floor') AS incubating,
    adv.manual_add,
    s.veto_reason,
    COALESCE(fn.funnel_url, adv.web_landing_url) AS funnel_url,
    (adv.web_payoff_kind IS NOT NULL) AS web_only
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
LEFT JOIN web_funnels fn ON fn.advertiser_id = s.advertiser_id
-- adv.manual_add is the pasted-link escape hatch. It sits INSIDE the veto OR
-- group and outside the DEMO exclusion, so a manual add can never resurrect a
-- DEMO page.
WHERE (s.vetoed = FALSE
       OR s.veto_reason = 'longevity_below_floor'
       OR adv.manual_add)
  AND starts_with(adv.page_id, 'DEMO') IS NOT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_emerging_advertiser
    ON frontend.emerging_scaling_apps (advertiser_id);

-- Recreate the dependent product-dedup view, verbatim from 142 plus the two
-- new pass-through columns.
CREATE OR REPLACE VIEW frontend.emerging_scaling_products AS
WITH base AS (
  SELECT mv.*,
         CASE
           WHEN mv.app_name IS NULL OR btrim(mv.app_name) = '' THEN 'adv:' || mv.advertiser_id
           ELSE frontend.product_key(mv.app_name, ap.developer)
         END AS product_key
  FROM frontend.emerging_scaling_apps mv
  LEFT JOIN public.apps ap ON ap.id = mv.app_id
),
agg AS (
  SELECT product_key,
         sum(active_ad_count)     AS p_active_ad_count,
         sum(ads_seen)            AS p_ads_seen,
         sum(new_ads_4w)          AS p_new_ads_4w,
         sum(longrun_ad_count)    AS p_longrun_ad_count,
         max(max_longevity_days)  AS p_max_longevity_days,
         count(*)::int            AS page_count,
         CASE
           WHEN count(DISTINCT store) FILTER (WHERE store IS NOT NULL) > 1 THEN 'ios+android'
           ELSE max(store) FILTER (WHERE store IS NOT NULL)
         END                      AS stores
  FROM base
  GROUP BY product_key
),
ranked AS (
  SELECT base.*,
         row_number() OVER (
           PARTITION BY product_key
           ORDER BY gem_score DESC NULLS LAST, active_ad_count DESC NULLS LAST, advertiser_id
         ) AS rn
  FROM base
)
SELECT
  r.advertiser_id, r.cohort, r.page_name, r.app_id, r.app_name, r.icon_url,
  r.gem_score, r.scaling_velocity, r.under_radar, r.funnel_intent,
  a.p_active_ad_count    AS active_ad_count,
  a.p_max_longevity_days AS max_longevity_days,
  r.alert, r.first_count, r.latest_count, r.weeks_observed, r.pct_change,
  r.refreshed_at, r.first_ad_at,
  a.p_longrun_ad_count   AS longrun_ad_count,
  a.p_new_ads_4w         AS new_ads_4w,
  a.p_ads_seen           AS ads_seen,
  r.demand, r.whitespace, r.niche_keyword, r.niche_advertiser_count,
  r.installs_delta_28d, r.rating_count_delta_28d,
  r.installs, r.rating_count, r.rating, r.tagline,
  r.store, r.store_id, r.incubating, r.manual_add, r.veto_reason,
  r.funnel_url, r.web_only,
  a.page_count,
  a.stores
FROM ranked r
JOIN agg a USING (product_key)
WHERE r.rn = 1;
