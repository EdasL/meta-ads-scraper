package mcpserver

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

//go:embed ad_research_guide.md
var adResearchGuide string

const countryCodes = " AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW "

type adQueriesIn struct {
	Queries        []string `json:"queries" jsonschema:"One to three advertiser names or keyword phrases"`
	Country        string   `json:"country,omitempty" jsonschema:"ISO country code or ALL; default US"`
	MatchMode      string   `json:"match_mode,omitempty" jsonschema:"Keyword search: exact (default) or broad"`
	Refresh        bool     `json:"refresh,omitempty" jsonschema:"Bypass six-hour cache"`
	IdempotencyKey string   `json:"idempotency_key,omitempty" jsonschema:"Optional retry key, identical requests only"`
}
type advertiserLookupIn struct {
	Queries        []string `json:"queries" jsonschema:"One to three advertiser names"`
	Country        string   `json:"country,omitempty" jsonschema:"ISO country code or ALL; default US"`
	Refresh        bool     `json:"refresh,omitempty" jsonschema:"Bypass six-hour cache"`
	IdempotencyKey string   `json:"idempotency_key,omitempty" jsonschema:"Optional retry key, identical requests only"`
}
type pageAdsIn struct {
	PageID         string `json:"page_id" jsonschema:"Numeric Meta page ID from find_advertisers"`
	Country        string `json:"country,omitempty" jsonschema:"ISO country code or ALL; default US"`
	Refresh        bool   `json:"refresh,omitempty" jsonschema:"Bypass six-hour cache"`
	IdempotencyKey string `json:"idempotency_key,omitempty" jsonschema:"Optional retry key, identical requests only"`
}
type scrapeStatusIn struct {
	ScrapeID int64 `json:"scrape_id" jsonschema:"ID returned by a scraping tool"`
	Limit    int   `json:"limit,omitempty" jsonschema:"Stored ads per response; default 10, maximum 50"`
	Offset   int   `json:"offset,omitempty" jsonschema:"Zero-based offset through stored ads"`
}
type nextAdPageIn struct {
	ScrapeID  int64  `json:"scrape_id" jsonschema:"Previous completed scrape ID"`
	SourceKey string `json:"source_key,omitempty" jsonschema:"Key from sources; may be omitted for a single-source scrape"`
}

type adCopyIn struct {
	ScrapeID int64  `json:"scrape_id" jsonschema:"Scrape containing the ad"`
	AdID     string `json:"ad_archive_id" jsonschema:"Ad ID from the scrape"`
	Offset   int    `json:"offset,omitempty" jsonschema:"Character offset; default 0"`
	Limit    int    `json:"limit,omitempty" jsonschema:"Characters to return; default 6000, maximum 12000"`
}

func (h *handlers) readAdCopy(ctx context.Context, _ *mcp.CallToolRequest, in adCopyIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("this tool requires an admin account")
	}
	if in.ScrapeID <= 0 || in.AdID == "" || len(in.AdID) > 100 || in.Offset < 0 || in.Limit < 0 || in.Limit > 12000 {
		return nil, nil, errors.New("invalid scrape_id, ad_archive_id, offset or limit")
	}
	if in.Limit == 0 {
		in.Limit = 6000
	}
	body, err := repositories.AdScrapeCopy(ctx, db.DB(), h.u.Email, in.ScrapeID, in.AdID)
	if err != nil || body == nil {
		return nil, nil, errors.New("scraped ad not found")
	}
	chars := []rune(*body)
	if in.Offset > len(chars) {
		return nil, nil, errors.New("offset exceeds stored copy length")
	}
	end := min(len(chars), in.Offset+in.Limit)
	var next any
	if end < len(chars) {
		next = end
	}
	return nil, map[string]any{"ad_archive_id": in.AdID, "text": string(chars[in.Offset:end]), "offset": in.Offset, "total_characters": len(chars), "next_offset": next, "has_more": end < len(chars)}, nil
}

