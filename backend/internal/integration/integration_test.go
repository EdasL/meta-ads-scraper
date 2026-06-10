// Package integration exercises the full gin stack (router → controller →
// repository → GORM → Postgres) against a real database. It is the gate that
// proves GORM correctly scans the custom APITime/APIDate types and null
// pointers, and that the JSON contract matches the Python API.
//
// Skipped unless AGF_IT_DB_URL points at a Postgres with the schema applied.
package integration

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/routers"
)

var advID, adv2ID int

func TestMain(m *testing.M) {
	dsn := os.Getenv("AGF_IT_DB_URL")
	if dsn == "" {
		os.Exit(0) // nothing to do; `go test` reports no failures
	}
	gin.SetMode(gin.TestMode)
	if _, err := db.Init(dsn); err != nil {
		panic(err)
	}
	seed(db.DB())
	os.Exit(m.Run())
}

func router() *gin.Engine {
	r := gin.New()
	routers.Init(r)
	return r
}

func get(t *testing.T, r *gin.Engine, path string) (*httptest.ResponseRecorder, map[string]json.RawMessage) {
	t.Helper()
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	r.ServeHTTP(w, req)
	out := map[string]json.RawMessage{}
	if strings.HasPrefix(w.Body.String(), "{") {
		_ = json.Unmarshal(w.Body.Bytes(), &out)
	}
	return w, out
}

func TestShortlist(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/shortlist", nil))
	require.Equal(t, 200, w.Code)

	var rows []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
	require.Len(t, rows, 2, "two seeded gems, ordered by gem_score DESC")

	top := rows[0]
	require.EqualValues(t, advID, top["advertiser_id"])
	require.EqualValues(t, 88.5, top["gem_score"])
	require.Equal(t, "Acme Co", top["page_name"])
	require.Equal(t, "Acme App", top["app_name"])
	require.Equal(t, true, top["alert"])
	// scaling_velocity is a Postgres REAL (float4); 0.7 widens to ~0.69999999 —
	// exactly what the Python API returns too. Compare with a tolerance.
	require.InDelta(t, 0.7, top["scaling_velocity"], 1e-6)
	require.NotNil(t, top["refreshed_at"])

	// Null fidelity: the second gem has no app and null axes — keys present, null.
	second := rows[1]
	for _, k := range []string{"app_id", "app_name", "page_name", "scaling_velocity", "active_ad_count"} {
		v, ok := second[k]
		require.True(t, ok, "key %s present", k)
		require.Nil(t, v, "key %s is null", k)
	}
}

func TestShortlistCohortFilter(t *testing.T) {
	r := router()

	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/shortlist?cohort=android", nil))
	require.Equal(t, 200, w.Code)
	require.Equal(t, "[]", strings.TrimSpace(w.Body.String()), "no android cohort → empty array, not null")

	w = httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/shortlist?cohort=ios&min_score=80", nil))
	var rows []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
	require.Len(t, rows, 1, "only the 88.5 gem clears min_score=80 in cohort ios")
}

