// Package-local: the member tool handlers. Every one reads identity from h.u
// (never from tool input) and returns structured output the SDK serializes as
// JSON text content.
package mcpserver

import (
	"context"
	"errors"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// ---- my_taste_profile ----

type tasteProfileIn struct{}

func (h *handlers) tasteProfile(ctx context.Context, _ *mcp.CallToolRequest, _ tasteProfileIn) (*mcp.CallToolResult, any, error) {
	tp, err := repositories.MyTasteProfile(ctx, db.DB(), h.u.Email)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading your taste profile.")
	}
	if tp == nil || len(tp.Counts) == 0 {
		return nil, map[string]any{"note": "No ratings yet — rate apps in the review queue first."}, nil
	}
	return nil, tp, nil
}

// ---- my_ratings ----

type myRatingsIn struct {
	Rating string `json:"rating,omitempty" jsonschema:"like, neutral, or dislike; empty for all"`
	Limit  int    `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset int    `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) myRatings(ctx context.Context, _ *mcp.CallToolRequest, in myRatingsIn) (*mcp.CallToolResult, any, error) {
	rows, total, err := repositories.MyRatings(ctx, db.DB(), h.u.Email, in.Rating, clampLimit(in.Limit), in.Offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading your ratings.")
	}
	return nil, map[string]any{"total": total, "ratings": rows}, nil
}

// ---- my_saved_gems ----

type mySavedGemsIn struct{}

func (h *handlers) mySavedGems(ctx context.Context, _ *mcp.CallToolRequest, _ mySavedGemsIn) (*mcp.CallToolResult, any, error) {
	rows, err := repositories.MySavedGems(ctx, db.DB(), h.u.Email)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading your saved gems.")
	}
	folders, err := repositories.ListSavedGroups(ctx, db.DB(), h.u.Email)
	if err != nil {
		return nil, nil, errors.New("could not read saved folders")
	}
	products, err := repositories.ListResearchProducts(ctx, db.DB(), h.u.Email)
	if err != nil {
		return nil, nil, errors.New("could not read products")
	}
	return nil, map[string]any{"saved_gems": rows, "folders": folders, "products": products}, nil
}

// ---- recommended_gems ----

type recommendedIn struct {
	Limit int `json:"limit,omitempty" jsonschema:"max gems to return (default 50, max 200)"`
}

func (h *handlers) recommended(ctx context.Context, _ *mcp.CallToolRequest, in recommendedIn) (*mcp.CallToolResult, any, error) {
	gems, personalized, err := repositories.RecommendedGems(ctx, db.DB(), h.u.Email, clampLimit(in.Limit))
	if err != nil {
		return nil, nil, errors.New("something went wrong building your recommendations.")
	}
	out := map[string]any{"personalized": personalized, "gems": gems}
	if !personalized {
		out["note"] = "No likes yet — showing GemScore order."
	}
	return nil, out, nil
}

// ---- search_gems ----

type searchGemsIn struct {
	Q         string  `json:"q,omitempty" jsonschema:"case-insensitive substring of app or page name"`
	Cohort    string  `json:"cohort,omitempty" jsonschema:"exact cohort key"`
	Category  string  `json:"category,omitempty" jsonschema:"store genre/category"`
	MinScore  float64 `json:"min_score,omitempty" jsonschema:"minimum GemScore"`
	HasFunnel bool    `json:"has_funnel,omitempty" jsonschema:"only gems with a confirmed funnel"`
	Sort      string  `json:"sort,omitempty" jsonschema:"sort key (e.g. gem_score, scaling_velocity)"`
	Dir       string  `json:"dir,omitempty" jsonschema:"asc or desc"`
	Limit     int     `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset    int     `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) searchGems(ctx context.Context, _ *mcp.CallToolRequest, in searchGemsIn) (*mcp.CallToolResult, any, error) {
	p := repositories.ShortlistParams{
		Q:         in.Q,
		MinScore:  in.MinScore,
		HasFunnel: in.HasFunnel,
		Sort:      in.Sort,
		Dir:       in.Dir,
		Limit:     clampLimit(in.Limit),
		Offset:    in.Offset,
	}
	if in.Cohort != "" {
		p.Cohort = &in.Cohort
	}
	if in.Category != "" {
		p.Category = &in.Category
	}
	rows, total, err := repositories.Shortlist(ctx, db.DB(), p)
	if err != nil {
		return nil, nil, errors.New("something went wrong searching the gem directory.")
	}
	return nil, map[string]any{"total": total, "gems": rows}, nil
}

// ---- gem_detail ----

type gemDetailIn struct {
	AdvertiserID int `json:"advertiser_id" jsonschema:"the gem's advertiser id"`
}

func (h *handlers) gemDetail(ctx context.Context, _ *mcp.CallToolRequest, in gemDetailIn) (*mcp.CallToolResult, any, error) {
	// Dossier reads the matview (frontend.emerging_scaling_apps); it MUST stay on
	// that read so non-representative advertiser ids resolve to their feed rep
	// (regression noted 2026-06-27 — the products view 404s them).
	d, err := repositories.Dossier(ctx, db.DB(), in.AdvertiserID)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading that gem.")
	}
	if d == nil {
		return nil, nil, errors.New("gem not found.")
	}
	return nil, d, nil
}
