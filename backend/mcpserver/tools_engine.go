// Package-local: admin-only engine tools. These read and steer the shared
// discovery engine, so every handler re-checks the admin role even though
// buildServer only registers them for admins.
package mcpserver

import (
	"context"
	"errors"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// ---- scraping_health (admin only) ----

type scrapingHealthIn struct {
	Days int `json:"days,omitempty" jsonschema:"discovery-rate window in days (default 14)"`
}

func (h *handlers) scrapingHealth(ctx context.Context, _ *mcp.CallToolRequest, in scrapingHealthIn) (*mcp.CallToolResult, any, error) {
	// Defense in depth: this tool is only registered on an admin's server, but
	// re-check here so a non-admin can never reach the engine internals even if
	// registration ever changes.
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	days := in.Days
	if days <= 0 {
		days = 14
	}
	g := db.DB()
	pulse, err := repositories.EnginePulse(ctx, g)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading engine health.")
	}
	rate, err := repositories.DiscoveryRate(ctx, g, days)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading engine health.")
	}
	diversity, err := repositories.IdeaDiversity(ctx, g)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading engine health.")
	}
	return nil, map[string]any{
		"pulse":          pulse,
		"discovery_rate": rate,
		"idea_diversity": diversity,
	}, nil
}
