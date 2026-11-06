package models

import (
	"encoding/json"
	"time"
)

// HuntRow is one hunt thread in the list view. LastRoundStatus is null when the
// hunt has no rounds yet. LastActivity is the newest of the thread's create time,
// its latest message, and its latest round — the list sorts on it, and the rail
// shows it so the date matches the sort order.
type HuntRow struct {
	ID              int       `json:"id"`
	Title           string    `json:"title"`
	FolderID        *int64    `json:"folder_id" gorm:"column:folder_id"`
	CreatedAt       time.Time `json:"created_at"`
	LastActivity    time.Time `json:"last_activity" gorm:"column:last_activity"`
	LastRoundStatus *string   `json:"last_round_status" gorm:"column:last_round_status"`
}

type HuntFolderContext struct {
	FolderID int64         `json:"folder_id" gorm:"column:folder_id"`
	Name     string        `json:"name"`
	Product  string        `json:"product"`
	Brands   []FolderBrand `json:"saved_brands" gorm:"-"`
}

type FolderBrand struct {
	AdvertiserID int64           `json:"advertiser_id"`
	Name         string          `json:"name"`
	Note         string          `json:"note"`
	Description  string          `json:"description"`
	AdCopy       json.RawMessage `json:"ad_copy" gorm:"column:ad_copy"`
}

// HuntMessageRow is one transcript entry. Proposal is null for user messages and
// carries the assistant's structured HuntProposal jsonb otherwise.
type HuntMessageRow struct {
	ID        int             `json:"id"`
	Role      string          `json:"role"`
	Content   string          `json:"content"`
	Proposal  json.RawMessage `json:"proposal" gorm:"column:proposal"`
	ToolLog   json.RawMessage `json:"tool_log" gorm:"column:tool_log"`
	CreatedAt time.Time       `json:"created_at" gorm:"column:created_at"`
}

// HuntRoundRow is one approved run. Terms/Competitors/Progress are raw jsonb.
type HuntRoundRow struct {
	ID          int             `json:"id"`
	MessageID   *int            `json:"message_id" gorm:"column:message_id"`
	Terms       json.RawMessage `json:"terms" gorm:"column:terms"`
	Competitors json.RawMessage `json:"competitor_apps" gorm:"column:competitor_apps"`
	Status      string          `json:"status"`
	Progress    json.RawMessage `json:"progress" gorm:"column:progress"`
	Settings    json.RawMessage `json:"settings" gorm:"column:settings"`
	ErrorText   *string         `json:"error_text" gorm:"column:error_text"`
	CreatedAt   time.Time       `json:"created_at" gorm:"column:created_at"`
	FinishedAt  *time.Time      `json:"finished_at" gorm:"column:finished_at"`
}

// HuntRoundReport is the tool-facing status digest for a single round, scoped to
// its hunt. Triage counts are aggregated from hunt_results.
type HuntRoundReport struct {
	Status         string          `json:"status"`
	ErrorText      *string         `json:"error_text" gorm:"column:error_text"`
	StartedAt      *time.Time      `json:"started_at" gorm:"column:started_at"`
	FinishedAt     *time.Time      `json:"finished_at" gorm:"column:finished_at"`
	Settings       json.RawMessage `json:"settings" gorm:"column:settings"`
	Progress       json.RawMessage `json:"progress" gorm:"column:progress"`
	KeptCount      int             `json:"kept_count" gorm:"column:kept_count"`
	VetoedCount    int             `json:"vetoed_count" gorm:"column:vetoed_count"`
	PendingCount   int             `json:"pending_count" gorm:"column:pending_count"`
	OffTargetCount int             `json:"off_target_count" gorm:"column:off_target_count"`
}

// HuntResultRow is one (round, found page/store listing). GemScore is joined at
// read time from adtech.advertiser_scores and is null for unscored/unkept rows.
type HuntResultRow struct {
	ID               int             `json:"id"`
	RoundID          int             `json:"round_id" gorm:"column:round_id"`
	PageID           string          `json:"page_id" gorm:"column:page_id"`
	PageName         *string         `json:"page_name" gorm:"column:page_name"`
	Store            string          `json:"store"`
	StoreID          string          `json:"store_id" gorm:"column:store_id"`
	LinkURL          *string         `json:"link_url" gorm:"column:link_url"`
	SourceTerm       string          `json:"source_term" gorm:"column:source_term"`
	WasNew           bool            `json:"was_new" gorm:"column:was_new"`
	Triage           string          `json:"triage"`
	KeptAdvertiserID *int            `json:"kept_advertiser_id" gorm:"column:kept_advertiser_id"`
	Relevance        *string         `json:"relevance" gorm:"column:relevance"`
	AdEvidence       json.RawMessage `json:"ad_evidence" gorm:"column:ad_evidence"`
	RelevanceNote    *string         `json:"relevance_note" gorm:"column:relevance_note"`
	GemScore         *float64        `json:"gem_score" gorm:"column:gem_score"`
	// Page is the SERP page the result was first found on (Task 1). App fields
	// come from the Discover matview (null until it carries the advertiser);
	// Saved/Rating are the CALLER's own state, email-scoped so no other user's
	// saves leak. All nil on the tool-facing RoundResults read (not selected).
	Page    *int    `json:"page" gorm:"column:page"`
	AppName *string `json:"app_name" gorm:"column:app_name"`
	IconURL *string `json:"icon_url" gorm:"column:icon_url"`
	Tagline *string `json:"tagline" gorm:"column:tagline"`
	Cohort  *string `json:"cohort" gorm:"column:cohort"`
	Saved   bool    `json:"saved" gorm:"column:saved"`
	Rating  *string `json:"rating" gorm:"column:rating"`
}

// HuntRoundSummary is the compact per-round context handed to Gemini so it can
// avoid repeating angles: how many terms the round ran and how it fared.
type HuntRoundSummary struct {
	RoundID        int             `json:"round_id" gorm:"column:round_id"`
	Terms          json.RawMessage `json:"terms" gorm:"column:terms"`
	Settings       json.RawMessage `json:"settings" gorm:"column:settings"`
	Progress       json.RawMessage `json:"progress" gorm:"column:progress"`
	TermCount      int             `json:"term_count" gorm:"column:term_count"`
	FoundCount     int             `json:"found_count" gorm:"column:found_count"`
	KeptCount      int             `json:"kept_count" gorm:"column:kept_count"`
	RelevantCount  int             `json:"relevant_count" gorm:"column:relevant_count"`
	OffTargetCount int             `json:"off_target_count" gorm:"column:off_target_count"`
}
