// Package models holds the API response/request shapes. They reproduce the
// Python Pydantic models in src/agf/api/schemas.py exactly: snake_case JSON,
// and every nullable field is a pointer with NO omitempty so a missing value
// serializes as the literal `null` (matching Pydantic), not an omitted key.
package models

import (
	"database/sql/driver"
	"fmt"
	"time"
)

// APITime serializes like Pydantic's datetime: RFC3339 with a numeric offset and
// microsecond precision. It scans from a Postgres timestamptz.
type APITime time.Time

func (t APITime) MarshalJSON() ([]byte, error) {
	// Normalize to UTC so the API never leaks the server's local timezone.
	return []byte(`"` + time.Time(t).UTC().Format("2006-01-02T15:04:05.999999-07:00") + `"`), nil
}

func (t *APITime) Scan(v any) error {
	if v == nil {
		return nil
	}
	if x, ok := v.(time.Time); ok {
		*t = APITime(x)
		return nil
	}
	return fmt.Errorf("APITime: cannot scan %T", v)
}

func (t APITime) Value() (driver.Value, error) { return time.Time(t), nil }

// APIDate serializes like Pydantic's date: YYYY-MM-DD. It scans from a Postgres
// date (kept distinct from APITime so a `date` column never leaks a time part).
type APIDate time.Time

func (d APIDate) MarshalJSON() ([]byte, error) {
	return []byte(`"` + time.Time(d).Format("2006-01-02") + `"`), nil
}

func (d *APIDate) Scan(v any) error {
	if v == nil {
		return nil
	}
	if x, ok := v.(time.Time); ok {
		*d = APIDate(x)
		return nil
	}
	return fmt.Errorf("APIDate: cannot scan %T", v)
}

func (d APIDate) Value() (driver.Value, error) { return time.Time(d), nil }

// ShortlistRow mirrors the frozen frontend.emerging_scaling_apps matview columns.
type ShortlistRow struct {
	AdvertiserID     int      `json:"advertiser_id" gorm:"column:advertiser_id"`
	Cohort           string   `json:"cohort" gorm:"column:cohort"`
	PageName         *string  `json:"page_name" gorm:"column:page_name"`
	AppID            *int     `json:"app_id" gorm:"column:app_id"`
	AppName          *string  `json:"app_name" gorm:"column:app_name"`
	GemScore         float64  `json:"gem_score" gorm:"column:gem_score"`
	ScalingVelocity  *float64 `json:"scaling_velocity" gorm:"column:scaling_velocity"`
	UnderRadar       *float64 `json:"under_radar" gorm:"column:under_radar"`
	FunnelIntent     *float64 `json:"funnel_intent" gorm:"column:funnel_intent"`
	ActiveAdCount    *int     `json:"active_ad_count" gorm:"column:active_ad_count"`
	MaxLongevityDays *int     `json:"max_longevity_days" gorm:"column:max_longevity_days"`
	Alert            bool     `json:"alert" gorm:"column:alert"`
	RefreshedAt      *APITime `json:"refreshed_at" gorm:"column:refreshed_at"`
}

// AppInfo mirrors a public.apps row joined through public.ads.
type AppInfo struct {
	Store       string   `json:"store" gorm:"column:store"`
	StoreID     string   `json:"store_id" gorm:"column:store_id"`
	Name        *string  `json:"name" gorm:"column:name"`
	Developer   *string  `json:"developer" gorm:"column:developer"`
	Genre       *string  `json:"genre" gorm:"column:genre"`
	ReleaseDate *APIDate `json:"release_date" gorm:"column:release_date"`
	Rating      *float64 `json:"rating" gorm:"column:rating"`
	RatingCount *int     `json:"rating_count" gorm:"column:rating_count"`
	IsEmerging  *bool    `json:"is_emerging" gorm:"column:is_emerging"`
}

// FunnelInfo mirrors a public.funnels row joined through public.ads.
type FunnelInfo struct {
	LandingURL            *string `json:"landing_url" gorm:"column:landing_url"`
	FinalURLAfterRedirect *string `json:"final_url_after_redirect" gorm:"column:final_url_after_redirect"`
	ScreenshotS3Key       *string `json:"screenshot_s3_key" gorm:"column:screenshot_s3_key"`
}

// CreativeInfo mirrors a public.creatives row joined through public.ads.
type CreativeInfo struct {
	Type     *string `json:"type" gorm:"column:type"`
	S3Key    *string `json:"s3_key" gorm:"column:s3_key"`
	BodyText *string `json:"body_text" gorm:"column:body_text"`
	Title    *string `json:"title" gorm:"column:title"`
}

// GemDossier is assembled in code from several reads (not scanned directly).
type GemDossier struct {
	Gem           ShortlistRow   `json:"gem"`
	Domain        *string        `json:"domain"`
	PageCategory  *string        `json:"page_category"`
	IgUsername    *string        `json:"ig_username"`
	IgFollowers   *int           `json:"ig_followers"`
	ParentCompany *string        `json:"parent_company"`
	Apps          []AppInfo      `json:"apps"`
	Funnels       []FunnelInfo   `json:"funnels"`
	Creatives     []CreativeInfo `json:"creatives"`
}

// ScalingPoint mirrors an adtech.app_advertiser_metrics row. week_start is NOT
// NULL, so it is a value (not a pointer).
type ScalingPoint struct {
	WeekStart             APIDate `json:"week_start" gorm:"column:week_start"`
	ActiveAdCount         *int    `json:"active_ad_count" gorm:"column:active_ad_count"`
	DistinctCreativeCount *int    `json:"distinct_creative_count" gorm:"column:distinct_creative_count"`
	TotalEuReach          *int64  `json:"total_eu_reach" gorm:"column:total_eu_reach"`
}

// ReviewQueueRow is the subset of the matview shown in the review queue.
type ReviewQueueRow struct {
	AdvertiserID     int     `json:"advertiser_id" gorm:"column:advertiser_id"`
	Cohort           string  `json:"cohort" gorm:"column:cohort"`
	PageName         *string `json:"page_name" gorm:"column:page_name"`
	AppName          *string `json:"app_name" gorm:"column:app_name"`
	GemScore         float64 `json:"gem_score" gorm:"column:gem_score"`
	ActiveAdCount    *int    `json:"active_ad_count" gorm:"column:active_ad_count"`
	MaxLongevityDays *int    `json:"max_longevity_days" gorm:"column:max_longevity_days"`
}

// ReviewDecisionIn is the human verdict body. The verdict enum is enforced by
// gin's binding validator (returns 422 on a bad value, like Pydantic's Literal).
type ReviewDecisionIn struct {
	Verdict  string  `json:"verdict" binding:"required,oneof=accept reject hold"`
	Reviewer string  `json:"reviewer" binding:"required"`
	Note     *string `json:"note"`
}

// Alert mirrors monitoring.metrics.Alert (a frozen dataclass).
type Alert struct {
	Kind     string `json:"kind"`
	Message  string `json:"message"`
	Severity string `json:"severity"`
}

// HealthMetrics is a struct (not a map) so the JSON key order matches the Python
// dict's insertion order: window_hours, total, errors, ok, alerts, degraded.
type HealthMetrics struct {
	WindowHours float64 `json:"window_hours"`
	Total       int64   `json:"total"`
	Errors      int64   `json:"errors"`
	Ok          int64   `json:"ok"`
	Alerts      []Alert `json:"alerts"`
	Degraded    bool    `json:"degraded"`
}
