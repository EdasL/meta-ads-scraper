package mcpserver

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type selectedAd struct {
	ScrapeID int64  `json:"scrape_id" jsonschema:"Your scrape containing this ad"`
	AdID     string `json:"ad_archive_id" jsonschema:"Ad ID from the scrape"`
}
type similarBrandsIn struct {
	Brief         string       `json:"brief,omitempty" jsonschema:"Advertising audience, primary problem, desired outcome or direction; required without reference ads"`
	ReferenceAds  []selectedAd `json:"reference_ads,omitempty" jsonschema:"Up to six source/reference ads selected by the calling agent"`
	SourceBrand   string       `json:"source_brand,omitempty" jsonschema:"Source brand to exclude from suggestions"`
	ExcludeBrands []string     `json:"exclude_brands,omitempty" jsonschema:"Up to twenty brands already checked or unwanted"`
	Limit         int          `json:"limit,omitempty" jsonschema:"Brand leads to suggest; default 5, maximum 10"`
}
type judgeAdsIn struct {
	Brief        string       `json:"brief,omitempty" jsonschema:"Advertising relevance criteria; required without reference ads"`
	ReferenceAds []selectedAd `json:"reference_ads,omitempty" jsonschema:"Up to six selected source/reference ads"`
	CandidateAds []selectedAd `json:"candidate_ads" jsonschema:"One to twenty candidate ads to assess, including any seed brands"`
}

func validateAdSelection(ads []selectedAd, max int) error {
	if len(ads) > max {
		return fmt.Errorf("select at most %d ads", max)
	}
	seen := map[selectedAd]bool{}
	for _, a := range ads {
		if a.ScrapeID <= 0 || a.AdID == "" || len(a.AdID) > 100 || strings.TrimSpace(a.AdID) != a.AdID || seen[a] {
			return errors.New("invalid or duplicate scrape_id/ad_archive_id pair")
		}
		seen[a] = true
	}
	return nil
}
func (h *handlers) adResearchEvidence(ctx context.Context, ads []selectedAd) ([]helpers.AdResearchEvidence, error) {
	out := make([]helpers.AdResearchEvidence, 0, len(ads))
	for _, a := range ads {
		row, err := repositories.AdScrapeAd(ctx, db.DB(), h.u.Email, a.ScrapeID, a.AdID)
		if err != nil || row == nil {
			return nil, errors.New("selected ad not found in your scrapes")
		}
		body := ""
		if row.BodyText != nil {
			body = *row.BodyText
		}
		text, truncated := helpers.AdEvidenceText(body)
		out = append(out, helpers.AdResearchEvidence{ID: fmt.Sprintf("%d:%s", a.ScrapeID, a.AdID), Text: text, Truncated: truncated})
	}
	return out, nil
}
func (h *handlers) adResearchBrief(ctx context.Context, brief string, refs []selectedAd) (helpers.AdResearchBrief, error) {
	in := helpers.AdResearchBrief{Brief: strings.TrimSpace(brief)}
	if utf8.RuneCountInString(in.Brief) > 4000 {
		return in, errors.New("brief exceeds 4000 characters")
	}
	if err := validateAdSelection(refs, 6); err != nil {
		return in, err
	}
	if in.Brief == "" && len(refs) == 0 {
		return in, errors.New("provide a brief or selected reference_ads; a brand name alone is insufficient")
	}
	var err error
	in.References, err = h.adResearchEvidence(ctx, refs)
	if err != nil {
		return in, err
	}
	if in.Brief == "" {
		substance := false
		for _, ad := range in.References {
			substance = substance || len(strings.Fields(ad.Text)) >= 8
		}
		if !substance {
			return in, errors.New("reference copy is sparse; inspect the media and provide an evidence-based brief")
		}
	}
	return in, nil
}
func (h *handlers) findSimilarBrands(ctx context.Context, _ *mcp.CallToolRequest, in similarBrandsIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	if in.Limit < 0 || in.Limit > 10 || utf8.RuneCountInString(in.SourceBrand) > 200 || len(in.ExcludeBrands) > 20 {
		return nil, nil, errors.New("invalid limit, source_brand or exclude_brands")
	}
	for _, name := range in.ExcludeBrands {
		if utf8.RuneCountInString(name) > 100 {
			return nil, nil, errors.New("excluded brand exceeds 100 characters")
		}
	}
	if in.Limit == 0 {
		in.Limit = 5
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	brief, err := h.adResearchBrief(ctx, in.Brief, in.ReferenceAds)
	if err != nil {
		return nil, nil, err
	}
	if os.Getenv("GEMINI_API_KEY") == "" {
		return nil, nil, errors.New("similar-brand finder unavailable: configure GEMINI_API_KEY on the API server")
	}
	result, err := helpers.NewGeminiHelper().FindSimilarAdBrands(ctx, brief, in.SourceBrand, in.ExcludeBrands, in.Limit)
	if err != nil {
		return nil, nil, err
	}
	return nil, map[string]any{"suggestions": result.Brands, "search_terms": result.SearchTerms,
		"model": result.Model, "evidence_source": result.EvidenceSource, "reference_ads": in.ReferenceAds,
		"active_ads_verified": false, "next_step": "These are model-suggested leads. Resolve them with find_advertisers, select page IDs, fetch actual ads with get_advertiser_ads, then assess relevance. No scrape was started."}, nil
}
func (h *handlers) judgeAds(ctx context.Context, _ *mcp.CallToolRequest, in judgeAdsIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	if len(in.CandidateAds) == 0 {
		return nil, nil, errors.New("select one to twenty candidate_ads")
	}
	if err := validateAdSelection(in.CandidateAds, 20); err != nil {
		return nil, nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, 25*time.Second)
	defer cancel()
	brief, err := h.adResearchBrief(ctx, in.Brief, in.ReferenceAds)
	if err != nil {
		return nil, nil, err
	}
	ads, err := h.adResearchEvidence(ctx, in.CandidateAds)
	if err != nil {
		return nil, nil, err
	}
	verdicts, err := (helpers.JevAdJudge{APIKey: os.Getenv("TYPESAFE_API_KEY")}).JudgeAds(ctx, brief, ads)
	if err != nil {
		return nil, nil, err
	}
	partial := false
	type scopedJudgement struct {
		selectedAd
		helpers.AdJudgement
	}
	results := make([]scopedJudgement, 0, len(verdicts))
	for i, v := range verdicts {
		partial = partial || v.Reason == "provider_unavailable" || v.Reason == "invalid_provider_output"
		results = append(results, scopedJudgement{selectedAd: in.CandidateAds[i], AdJudgement: v})
	}
	return nil, map[string]any{"judgements": results, "candidate_ads": in.CandidateAds, "reference_ads": in.ReferenceAds,
		"partial": partial, "advisory": true, "notes": []string{"Jev probabilities are model estimates, not measured accuracy or ad performance.", "Only selected ad copy is assessed; media is unexamined. Full snapshots remain unclassified and unchanged.", "Inspect uncertain results and the actual offer before deciding which advertisers to keep."}}, nil
}