func TestDossier(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/gems/"+strconv.Itoa(advID), nil))
	require.Equal(t, 200, w.Code)

	var d struct {
		Gem           map[string]any   `json:"gem"`
		Domain        *string          `json:"domain"`
		PageCategory  *string          `json:"page_category"`
		IgUsername    *string          `json:"ig_username"`
		IgFollowers   *int             `json:"ig_followers"`
		ParentCompany *string          `json:"parent_company"`
		Apps          []map[string]any `json:"apps"`
		Funnels       []map[string]any `json:"funnels"`
		Creatives     []map[string]any `json:"creatives"`
	}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &d))
	require.EqualValues(t, advID, d.Gem["advertiser_id"])
	require.NotNil(t, d.Domain)
	require.Equal(t, "example.com", *d.Domain)
	require.NotNil(t, d.ParentCompany)
	require.Equal(t, "BigCorp", *d.ParentCompany)
	require.NotNil(t, d.PageCategory)
	require.Equal(t, "Software", *d.PageCategory)
	require.NotNil(t, d.IgFollowers)
	require.Equal(t, 1234, *d.IgFollowers)

	require.Len(t, d.Apps, 1)
	require.Equal(t, "ios", d.Apps[0]["store"])
	require.Equal(t, "111", d.Apps[0]["store_id"])
	require.Equal(t, "2026-01-15", d.Apps[0]["release_date"], "date-only format")
	require.EqualValues(t, 4.5, d.Apps[0]["rating"])
	require.Equal(t, true, d.Apps[0]["is_emerging"])

	// Store-preview fields (migration 107) + reviews (108).
	require.Equal(t, "Splits bills.", d.Apps[0]["description"])
	require.Equal(t, "https://cdn/icon.png", d.Apps[0]["icon_url"])
	require.Equal(t, []any{"https://cdn/s1.png", "https://cdn/s2.png"}, d.Apps[0]["screenshots"])
	require.Equal(t, "Free", d.Apps[0]["price_label"])
	require.Nil(t, d.Apps[0]["installs"], "iOS app — no install counts")
	require.NotNil(t, d.Apps[0]["updated_at"])
	reviews, ok := d.Apps[0]["reviews"].([]any)
	require.True(t, ok, "reviews is an array")
	require.Len(t, reviews, 5, "top 5 of 6 seeded reviews")
	top := reviews[0].(map[string]any)
	require.Equal(t, "reviewer 6", top["author"], "most recent first")
	require.EqualValues(t, (6%5)+1, top["rating"], "seeded as (i%5)+1")

	require.Len(t, d.Funnels, 1)
	require.Equal(t, "funnels/ad1.png", d.Funnels[0]["screenshot_s3_key"])
	require.Len(t, d.Creatives, 1)
	require.Equal(t, "creatives/ad1.jpg", d.Creatives[0]["s3_key"])
	require.Equal(t, "image", d.Creatives[0]["type"])

	// Per-ad run metadata: the seeded ad started 9 days ago and is active.
	require.NotNil(t, d.Creatives[0]["ad_started_on"])
	require.Equal(t, true, d.Creatives[0]["ad_active"])
	require.EqualValues(t, 9, d.Creatives[0]["ad_days_running"])
	require.Equal(t, []any{"facebook", "instagram"}, d.Creatives[0]["publisher_platforms"])
	require.EqualValues(t, 75000, d.Creatives[0]["eu_total_reach"],
		"latest non-NULL ad_metrics snapshot wins")
}

// The second advertiser has no app, no ad run metadata, and no Meta signals:
// every new field must serialize as null/[]/false — never crash or omit keys.
func TestDossierNullSignals(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/gems/"+strconv.Itoa(adv2ID), nil))
	require.Equal(t, 200, w.Code)

	var d map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &d))
	for _, k := range []string{"page_like_count", "meta_active_ads", "meta_active_ads_checked_at"} {
		v, ok := d[k]
		require.True(t, ok, "key %s present", k)
		require.Nil(t, v, "key %s is null", k)
	}
	require.Equal(t, false, d["meta_active_ads_capped"])
	require.NotNil(t, d["ads_last_seen"], "advertisers.last_seen is NOT NULL")
	require.Equal(t, []any{}, d["apps"])
}

func TestDossierMetaSignals(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/gems/"+strconv.Itoa(advID), nil))
	require.Equal(t, 200, w.Code)

	var d map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &d))
	require.EqualValues(t, 3500, d["page_like_count"])
	require.EqualValues(t, 12, d["meta_active_ads"])
	require.Equal(t, false, d["meta_active_ads_capped"])
	require.NotNil(t, d["meta_active_ads_checked_at"])
	require.NotNil(t, d["ads_last_seen"])
}

func TestAppMetrics(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/gems/"+strconv.Itoa(advID)+"/app-metrics", nil))
	require.Equal(t, 200, w.Code)

	var pts []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &pts))
	require.Len(t, pts, 3)
	require.Equal(t, "2026-06-08", pts[0]["snapshot_date"], "ordered by snapshot_date ASC")
	require.Equal(t, "2026-06-10", pts[2]["snapshot_date"])
	require.Equal(t, "ios", pts[0]["store"])
	require.EqualValues(t, 100, pts[0]["rating_count"])
	require.EqualValues(t, 130, pts[2]["rating_count"])
	require.Nil(t, pts[0]["installs"], "iOS rows carry no installs")
	require.InDelta(t, 4.5, pts[0]["rating"], 1e-6)
}

func TestAppMetricsEmpty(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/gems/"+strconv.Itoa(adv2ID)+"/app-metrics", nil))
	require.Equal(t, 200, w.Code)
	require.Equal(t, "[]", strings.TrimSpace(w.Body.String()), "no linked app → empty array, not null")
}

func TestDossierNotFound(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/gems/999999", nil))
	require.Equal(t, 404, w.Code)
	require.Contains(t, w.Body.String(), "loosen filter")
}

func TestScaling(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/gems/"+strconv.Itoa(advID)+"/scaling", nil))
	require.Equal(t, 200, w.Code)
	var pts []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &pts))
	require.Len(t, pts, 1)
	require.Equal(t, "2026-05-25", pts[0]["week_start"], "date-only format")
	require.EqualValues(t, 100000, pts[0]["total_eu_reach"])
	require.EqualValues(t, 3, pts[0]["active_ad_count"])
}

