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
