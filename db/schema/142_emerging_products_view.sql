-- Single source of truth for the product identity key: normalized app name +
-- developer (corp suffixes stripped). Used by the product view below AND by the
-- dossier "pages running this app" query, so both agree on what one product is.
CREATE OR REPLACE FUNCTION frontend.product_key(p_name text, p_developer text)
RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT 'p:'
      || regexp_replace(lower(btrim(coalesce(p_name, ''))), '[^a-z0-9]+', '', 'g')
      || '|'
      || regexp_replace(
           regexp_replace(lower(btrim(coalesce(p_developer, ''))),
             '\y(llc|inc|ltd|co|gmbh|corp|corporation|company|limited)\y\.?', '', 'g'),
           '[^a-z0-9]+', '', 'g');
$$;

-- App-level dedup for the gems feed. The per-advertiser matview
-- frontend.emerging_scaling_apps shows one row per Meta page, but one app is
-- routinely fronted by many pages (persona pages that read like a real person,
-- main-account shielding, geographic splits) and by both app stores — so ~10%
-- of feed rows were the same product repeated. This view collapses to one row
-- per product: the highest-GemScore page represents it, the ad-count footprint
-- is summed across pages, and iOS+Android listings of the same app merge by a
-- normalized name+developer key. The feed and the dossier's Gem read this view;
-- the matview is unchanged, so per-advertiser dossier/dossier-ads still work.
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
  r.store, r.store_id, r.incubating, r.funnel_url, r.web_only,
  a.page_count,
  a.stores
FROM ranked r
JOIN agg a USING (product_key)
WHERE r.rn = 1;