func TestReviewQueueAndDecision(t *testing.T) {
	r := router()

	// Queue starts with the one alerting, undecided gem.
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/review/queue", nil))
	require.Equal(t, 200, w.Code)
	var q []map[string]any
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &q))
	require.Len(t, q, 1)
	require.EqualValues(t, advID, q[0]["advertiser_id"])

	// Record a decision (the only write path).
	body := strings.NewReader(`{"verdict":"accept","reviewer":"tester","note":"looks good"}`)
	w = httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/review/"+strconv.Itoa(advID)+"/decision", body)
	req.Header.Set("Content-Type", "application/json")
	r.ServeHTTP(w, req)
	require.Equal(t, 200, w.Code)
	require.Contains(t, w.Body.String(), `"status":"ok"`)

	// It must now be excluded from the queue (NOT EXISTS decision).
	w = httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/review/queue", nil))
	require.Equal(t, "[]", strings.TrimSpace(w.Body.String()))

	// Row landed in frontend.review_decisions.
	var n int64
	require.NoError(t, db.DB().Raw(
		"SELECT count(*) FROM frontend.review_decisions WHERE advertiser_id = ? AND verdict = 'accept'", advID,
	).Scan(&n).Error)
	require.EqualValues(t, 1, n)
}

func TestReviewDecisionBadVerdict(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/review/"+strconv.Itoa(advID)+"/decision",
		strings.NewReader(`{"verdict":"maybe","reviewer":"x"}`))
	req.Header.Set("Content-Type", "application/json")
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusUnprocessableEntity, w.Code)
}

func TestHealthMetrics(t *testing.T) {
	r := router()
	w, _ := get(t, r, "/api/health/metrics")
	require.Equal(t, 200, w.Code)
	var m struct {
		WindowHours float64 `json:"window_hours"`
		Total       int64   `json:"total"`
		Errors      int64   `json:"errors"`
		Ok          int64   `json:"ok"`
		Alerts      []struct {
			Kind     string `json:"kind"`
			Severity string `json:"severity"`
		} `json:"alerts"`
		Degraded bool `json:"degraded"`
	}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &m))
	require.Equal(t, float64(24), m.WindowHours)
	require.EqualValues(t, 6, m.Errors)
	require.Equal(t, m.Total-m.Errors, m.Ok)
	require.True(t, m.Degraded)
	require.NotEmpty(t, m.Alerts)
	require.Equal(t, "doc_id_rotation", m.Alerts[0].Kind)
	require.Equal(t, "critical", m.Alerts[0].Severity)
}

func TestMetricsEndpoint(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	require.Equal(t, 200, w.Code)
	require.Contains(t, w.Body.String(), "# HELP", "prometheus exposition format")
}

func TestMediaForbiddenKey(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/media/etc/passwd", nil))
	require.Equal(t, 403, w.Code)
}

func TestHealthz(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	require.Equal(t, 200, w.Code)
	require.JSONEq(t, `{"status":"ok"}`, w.Body.String())
}

func TestReadyz(t *testing.T) {
	r := router()
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	require.Equal(t, 200, w.Code, "DB reachable → ready")
	require.JSONEq(t, `{"status":"ok"}`, w.Body.String())
}

