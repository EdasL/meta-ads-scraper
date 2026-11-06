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
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
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
// patience 1-10, listing_pages 1-30, min_pages 1-30, skip_pages 0-30); Go
// passes values through so there is exactly one place that decides what is
// legal. skip_pages is the one knob whose floor is 0, and 0 is also its
// default, so omitempty dropping it changes nothing the worker sees.
type huntSettingsIn struct {
	PageCap      int `json:"page_cap,omitempty" jsonschema:"max pages per term, 1-100 (default 40)"`
	Patience     int `json:"patience,omitempty" jsonschema:"consecutive pages with nothing new before giving up on a term, 1-10 (default 6)"`
	ListingPages int `json:"listing_pages,omitempty" jsonschema:"pages pulled per competitor page listing, 1-30 (default 10)"`
	MinPages     int `json:"min_pages,omitempty" jsonschema:"pages to read before the patience exit may fire, 1-30 (default 10)"`
	SkipPages    int `json:"skip_pages,omitempty" jsonschema:"early pages to fetch and discard unread, 0-30 (default 0); shallow pages are mostly advertisers already in the corpus. Must be below page_cap, or the term is not searched"`
}

type runHuntIn struct {
	FolderID    *int64         `json:"folder_id,omitempty" jsonschema:"owned saved folder whose attached product and saved brands guide relevance; only when starting a new hunt"`
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
	if in.HuntID != 0 && in.FolderID != nil {
		return nil, nil, errors.New("an existing hunt already has its folder context; omit folder_id")
	}
	// Same normalization and bounds as the REST hunt path, from the one place
	// that defines them. Terms are lowercased, trimmed and deduped (they are
	// search keys, so "Sleep Tracker" and "sleep tracker" are one sweep);
	// competitors keep their case. Both helpers return a non-nil empty slice, so
	// this also settles the nil-slice trap: json.Marshal renders a nil slice as
	// the literal `null` rather than `[]`, and since that is four bytes, not
	// zero, the repository's empty-value default never fires and the column
	// stores a JSON null. A jsonb null is not a SQL NULL, so NOT NULL does not
	// object either — the round is written, looks well formed, and then kills the
	// worker on `list(rnd.terms)`.
	in.Terms = helpers.NormalizeHuntTerms(in.Terms)
	in.Competitors = helpers.TrimHuntCompetitors(in.Competitors)

	// Emptiness is checked after normalization so a list of blanks is rejected
	// rather than reaching the worker as a sweep for "".
	if len(in.Terms) == 0 && len(in.Competitors) == 0 {
		return nil, nil, errors.New("provide at least one term or one competitor.")
	}
	// A round is real spend, and the worker consumes every term it is handed, so
	// the caps are enforced rather than silently truncated: dropping terms the
	// caller asked for would hide the cost decision from them.
	if len(in.Terms) > helpers.MaxHuntTerms {
		return nil, nil, fmt.Errorf("at most %d terms per round.", helpers.MaxHuntTerms)
	}
	if len(in.Competitors) > helpers.MaxHuntCompetitors {
		return nil, nil, fmt.Errorf("at most %d competitors per round.", helpers.MaxHuntCompetitors)
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
		huntID, err = repositories.CreateHuntInFolder(ctx, db.DB(), h.u.Email, title, in.FolderID)
		if errors.Is(err, repositories.ErrFolderNotFound) {
			return nil, nil, errors.New("folder not found")
		}
		if err != nil {
			return nil, nil, errors.New("something went wrong starting the hunt.")
		}
	} else {
		// A supplied hunt_id has to be one the caller owns. The REST path 404s on
		// a miss; without this check a round would be injected into a thread
		// belonging to someone else, where its owner would read it as their own
		// work. HuntOwner returns "" when the hunt does not exist, so both the
		// missing and the foreign case land on the same message and the tool does
		// not tell a caller which other accounts have hunts.
		owner, oerr := repositories.HuntOwner(ctx, db.DB(), huntID)
		if oerr != nil {
			return nil, nil, errors.New("something went wrong reading that hunt.")
		}
		if owner != h.u.Email {
			return nil, nil, errors.New("that hunt was not found.")
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
	Limit   int `json:"limit,omitempty" jsonschema:"max results to return (default 50, max 200)"`
	Offset  int `json:"offset,omitempty" jsonschema:"results to skip for paging"`
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
	limit := clampLimit(in.Limit)
	offset := in.Offset
	if offset < 0 {
		offset = 0
	}
	results, err := repositories.RoundResults(ctx, db.DB(), huntID, in.RoundID, "", limit, offset)
	if err != nil {
		return nil, nil, errors.New("something went wrong reading that round's results.")
	}
	out := map[string]any{
		"hunt_id": huntID,
		"status":  report.Status,
		"round":   report,
		"results": results,
		"limit":   limit,
		"offset":  offset,
	}
	// A round routinely returns more than one page. The triage counts on the
	// report make a short page visible but not addressable, so say plainly when
	// there is more and how to ask for it.
	if len(results) == limit {
		out["note"] = fmt.Sprintf(
			"Showing results %d-%d. There may be more; call again with offset %d.",
			offset+1, offset+len(results), offset+limit)
	}
	return nil, out, nil
}
