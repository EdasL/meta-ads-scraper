package models

import (
	"encoding/json"
	"testing"
	"time"
)

// Nullable pointer fields must serialize as the literal `null` (present key),
// matching Pydantic's `T | None` output — never an omitted key.
func TestShortlistRow_NullsArePresent(t *testing.T) {
	b, err := json.Marshal(ShortlistRow{AdvertiserID: 7, Cohort: "ios", GemScore: 80})
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"page_name", "app_id", "app_name", "scaling_velocity",
		"under_radar", "funnel_intent", "active_ad_count", "max_longevity_days", "refreshed_at"} {
		v, ok := m[k]
		if !ok {
			t.Errorf("key %q missing (must be present as null)", k)
			continue
		}
		if string(v) != "null" {
			t.Errorf("key %q = %s, want null", k, v)
		}
	}
	if string(m["alert"]) != "false" {
		t.Errorf("alert = %s, want false", m["alert"])
	}
}

// Empty dossier slices must render as [] (not null) so the UI can map over them.
func TestGemDossier_EmptySlicesAreArrays(t *testing.T) {
	d := GemDossier{
		Apps:      make([]AppInfo, 0),
		Funnels:   make([]FunnelInfo, 0),
		Creatives: make([]CreativeInfo, 0),
	}
	b, _ := json.Marshal(d)
	var m map[string]json.RawMessage
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"apps", "funnels", "creatives"} {
		if string(m[k]) != "[]" {
			t.Errorf("key %q = %s, want []", k, m[k])
		}
	}
}

// New nullable signal fields must serialize as literal null (present key), and
// the list-shaped fields (screenshots, publisher_platforms) as [] when absent.
func TestAppInfo_NewFieldsSerializeCleanly(t *testing.T) {
	b, err := json.Marshal(AppInfo{Store: "ios", StoreID: "1", Reviews: make([]AppReview, 0)})
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"installs", "updated_at"} {
		if v, ok := m[k]; !ok || string(v) != "null" {
			t.Errorf("key %q = %s, want null", k, v)
		}
	}
	for k, want := range map[string]string{
		"description": `""`, "icon_url": `""`, "price_label": `""`,
		"screenshots": "[]", "reviews": "[]",
	} {
		if string(m[k]) != want {
			t.Errorf("key %q = %s, want %s", k, m[k], want)
		}
	}
}

func TestCreativeInfo_NewFieldsSerializeCleanly(t *testing.T) {
	b, err := json.Marshal(CreativeInfo{})
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"ad_started_on", "ad_ended_on", "ad_active", "ad_days_running",
		"external_ad_id", "cta_text", "link_url", "eu_total_reach"} {
		if v, ok := m[k]; !ok || string(v) != "null" {
			t.Errorf("key %q = %s, want null", k, v)
		}
	}
	if string(m["publisher_platforms"]) != "[]" {
		t.Errorf("publisher_platforms = %s, want []", m["publisher_platforms"])
	}
}

func TestGemDossier_MetaSignalsSerializeCleanly(t *testing.T) {
	b, err := json.Marshal(GemDossier{})
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"ads_last_seen", "page_like_count", "meta_active_ads",
		"meta_active_ads_checked_at"} {
		if v, ok := m[k]; !ok || string(v) != "null" {
			t.Errorf("key %q = %s, want null", k, v)
		}
	}
	if string(m["meta_active_ads_capped"]) != "false" {
		t.Errorf("meta_active_ads_capped = %s, want false", m["meta_active_ads_capped"])
	}
}

func TestAppMetricPoint_Marshal(t *testing.T) {
	rc := int64(100)
	p := AppMetricPoint{
		AppID:        3,
		Store:        "ios",
		SnapshotDate: APIDate(time.Date(2026, 6, 10, 0, 0, 0, 0, time.UTC)),
		RatingCount:  &rc,
	}
	b, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"app_id":3,"store":"ios","snapshot_date":"2026-06-10","rating":null,"rating_count":100,"installs":null}`
	if string(b) != want {
		t.Errorf("AppMetricPoint = %s, want %s", b, want)
	}
}

func TestAPIDate_MarshalsDateOnly(t *testing.T) {
	d := APIDate(time.Date(2026, 6, 5, 13, 30, 0, 0, time.UTC))
	b, err := json.Marshal(d)
	if err != nil {
		t.Fatal(err)
	}
	if string(b) != `"2026-06-05"` {
		t.Errorf("APIDate = %s, want \"2026-06-05\"", b)
	}
}

func TestAPITime_MarshalsLikePydantic(t *testing.T) {
	cases := []struct {
		name string
		tm   time.Time
		want string
	}{
		{"whole second omits fraction", time.Date(2026, 6, 5, 13, 30, 0, 0, time.UTC), `"2026-06-05T13:30:00Z"`},
		{"sub-second keeps six digits", time.Date(2026, 6, 5, 13, 30, 0, 500000000, time.UTC), `"2026-06-05T13:30:00.500000Z"`},
		{"microsecond precision", time.Date(2026, 6, 5, 8, 8, 56, 926285000, time.UTC), `"2026-06-05T08:08:56.926285Z"`},
		{"non-UTC normalized to Z", time.Date(2026, 6, 5, 16, 30, 0, 0, time.FixedZone("x", 3*3600)), `"2026-06-05T13:30:00Z"`},
	}
	for _, c := range cases {
		b, err := json.Marshal(APITime(c.tm))
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if string(b) != c.want {
			t.Errorf("%s: APITime = %s, want %s", c.name, b, c.want)
		}
	}
}

func TestAPITime_Scan(t *testing.T) {
	var tm APITime
	if err := (&tm).Scan(time.Date(2026, 6, 5, 0, 0, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	if !time.Time(tm).Equal(time.Date(2026, 6, 5, 0, 0, 0, 0, time.UTC)) {
		t.Errorf("scanned time = %v", time.Time(tm))
	}
	// nil (SQL NULL) leaves the zero value without error.
	var z APITime
	if err := (&z).Scan(nil); err != nil {
		t.Fatalf("scan nil: %v", err)
	}
}