// seed loads a small, deterministic dataset and refreshes the product matview.
func seed(g *gorm.DB) {
	must(g.Exec(`TRUNCATE frontend.review_decisions, adtech.app_advertiser_metrics,
		adtech.ad_metrics, adtech.app_metrics, adtech.advertiser_scores,
		public.app_reviews, public.creatives, public.funnels,
		public.ads, public.apps, public.advertisers, public.domains, logging.crawl_log
		RESTART IDENTITY CASCADE`).Error)

	var domainID int
	must(g.Raw(`INSERT INTO public.domains (domain) VALUES ('example.com') RETURNING id`).Scan(&domainID).Error)

	var parentID int
	must(g.Raw(`INSERT INTO public.advertisers (page_id, page_name) VALUES ('PARENT1','BigCorp') RETURNING id`).Scan(&parentID).Error)

	must(g.Raw(`INSERT INTO public.advertisers
		(page_id, page_name, domain_id, parent_company_id, page_category, ig_username, ig_followers,
		 page_like_count, meta_active_ads, meta_active_ads_capped, meta_active_ads_checked_at)
		VALUES ('PAGE1','Acme Co',?,?,'Software','acme',1234, 3500, 12, false, now()) RETURNING id`,
		domainID, parentID).Scan(&advID).Error)

	must(g.Raw(`INSERT INTO public.advertisers (page_id, page_name) VALUES ('PAGE2', NULL) RETURNING id`).Scan(&adv2ID).Error)

	var appID int
	must(g.Raw(`INSERT INTO public.apps
		(store, store_id, name, developer, genre, release_date, rating, rating_count, is_emerging,
		 description, icon_url, screenshots, price_label)
		VALUES ('ios','111','Acme App','Acme Inc','Productivity','2026-01-15',4.5,100,true,
		 'Splits bills.','https://cdn/icon.png',
		 '["https://cdn/s1.png","https://cdn/s2.png"]'::jsonb,'Free') RETURNING id`).Scan(&appID).Error)

	// 6 reviews → the dossier returns the 5 most recent (review 6 first).
	for i := 1; i <= 6; i++ {
		must(g.Exec(`INSERT INTO public.app_reviews
			(app_id, review_id, author, rating, title, content, reviewed_at)
			VALUES (?, ?, ?, ?, 'ok app', 'does the thing', now() - (? * interval '1 day'))`,
			appID, fmt.Sprintf("r%d", i), fmt.Sprintf("reviewer %d", i), (i%5)+1, 6-i).Error)
	}

	// Daily app metric snapshots (iOS → installs NULL).
	must(g.Exec(`INSERT INTO adtech.app_metrics (app_id, snapshot_date, rating, rating_count, installs)
		VALUES (?, '2026-06-08', 4.5, 100, NULL), (?, '2026-06-09', 4.5, 110, NULL),
		       (?, '2026-06-10', 4.6, 130, NULL)`, appID, appID, appID).Error)

	var adID int
	must(g.Raw(`INSERT INTO public.ads
		(external_ad_id, advertiser_id, app_id, is_active, start_date, publisher_platforms, last_seen)
		VALUES ('AD1',?,?, true, now() - interval '9 days', ARRAY['facebook','instagram'], now())
		RETURNING id`, advID, appID).Scan(&adID).Error)

	// Per-ad reach snapshots: the latest non-NULL value (75000) is the one served.
	must(g.Exec(`INSERT INTO adtech.ad_metrics (ad_id, snapshot_date, eu_total_reach, active)
		VALUES (?, '2026-06-08', 50000, true), (?, '2026-06-09', 75000, true),
		       (?, '2026-06-10', NULL, true)`, adID, adID, adID).Error)

	must(g.Exec(`INSERT INTO public.creatives (ad_id, type, s3_key, body_text, title)
		VALUES (?, 'image','creatives/ad1.jpg','buy now','Great Ad')`, adID).Error)
	must(g.Exec(`INSERT INTO public.funnels (ad_id, landing_url, final_url_after_redirect, screenshot_s3_key)
		VALUES (?, 'https://land','https://final','funnels/ad1.png')`, adID).Error)

	// adv: full scores, alerting. adv2: minimal, null axes, not alerting.
	must(g.Exec(`INSERT INTO adtech.advertiser_scores
		(advertiser_id, cohort, gem_score, scaling_velocity, under_radar, funnel_intent,
		 active_ad_count, max_longevity_days, alert)
		VALUES (?, 'ios', 88.5, 0.7, 0.6, 0.5, 3, 42, true)`, advID).Error)
	must(g.Exec(`INSERT INTO adtech.advertiser_scores (advertiser_id, cohort, gem_score, alert)
		VALUES (?, 'ios', 75, false)`, adv2ID).Error)

	must(g.Exec(`INSERT INTO adtech.app_advertiser_metrics
		(advertiser_id, week_start, active_ad_count, distinct_creative_count, total_eu_reach)
		VALUES (?, '2026-05-25', 3, 2, 100000)`, advID).Error)

	// The shortlist only surfaces advertisers with at least one downloaded
	// creative — give the minimal advertiser a bare ad (no app, no run metadata)
	// with one creative so it stays on the shortlist with all-null fields.
	var ad2ID int
	must(g.Raw(`INSERT INTO public.ads (external_ad_id, advertiser_id)
		VALUES ('AD2', ?) RETURNING id`, adv2ID).Scan(&ad2ID).Error)
	must(g.Exec(`INSERT INTO public.creatives (ad_id, type, s3_key)
		VALUES (?, 'image', 'creatives/ad2.jpg')`, ad2ID).Error)

	// crawl_log: 6 doc_id failures (last hour) → doc_id_rotation alert; plus ok rows.
	for i := 0; i < 6; i++ {
		must(g.Exec(`INSERT INTO logging.crawl_log (entity_type, entity_id, crawl_result)
			VALUES ('ad', ?, 'error doc_id rotated')`, i).Error)
	}
	for i := 0; i < 4; i++ {
		must(g.Exec(`INSERT INTO logging.crawl_log (entity_type, entity_id, crawl_result)
			VALUES ('ad', ?, 'ok')`, i).Error)
	}

	must(g.Exec(`REFRESH MATERIALIZED VIEW frontend.emerging_scaling_apps`).Error)
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
