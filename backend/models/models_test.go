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

func TestAPITime_MarshalsWithOffset(t *testing.T) {
	tm := APITime(time.Date(2026, 6, 5, 13, 30, 0, 0, time.UTC))
	b, err := json.Marshal(tm)
	if err != nil {
		t.Fatal(err)
	}
	if string(b) != `"2026-06-05T13:30:00+00:00"` {
		t.Errorf("APITime = %s, want \"2026-06-05T13:30:00+00:00\"", b)
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
