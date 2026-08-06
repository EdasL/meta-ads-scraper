// Package-local: read-only history tools. These answer "what has the engine
// found, and which queries produced it" — the inputs to steering decisions
// made through tools_engine.go.
package mcpserver

import (
	"context"
	"errors"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// ---- keyword_yield (admin only) ----

type keywordYieldIn struct {
	Source    string `json:"source,omitempty" jsonschema:"filter by mint source (e.g. gem_copy, frame_mint, experiment_angles, mcp)"`
	Status    string `json:"status,omitempty" jsonschema:"pending, trial, active, resting, or retired"`
	MinSweeps int    `json:"min_sweeps,omitempty" jsonschema:"only keywords swept at least this many times"`
	Limit     int    `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset    int    `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) keywordYield(ctx context.Context, _ *mcp.CallToolRequest, in keywordYieldIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	rows, err := repositories.KeywordYield(ctx, db.DB(), repositories.KeywordYieldParams{
		Source:    in.Source,
		Status:    in.Status,
		MinSweeps: in.MinSweeps,
		Limit:     clampLimit(in.Limit),
		Offset:    in.Offset,
	})
	if err != nil {
		return nil, nil, errors.New("something went wrong reading keyword yield.")
	}
	if len(rows) == 0 {
		return nil, map[string]any{"keywords": rows, "note": "No keywords matched those filters."}, nil
	}
	return nil, map[string]any{"keywords": rows}, nil
}

// ---- hunt_term_yield (admin only) ----

type huntTermYieldIn struct {
	RoundID int `json:"round_id,omitempty" jsonschema:"scope to one hunt round; omit for every round"`
	Limit   int `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset  int `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) huntTermYield(ctx context.Context, _ *mcp.CallToolRequest, in huntTermYieldIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	var round *int
	if in.RoundID > 0 {
		round = &in.RoundID
	}
	rows, err := repositories.HuntTermYield(ctx, db.DB(), round, clampLimit(in.Limit), in.Offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading hunt term yield.")
	}
	if len(rows) == 0 {
		return nil, map[string]any{"terms": rows, "note": "No hunt results yet."}, nil
	}
	return nil, map[string]any{"terms": rows}, nil
}

// ---- users_overview (admin only) ----

type usersOverviewIn struct {
	Limit  int `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset int `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) usersOverview(ctx context.Context, _ *mcp.CallToolRequest, in usersOverviewIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	rows, err := repositories.UsersOverview(ctx, db.DB(), clampLimit(in.Limit), in.Offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading the user list.")
	}
	return nil, map[string]any{"users": rows}, nil
}

// ---- ad_creatives ----

type adCreativesIn struct {
	AdvertiserID int `json:"advertiser_id" jsonschema:"the advertiser whose creatives to list"`
	Limit        int `json:"limit,omitempty" jsonschema:"max rows to return (default 50, max 200)"`
	Offset       int `json:"offset,omitempty" jsonschema:"rows to skip for paging"`
}

func (h *handlers) adCreatives(ctx context.Context, _ *mcp.CallToolRequest, in adCreativesIn) (*mcp.CallToolResult, any, error) {
	if in.AdvertiserID <= 0 {
		return nil, nil, errors.New("advertiser_id is required.")
	}
	rows, err := repositories.AdCreatives(ctx, db.DB(), in.AdvertiserID, clampLimit(in.Limit), in.Offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading those creatives.")
	}
	for i := range rows {
		if rows[i].S3Key != nil {
			rows[i].URL = h.base + "/api/media/" + *rows[i].S3Key
		}
	}
	if len(rows) == 0 {
		return nil, map[string]any{"creatives": rows, "note": "No stored creatives for that advertiser."}, nil
	}
	return nil, map[string]any{"creatives": rows}, nil
}
