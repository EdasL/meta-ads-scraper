package models

import (
	"encoding/json"
	"time"
)

type AdSearchRequest struct {
	Operation string   `json:"operation"`
	Queries   []string `json:"queries,omitempty"`
	PageID    string   `json:"page_id,omitempty"`
	Country   string   `json:"country"`
	MatchMode string   `json:"match_mode,omitempty"`
	Cursor    string   `json:"cursor,omitempty"`
}
type AdSearch struct {
	ID         int64           `json:"scrape_id"`
	Request    json.RawMessage `json:"request"`
	Status     string          `json:"status"`
	Progress   json.RawMessage `json:"progress"`
	ErrorText  *string         `json:"error"`
	CreatedAt  time.Time       `json:"created_at"`
	StartedAt  *time.Time      `json:"started_at"`
	FinishedAt *time.Time      `json:"finished_at"`
}
type AdSearchResult struct {
	ID             int64           `json:"id"`
	AdArchiveID    string          `json:"ad_archive_id"`
	PageID         string          `json:"page_id"`
	PageName       string          `json:"page_name"`
	SourceTerm     string          `json:"source_term"`
	Country        string          `json:"country"`
	IsActive       bool            `json:"is_active"`
	StartDate      *time.Time      `json:"start_date"`
	EndDate        *time.Time      `json:"end_date"`
	CollationCount int             `json:"collation_count"`
	PageLikeCount  *int64          `json:"page_like_count"`
	CTAType        *string         `json:"cta_type"`
	CTAText        *string         `json:"cta_text"`
	LinkURL        *string         `json:"link_url"`
	BodyText       *string         `json:"body_text"`
	CopyTruncated  bool            `json:"copy_truncated" gorm:"-"`
	ImageURLs      json.RawMessage `json:"image_urls"`
	VideoURLs      json.RawMessage `json:"video_urls"`
	CreativeHash   string          `json:"creative_hash"`
	ObservedAt     time.Time       `json:"observed_at"`
	LibraryURL     string          `json:"library_url" gorm:"-"`
}
