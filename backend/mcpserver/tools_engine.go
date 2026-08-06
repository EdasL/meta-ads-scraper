// Package-local: admin-only engine tools. These read and steer the shared
// discovery engine, so every handler re-checks the admin role even though
// buildServer only registers them for admins.
package mcpserver

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

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

// ---- keyword_control (admin only) ----

// defaultKeywordCooldownDays matches the flywheel's own resting cooldown so a
// manual rest behaves like an automatic one unless the caller overrides it.
const defaultKeywordCooldownDays = 7

type keywordControlIn struct {
	Action       string   `json:"action" jsonschema:"activate, rest, retire, or seed"`
	Keywords     []string `json:"keywords" jsonschema:"the keywords to act on"`
	CooldownDays int      `json:"cooldown_days,omitempty" jsonschema:"rest cooldown in days (default 7); ignored by other actions"`
}

func (h *handlers) keywordControl(ctx context.Context, _ *mcp.CallToolRequest, in keywordControlIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	switch in.Action {
	case "activate", "rest", "retire", "seed":
	default:
		return nil, nil, errors.New("action must be activate, rest, retire, or seed.")
	}
	if len(in.Keywords) == 0 {
		return nil, nil, errors.New("keywords must not be empty.")
	}
	cooldown := in.CooldownDays
	if cooldown <= 0 {
		cooldown = defaultKeywordCooldownDays
	}
	changed, unknown, err := repositories.ControlKeywords(ctx, db.DB(), in.Action, in.Keywords, cooldown)
	if err != nil {
		return nil, nil, errors.New("something went wrong updating those keywords.")
	}
	out := map[string]any{"changed": changed, "unknown": unknown}
	out["note"] = "Takes effect on the next flywheel tick."
	return nil, out, nil
}

// ---- run_hunt (admin only) ----

// huntSettingsIn mirrors the per-round knobs the Python executor reads. Bounds
// are enforced by clamp_hunt_settings on the worker side (page_cap 1-100,
// patience 1-10, listing_pages 1-30, min_pages 1-30); Go passes values through
// so there is exactly one place that decides what is legal.
type huntSettingsIn struct {
	PageCap      int `json:"page_cap,omitempty" jsonschema:"max pages per term, 1-100 (default 40)"`
	Patience     int `json:"patience,omitempty" jsonschema:"consecutive pages with nothing new before giving up on a term, 1-10 (default 6)"`
	ListingPages int `json:"listing_pages,omitempty" jsonschema:"pages pulled per competitor page listing, 1-30 (default 10)"`
	MinPages     int `json:"min_pages,omitempty" jsonschema:"pages to read before the patience exit may fire, 1-30 (default 10)"`
}

type runHuntIn struct {
	Terms       []string       `json:"terms,omitempty" jsonschema:"keyword searches, US only, paged until dry"`
	Competitors []string       `json:"competitors,omitempty" jsonschema:"app or brand names; each is resolved to its Ad Library page and its whole ad set is pulled, globally"`
	Settings    huntSettingsIn `json:"settings,omitempty" jsonschema:"per-round depth knobs"`
	Intent      string         `json:"intent,omitempty" jsonschema:"what this round is looking for; used to judge result relevance"`
	HuntID      int            `json:"hunt_id,omitempty" jsonschema:"add the round to an existing hunt thread; omit to start a new one"`
}

func (h *handlers) runHunt(ctx context.Context, _ *mcp.CallToolRequest, in runHuntIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	if len(in.Terms) == 0 && len(in.Competitors) == 0 {
		return nil, nil, errors.New("provide at least one term or one competitor.")
	}

	terms, err := json.Marshal(in.Terms)
	if err != nil {
		return nil, nil, errors.New("terms could not be encoded.")
	}
	competitors, err := json.Marshal(in.Competitors)
	if err != nil {
		return nil, nil, errors.New("competitors could not be encoded.")
	}
	settings, err := json.Marshal(in.Settings)
	if err != nil {
		return nil, nil, errors.New("settings could not be encoded.")
	}

	huntID := in.HuntID
	if huntID == 0 {
		title := in.Intent
		if title == "" {
			title = fmt.Sprintf("MCP hunt: %d terms, %d competitors", len(in.Terms), len(in.Competitors))
		}
		if len(title) > 120 {
			title = title[:120]
		}
		huntID, err = repositories.CreateHunt(ctx, db.DB(), h.u.Email, title)
		if err != nil {
			return nil, nil, errors.New("something went wrong starting the hunt.")
		}
	}

	var intent *string
	if in.Intent != "" {
		intent = &in.Intent
	}
	roundID, err := repositories.InsertHuntRound(ctx, db.DB(), huntID, nil, terms, competitors, settings, intent)
	if err != nil {
		return nil, nil, errors.New("something went wrong queueing that round.")
	}
	return nil, map[string]any{
		"hunt_id":  huntID,
		"round_id": roundID,
		"status":   "queued",
		"note":     "The worker picks this up on its next poll. Check progress with hunt_status.",
	}, nil
}

// ---- hunt_status (admin only) ----

type huntStatusIn struct {
	RoundID int `json:"round_id" jsonschema:"the round to poll"`
}

func (h *handlers) huntStatus(ctx context.Context, _ *mcp.CallToolRequest, in huntStatusIn) (*mcp.CallToolResult, any, error) {
	if h.u.Role != "admin" {
		return nil, nil, errors.New("This tool requires an admin account.")
	}
	if in.RoundID <= 0 {
		return nil, nil, errors.New("round_id is required.")
	}
	// RoundReport and RoundResults are both hunt-scoped, so resolve the round's
	// hunt first. A round created by run_hunt belongs to the caller, but this is
	// read-only over the shared engine and the tool is already admin-gated, so
	// ownership is not re-checked here.
	huntID, _, err := repositories.HuntForRound(ctx, db.DB(), in.RoundID)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading that round.")
	}
	if huntID == 0 {
		return nil, nil, errors.New("round not found.")
	}
	report, err := repositories.RoundReport(ctx, db.DB(), huntID, in.RoundID)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading that round.")
	}
	if report == nil {
		return nil, nil, errors.New("round not found.")
	}
	results, err := repositories.RoundResults(ctx, db.DB(), huntID, in.RoundID, "", clampLimit(0))
	if err != nil {
		return nil, nil, errors.New("something went wrong reading that round's results.")
	}
	return nil, map[string]any{
		"hunt_id": huntID,
		"status":  report.Status,
		"round":   report,
		"results": results,
	}, nil
}
