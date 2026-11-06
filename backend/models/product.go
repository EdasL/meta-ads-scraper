package models

import "encoding/json"

// ResearchProduct is an owned reference product, independent of saved competitors.
type ResearchProduct struct {
	ID                int64           `json:"id"`
	Name              string          `json:"name"`
	URL               string          `json:"url"`
	Description       string          `json:"description"`
	PageID            string          `json:"page_id"`
	Store             string          `json:"store"`
	StoreID           string          `json:"store_id"`
	AdvertiserID      *int64          `json:"advertiser_id"`
	SourceDescription string          `json:"source_description"`
	AdCopy            json.RawMessage `json:"ad_copy"`
}

type ProductMatch struct {
	PageID      string `json:"page_id"`
	Name        string `json:"name"`
	Description string `json:"description"`
}

type ProductResolution struct {
	URL         string         `json:"url"`
	IdentityKey string         `json:"-"`
	PageID      string         `json:"page_id"`
	Store       string         `json:"store"`
	StoreID     string         `json:"store_id"`
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Matches     []ProductMatch `json:"matches" gorm:"-"`
}
