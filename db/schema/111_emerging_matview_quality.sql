-- Rebuild frontend.emerging_scaling_apps so the feed honors scoring vetoes and can
-- never surface demo-seeded rows. SELECT/joins copied verbatim from
-- 105_emerging_matview_growth.sql; only the WHERE clause is new:
--   * s.vetoed = FALSE   — vetoes (game / no_app / incumbent_* / established_traction
--                          plus the original five) now actually remove rows from the feed
--   * NOT starts_with(page_id, 'DEMO') — demo seed is structurally excluded from the
--     real feed (starts_with, not LIKE: the migration runner executes files through the
--     driver, which rejects the percent sign anywhere in the statement, comments included)
-- The matview is DROP/CREATE (never edit an applied file). The UNIQUE index on
-- advertiser_id is required for REFRESH ... CONCURRENTLY.
DROP MATERIALIZED VIEW IF EXISTS frontend.emerging_scaling_apps;
CREATE MATERIALIZED VIEW frontend.emerging_scaling_apps AS
SELECT
    s.advertiser_id,
    s.cohort,
    adv.page_name,
    app.app_id,
    app.app_name,
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
    clock_timestamp() AS refreshed_at
FROM adtech.advertiser_scores s
JOIN public.advertisers adv ON adv.id = s.advertiser_id
LEFT JOIN LATERAL (
    SELECT a.app_id AS app_id, ap.name AS app_name
    FROM public.ads a
    JOIN public.apps ap ON ap.id = a.app_id
    WHERE a.advertiser_id = s.advertiser_id AND a.app_id IS NOT NULL
    ORDER BY a.last_seen DESC NULLS LAST
    LIMIT 1
) app ON TRUE
LEFT JOIN adtech.advertiser_growth g ON g.advertiser_id = s.advertiser_id
WHERE s.vetoed = FALSE
  AND starts_with(adv.page_id, 'DEMO') IS NOT TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_emerging_advertiser
    ON frontend.emerging_scaling_apps (advertiser_id);
