// Package repositories is the raw-SQL data layer. Each query is a verbatim port
// of src/agf/api/queries.py (and src/agf/monitoring/metrics.py), executed via
// GORM's Raw/Exec in raw-SQL mode. GORM rewrites `?` placeholders to `$n` for
// Postgres. All reads run inside a READ ONLY transaction (db.WithReadTx); the
// single write runs inside db.WithWriteTx.
package repositories

// mvCols is the frozen frontend.emerging_scaling_apps column list, in order.
const mvCols = `advertiser_id, cohort, page_name, app_id, app_name, gem_score, ` +
	`scaling_velocity, under_radar, funnel_intent, active_ad_count, ` +
	`max_longevity_days, alert, refreshed_at`