func scrapeCountry(value string) (string, error) {
	country := strings.ToUpper(strings.TrimSpace(value))
	if country == "" {
		country = "US"
	}
	if country != "ALL" && (len(country) != 2 || !strings.Contains(countryCodes, " "+country+" ")) {
		return "", errors.New("country must be an ISO country code or ALL")
	}
	return country, nil
}
func normalizeScrape(in adQueriesIn, operation string) (models.AdSearchRequest, error) {
	r := models.AdSearchRequest{Operation: operation}
	var err error
	r.Country, err = scrapeCountry(in.Country)
	if err != nil {
		return r, err
	}
	if len(in.Queries) < 1 || len(in.Queries) > 3 {
		return r, errors.New("provide one to three queries")
	}
	seen := map[string]bool{}
	for _, q := range in.Queries {
		q = strings.TrimSpace(q)
		if q == "" || utf8.RuneCountInString(q) > 200 {
			return r, errors.New("queries must contain 1–200 characters")
		}
		k := strings.ToLower(q)
		if !seen[k] {
			r.Queries = append(r.Queries, q)
			seen[k] = true
		}
	}
	if operation == "keyword" {
		r.MatchMode = strings.ToLower(strings.TrimSpace(in.MatchMode))
		if r.MatchMode == "" {
			r.MatchMode = "exact"
		}
		if r.MatchMode != "exact" && r.MatchMode != "broad" {
			return r, errors.New("match_mode must be exact or broad")
		}
	} else if in.MatchMode != "" {
		return r, errors.New("match_mode applies only to search_meta_ads")
	}
	return r, nil
}
func (h *handlers) researchGuide(context.Context, *mcp.CallToolRequest, struct{}) (*mcp.CallToolResult, any, error) {
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: adResearchGuide}}}, nil, nil
}
func (h *handlers) startScrape(ctx context.Context, r models.AdSearchRequest, key string, refresh bool) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("this tool requires an admin account")
	}
	key = strings.TrimSpace(key)
	if utf8.RuneCountInString(key) > 128 {
		return nil, nil, errors.New("idempotency_key exceeds 128 characters")
	}
	s, err := repositories.CreateAdSearch(ctx, db.DB(), h.u.Email, r, key, refresh)
	if err != nil {
		if errors.Is(err, repositories.ErrAdSearchConflict) || errors.Is(err, repositories.ErrAdSearchCapacity) {
			return nil, nil, err
		}
		return nil, nil, errors.New("could not queue scrape")
	}
	return h.adScrapeStatus(ctx, nil, scrapeStatusIn{ScrapeID: s.ID})
}
func (h *handlers) findAdvertisers(ctx context.Context, _ *mcp.CallToolRequest, in advertiserLookupIn) (*mcp.CallToolResult, any, error) {
	r, err := normalizeScrape(adQueriesIn{Queries: in.Queries, Country: in.Country}, "resolve")
	if err != nil {
		return nil, nil, err
	}
	return h.startScrape(ctx, r, in.IdempotencyKey, in.Refresh)
}
func (h *handlers) searchMetaAds(ctx context.Context, _ *mcp.CallToolRequest, in adQueriesIn) (*mcp.CallToolResult, any, error) {
	r, err := normalizeScrape(in, "keyword")
	if err != nil {
		return nil, nil, err
	}
	return h.startScrape(ctx, r, in.IdempotencyKey, in.Refresh)
}
func (h *handlers) getAdvertiserAds(ctx context.Context, _ *mcp.CallToolRequest, in pageAdsIn) (*mcp.CallToolResult, any, error) {
	id := strings.TrimSpace(in.PageID)
	if len(id) == 0 || len(id) > 32 || strings.Trim(id, "0123456789") != "" {
		return nil, nil, errors.New("page_id must be numeric")
	}
	country, err := scrapeCountry(in.Country)
	if err != nil {
		return nil, nil, err
	}
	return h.startScrape(ctx, models.AdSearchRequest{Operation: "page", PageID: id, Country: country}, in.IdempotencyKey, in.Refresh)
}
func (h *handlers) nextAdPage(ctx context.Context, _ *mcp.CallToolRequest, in nextAdPageIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("this tool requires an admin account")
	}
	s, err := repositories.GetAdSearch(ctx, db.DB(), h.u.Email, in.ScrapeID)
	if err != nil || s == nil {
		return nil, nil, errors.New("scrape not found")
	}
	if s.Status != "done" {
		return nil, nil, errors.New("wait until the scrape completes before requesting another page")
	}
	var req models.AdSearchRequest
	if json.Unmarshal(s.Request, &req) != nil || req.Operation == "resolve" {
		return nil, nil, errors.New("this operation has no ad pages")
	}
	var progress struct {
		Sources map[string]struct {
			HasMore bool   `json:"has_more"`
			Cursor  string `json:"cursor"`
			Query   string `json:"query"`
			PageID  string `json:"page_id"`
		} `json:"sources"`
	}
	if json.Unmarshal(s.Progress, &progress) != nil {
		return nil, nil, errors.New("invalid stored progress")
	}
	key := in.SourceKey
	if key == "" && len(progress.Sources) == 1 {
		for k := range progress.Sources {
			key = k
		}
	}
	source, ok := progress.Sources[key]
	if !ok || !source.HasMore || source.Cursor == "" {
		return nil, nil, errors.New("source_key must identify a source with has_more=true")
	}
	req.Cursor = source.Cursor
	if req.Operation == "keyword" {
		req.Queries = []string{source.Query}
	} else {
		req.PageID = source.PageID
	}
	return h.startScrape(ctx, req, fmt.Sprintf("next:%d:%x", s.ID, sha256.Sum256([]byte(key))), false)
}
func (h *handlers) adScrapeStatus(ctx context.Context, _ *mcp.CallToolRequest, in scrapeStatusIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("this tool requires an admin account")
	}
	if in.ScrapeID <= 0 || in.Offset < 0 || in.Limit < 0 || in.Limit > 50 {
		return nil, nil, errors.New("invalid scrape_id, offset or limit")
	}
	if in.Limit == 0 {
		in.Limit = 10
	}
	s, err := repositories.GetAdSearch(ctx, db.DB(), h.u.Email, in.ScrapeID)
	if err != nil || s == nil {
		return nil, nil, errors.New("scrape not found")
	}
	rows, err := repositories.AdScrapeResults(ctx, db.DB(), h.u.Email, s.ID, in.Limit+1, in.Offset)
	if err != nil {
		return nil, nil, errors.New("could not read scraped ads")
	}
	more := len(rows) > in.Limit
	if more {
		rows = rows[:in.Limit]
	}
	for i := range rows {
		rows[i].LibraryURL = "https://www.facebook.com/ads/library/?id=" + url.QueryEscape(rows[i].AdArchiveID)
		if rows[i].BodyText != nil {
			copy := []rune(*rows[i].BodyText)
			if len(copy) > 6000 {
				bounded := string(copy[:3500]) + "\n[Middle omitted; use read_ad_copy for full copy]\n" + string(copy[len(copy)-2500:])
				rows[i].BodyText = &bounded
				rows[i].CopyTruncated = true
			}
		}
	}
	progress := map[string]any{}
	_ = json.Unmarshal(s.Progress, &progress)
	sources, _ := progress["sources"].(map[string]any)
	for _, value := range sources {
		if source, ok := value.(map[string]any); ok {
			delete(source, "cursor")
		}
	}
	delete(progress, "sources")
	if sources == nil {
		sources = map[string]any{}
	}
	active := s.Status == "queued" || s.Status == "running"
	poll := 0
	if active {
		poll = 2
	}
	var next any
	if more {
		next = in.Offset + len(rows)
	}
	return nil, map[string]any{"scrape_id": s.ID, "status": s.Status, "progress": progress, "sources": sources, "ads": rows,
		"error": s.ErrorText, "poll_after_seconds": poll, "has_more_results": more, "next_offset": next, "provisional": active,
		"observed_at": s.FinishedAt, "cache_age_seconds": int(time.Since(s.CreatedAt).Seconds()),
		"interpretation": "unclassified", "next_step": "Poll ad_scrape_status while running. Read ad_research_guide; the calling agent judges relevance. next_ad_page fetches more Meta results for a source with has_more=true."}, nil
}
