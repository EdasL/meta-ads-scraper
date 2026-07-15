// Package helpers holds small, dependency-light service helpers. gemini.go wraps
// the google.golang.org/genai client for structured-JSON generation, mirroring
// the proven pattern in femrun/api/helpers/gemini.go (same package version,
// model, retry discipline). The only product-specific piece is GenerateExplainer.
package helpers

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"google.golang.org/genai"
)

// GeminiHelper wraps a genai client bound to a single model.
type GeminiHelper struct {
	client       *genai.Client
	model        string
	huntModel    string
	huntWebTools bool
}

// NewGeminiHelper builds a helper from GEMINI_API_KEY. It returns nil when the
// key is unset (callers nil-check and skip generation) — the same fail-soft
// contract femrun uses, so a missing key disables AI features instead of
// crashing the process.
func NewGeminiHelper() *GeminiHelper {
	apiKey := os.Getenv("GEMINI_API_KEY")
	if apiKey == "" {
		log.Println("GEMINI_API_KEY not set, AI explainer generation disabled")
		return nil
	}

	client, err := genai.NewClient(context.Background(), &genai.ClientConfig{
		APIKey:  apiKey,
		Backend: genai.BackendGeminiAPI,
	})
	if err != nil {
		log.Printf("Failed to create Gemini client: %v", err)
		return nil
	}

	model := os.Getenv("GEMINI_MODEL")
	if model == "" {
		model = "gemini-2.5-flash" // gemini-2.0-flash was retired; override via GEMINI_MODEL
	}
	huntModel := os.Getenv("GEMINI_HUNT_MODEL")
	if huntModel == "" {
		// gemini-2.5-flash rejects built-in tools combined with function
		// calling; the hunt agent needs a Gemini 3 model.
		huntModel = "gemini-3.5-flash"
	}
	wt := strings.ToLower(strings.TrimSpace(os.Getenv("AGF_HUNT_WEB_TOOLS")))
	huntWebTools := wt != "0" && wt != "false"
	return &GeminiHelper{
		client:       client,
		model:        model,
		huntModel:    huntModel,
		huntWebTools: huntWebTools,
	}
}

// ExplainerCreative is one scraped ad creative fed to the model as source
// material. Title and BodyText come straight from public.creatives.
type ExplainerCreative struct {
	Title    string
	BodyText string
}

// ExplainerInput is the grounding material for one advertiser's explainer. The
// LLM is told to use ONLY these fields — app data + the real scraped ad copy —
// so the output stays factual.
type ExplainerInput struct {
	AppName        string
	Genre          string
	AppDescription string
	Creatives      []ExplainerCreative
	CTA            string
	LandingURL     string
}

// Explainer is the structured explainer output. The JSON tags match both the
// genai ResponseSchema property names and the adtech.advertiser_explainers
// columns, so the same shape round-trips model → struct → DB.
type Explainer struct {
	WhatItDoes    string `json:"what_it_does"`
	ProblemSolved string `json:"problem_solved"`
	WhyWorking    string `json:"why_working"`
}

const explainerSystemInstruction = "You write short, factual product notes in plain, professional language. " +
	"Use ONLY the ad copy and app data provided. Never invent features or claims. If unsure, say less."

// GenerateExplainer asks Gemini for a three-field plain-voice explainer grounded
// in the supplied ad copy + app data. It uses structured JSON (ResponseSchema)
// and the same 3-attempt exponential backoff as femrun. Returns an error
// (callers skip) when the helper is nil or every attempt fails.
func (h *GeminiHelper) GenerateExplainer(ctx context.Context, in ExplainerInput) (Explainer, error) {
	if h == nil {
		return Explainer{}, fmt.Errorf("gemini helper not initialized")
	}

	schema := &genai.Schema{
		Type: genai.TypeObject,
		Properties: map[string]*genai.Schema{
			"what_it_does": {
				Type:        genai.TypeString,
				Description: "What the app does, in plain language. One or two short sentences.",
			},
			"problem_solved": {
				Type:        genai.TypeString,
				Description: "The problem it solves for the user. One short sentence.",
			},
			"why_working": {
				Type:        genai.TypeString,
				Description: "Why the ad signals suggest traction, based only on the supplied ad data. One or two short sentences.",
			},
		},
		Required:         []string{"what_it_does", "problem_solved", "why_working"},
		PropertyOrdering: []string{"what_it_does", "problem_solved", "why_working"},
	}

	config := &genai.GenerateContentConfig{
		SystemInstruction: &genai.Content{
			Parts: []*genai.Part{
				{Text: explainerSystemInstruction},
			},
		},
		ResponseMIMEType: "application/json",
		ResponseSchema:   schema,
		MaxOutputTokens:  1024,
	}

	content := []*genai.Content{
		{
			Parts: []*genai.Part{
				{Text: buildExplainerPrompt(in)},
			},
		},
	}

	var result *genai.GenerateContentResponse
	var err error
	maxRetries := 3

	for attempt := 0; attempt < maxRetries; attempt++ {
		result, err = h.client.Models.GenerateContent(ctx, h.model, content, config)
		if err == nil {
			break
		}
		waitTime := time.Duration(1<<attempt) * time.Second
		log.Printf("Gemini API error (attempt %d/%d): %v, retrying in %v", attempt+1, maxRetries, err, waitTime)
		time.Sleep(waitTime)
	}

	if err != nil {
		return Explainer{}, fmt.Errorf("gemini API error after %d retries: %w", maxRetries, err)
	}

	if len(result.Candidates) == 0 || result.Candidates[0].Content == nil || len(result.Candidates[0].Content.Parts) == 0 {
		return Explainer{}, fmt.Errorf("no content generated by Gemini")
	}

	generatedText := strings.TrimSpace(result.Candidates[0].Content.Parts[0].Text)
	if generatedText == "" {
		return Explainer{}, fmt.Errorf("empty response from Gemini")
	}

	var out Explainer
	if err := json.Unmarshal([]byte(generatedText), &out); err != nil {
		return Explainer{}, fmt.Errorf("gemini explainer JSON unmarshal: %w (raw=%q)", err, generatedText)
	}
	return out, nil
}

// HuntHistoryTurn is one prior chat turn fed back as history (oldest first).
type HuntHistoryTurn struct {
	Role    string
	Content string
}

// HuntProposalInput is the context for one Peon proposal turn.
type HuntProposalInput struct {
	Message        string            // the user's new request
	History        []HuntHistoryTurn // prior chat turns, oldest first
	TriedTerms     []string          // all terms from prior rounds (never repeat)
	RoundSummaries []string          // e.g. "round 1: 12 terms, 40 found, 5 kept"
}

// HuntProposal is Peon's structured suggestion: a short plain-voice reply plus
// the search terms and competitor apps to seed a round. JSON tags match both the
// genai ResponseSchema property names and the proposal jsonb the API stores.
type HuntProposal struct {
	Reply          string   `json:"reply"`
	Terms          []string `json:"terms"`
	CompetitorApps []string `json:"competitor_apps"`
}

const huntSystemInstruction = "You are Peon, Field Desk's discovery agent. " +
	"You brainstorm Meta Ad Library search terms to find emerging mobile apps. " +
	"Terms must be generic niche queries a user would type (e.g. 'sleep stories app', " +
	"'ai bedtime audio') — short, plain, no brand names. Brand or app names the user " +
	"mentions belong in competitor_apps, never in terms. Never repeat a term listed " +
	"as already tried. Reply text is 1-3 plain sentences on the angles you chose. " +
	"Propose 8-15 terms."

// Deprecated: GenerateHuntProposal is the single-shot proposal path. Task 4
// switches the controller to GenerateHuntTurn (the function-calling agent loop);
// this func + buildHuntPrompt become dead then. Kept compiling until then.
//
// GenerateHuntProposal asks Gemini (as Peon) for search terms + competitor apps
// grounded in the chat history and prior-round outcomes. It mirrors
// GenerateExplainer's structured-JSON + 3-attempt backoff discipline, then
// post-processes terms (lowercase, trim, drop empties/dupes/already-tried, cap
// 15) and caps competitors at 3. Returns an error (callers skip) when the helper
// is nil or every attempt fails.
func (h *GeminiHelper) GenerateHuntProposal(ctx context.Context, in HuntProposalInput) (HuntProposal, error) {
	if h == nil {
		return HuntProposal{}, fmt.Errorf("gemini helper not initialized")
	}

	schema := &genai.Schema{
		Type: genai.TypeObject,
		Properties: map[string]*genai.Schema{
			"reply": {
				Type:        genai.TypeString,
				Description: "1-3 plain sentences on the angles you chose. No brand names.",
			},
			"terms": {
				Type:        genai.TypeArray,
				Description: "8-15 generic niche search queries. Short, plain, no brand names.",
				Items:       &genai.Schema{Type: genai.TypeString},
			},
			"competitor_apps": {
				Type:        genai.TypeArray,
				Description: "Brand or app names the user named as competitors, if any.",
				Items:       &genai.Schema{Type: genai.TypeString},
			},
		},
		Required:         []string{"reply", "terms", "competitor_apps"},
		PropertyOrdering: []string{"reply", "terms", "competitor_apps"},
	}

	config := &genai.GenerateContentConfig{
		SystemInstruction: &genai.Content{
			Parts: []*genai.Part{
				{Text: huntSystemInstruction},
			},
		},
		ResponseMIMEType: "application/json",
		ResponseSchema:   schema,
		MaxOutputTokens:  1024,
	}

	content := []*genai.Content{
		{
			Parts: []*genai.Part{
				{Text: buildHuntPrompt(in)},
			},
		},
	}

	var result *genai.GenerateContentResponse
	var err error
	maxRetries := 3

	for attempt := 0; attempt < maxRetries; attempt++ {
		result, err = h.client.Models.GenerateContent(ctx, h.model, content, config)
		if err == nil {
			break
		}
		waitTime := time.Duration(1<<attempt) * time.Second
		log.Printf("Gemini API error (attempt %d/%d): %v, retrying in %v", attempt+1, maxRetries, err, waitTime)
		time.Sleep(waitTime)
	}

	if err != nil {
		return HuntProposal{}, fmt.Errorf("gemini API error after %d retries: %w", maxRetries, err)
	}

	if len(result.Candidates) == 0 || result.Candidates[0].Content == nil || len(result.Candidates[0].Content.Parts) == 0 {
		return HuntProposal{}, fmt.Errorf("no content generated by Gemini")
	}

	generatedText := strings.TrimSpace(result.Candidates[0].Content.Parts[0].Text)
	if generatedText == "" {
		return HuntProposal{}, fmt.Errorf("empty response from Gemini")
	}

	var out HuntProposal
	if err := json.Unmarshal([]byte(generatedText), &out); err != nil {
		return HuntProposal{}, fmt.Errorf("gemini hunt proposal JSON unmarshal: %w (raw=%q)", err, generatedText)
	}

	out.Terms = cleanHuntTerms(out.Terms, in.TriedTerms)
	out.CompetitorApps = capStrings(out.CompetitorApps, 3)
	return out, nil
}

// cleanHuntTerms lowercases+trims terms, drops empties, dedups (and drops any
// term already tried, matched case-insensitively), and caps the list at 15.
func cleanHuntTerms(terms, tried []string) []string {
	seen := make(map[string]struct{}, len(tried))
	for _, t := range tried {
		seen[strings.ToLower(strings.TrimSpace(t))] = struct{}{}
	}
	out := make([]string, 0, len(terms))
	for _, t := range terms {
		norm := strings.ToLower(strings.TrimSpace(t))
		if norm == "" {
			continue
		}
		if _, dup := seen[norm]; dup {
			continue
		}
		seen[norm] = struct{}{}
		out = append(out, norm)
		if len(out) >= 15 {
			break
		}
	}
	return out
}

// capStrings trims and drops empties, then caps the slice length.
func capStrings(in []string, max int) []string {
	out := make([]string, 0, len(in))
	for _, s := range in {
		s = strings.TrimSpace(s)
		if s == "" {
			continue
		}
		out = append(out, s)
		if len(out) >= max {
			break
		}
	}
	return out
}

// buildHuntPrompt renders the chat history, already-tried terms, and prior-round
// summaries, then the new request, as a compact labelled prompt.
func buildHuntPrompt(in HuntProposalInput) string {
	var b strings.Builder
	for _, turn := range in.History {
		content := strings.TrimSpace(turn.Content)
		if content == "" {
			continue
		}
		who := "User"
		if strings.EqualFold(turn.Role, "assistant") {
			who = "Peon"
		}
		fmt.Fprintf(&b, "%s: %s\n", who, content)
	}
	if len(in.TriedTerms) > 0 {
		fmt.Fprintf(&b, "\nAlready tried terms: %s\n", strings.Join(in.TriedTerms, ", "))
	}
	if len(in.RoundSummaries) > 0 {
		b.WriteString("\nPrior rounds:\n")
		for _, s := range in.RoundSummaries {
			if s = strings.TrimSpace(s); s != "" {
				fmt.Fprintf(&b, "- %s\n", s)
			}
		}
	}
	fmt.Fprintf(&b, "\nNew request: %s\n", strings.TrimSpace(in.Message))
	return b.String()
}

// ---------------------------------------------------------------------------
// Peon agent loop (function-calling): GenerateHuntTurn + supporting types.
// ---------------------------------------------------------------------------

// HuntSettings carries optional scrape-tuning knobs the founder can set through
// chat. Nil pointers mean "not set" (the key is omitted from the clamped map);
// present pointers are clamped to their server-side bounds by ClampHuntSettings.
type HuntSettings struct {
	PageCap      *int // Ad Library pages per term (1-100)
	Patience     *int // empty pages tolerated before stopping a term (1-10)
	ListingPages *int // store listing pages fetched per app (1-30)
	MinPages     *int // pages to read before the dry exit may fire (1-30)
}

// ClampHuntSettings bounds each present knob and returns a map keyed by the
// settings names the round row + pipeline job expect. Only keys present in the
// input appear; the result is an empty (non-nil) map when nothing is set.
func ClampHuntSettings(s HuntSettings) map[string]int {
	out := map[string]int{}
	if s.PageCap != nil {
		out["page_cap"] = clampInt(*s.PageCap, 1, 100)
	}
	if s.Patience != nil {
		out["patience"] = clampInt(*s.Patience, 1, 10)
	}
	if s.ListingPages != nil {
		out["listing_pages"] = clampInt(*s.ListingPages, 1, 30)
	}
	if s.MinPages != nil {
		out["min_pages"] = clampInt(*s.MinPages, 1, 30)
	}
	return out
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// HuntToolExecutor is the read side the agent loop calls into for round
// telemetry. Task 4 implements it against the repo layer, per-request and scoped
// to the current hunt (foreign round ids come back as an error string the model
// sees). This package only defines the interface.
type HuntToolExecutor interface {
	GetRoundReport(roundID int) (string, error)
	GetRoundResults(roundID int, triage string) (string, error)
}

// HuntPlan is the terms + competitor apps + clamped settings for a round, plus
// the plain-sentence intent the founder is hunting for.
type HuntPlan struct {
	Terms          []string
	CompetitorApps []string
	Settings       map[string]int
	Intent         string
}

// HuntTurn is the result of one Peon agent turn. Proposal is non-nil when the
// model called propose_round (the only terminal tool); nil for a plain chat
// reply. ToolLog is the plain-sentence trace the loop wrote.
type HuntTurn struct {
	Reply    string
	Proposal *HuntPlan
	ToolLog  []string
}

// HuntTurnInput is the context for one agent turn (a superset of
// HuntProposalInput).
type HuntTurnInput struct {
	Message        string
	History        []HuntHistoryTurn
	TriedTerms     []string
	RoundSummaries []string

	// PendingProposal is the latest un-started proposal, formatted for the
	// model; empty when none is pending. CardState is what the founder's
	// proposal card currently shows (they may have edited chips). PriorHunts
	// are compact one-line summaries from the founder's other hunts.
	PendingProposal string
	CardState       string
	PriorHunts      []string
}

const huntAgentInstruction = "You are Peon, Field Desk's discovery agent. You help the founder find " +
	"emerging mobile apps by running Meta Ad Library search rounds and researching niches on the web.\n" +
	"\n" +
	"Search terms are generic niche queries a user would type (e.g. 'sleep stories app', " +
	"'ai bedtime audio') — short, plain, and never brand or app names. Any brand or app the " +
	"founder names belongs in competitor_apps, never in terms. At most 15 terms and at most " +
	"3 competitor apps.\n" +
	"\n" +
	"You cannot start a round. propose_round suggests a plan the founder approves with the " +
	"Start hunt button — that button is the only way a round starts. When the founder asks to " +
	"run or re-run something, reply with a propose_round call (set allow_tried true for " +
	"re-runs so previously tried terms are kept) and note they can press Start hunt. Never " +
	"say a round is running unless round telemetry says so.\n" +
	"\n" +
	"When a proposal is awaiting approval, a new propose_round is a refinement: keep " +
	"everything the founder did not ask to change, starting from the proposal card state " +
	"when it is provided.\n" +
	"\n" +
	"Every propose_round includes intent: one plain sentence saying what the founder is " +
	"hunting, from the conversation (e.g. 'sleep apps aimed at new parents'). Results are " +
	"judged against it after the round runs.\n" +
	"\n" +
	"Use web search and page reading when they help: niche research, a link the founder " +
	"pastes, a store listing. Before answering about a prior round, call get_round_report " +
	"and/or get_round_results — do not guess round outcomes.\n" +
	"\n" +
	"Optional round settings and their bounds: page_cap (Ad Library pages per term, 1-100), " +
	"patience (empty pages tolerated before stopping a term, 1-10), min_pages (pages to read " +
	"before the dry exit may fire, 1-30), listing_pages (store listing pages per app, 1-30). " +
	"Set only the ones the founder asked for; when asked what a setting means, just explain it.\n" +
	"\n" +
	"Your reply text is 1-3 plain, professional sentences. The name Peon appears only in " +
	"prose, never inside terms or competitor apps.\n" +
	"\n" +
	"Prefer a few broad head terms ('running app', 'sleep tracker') over many hyper-" +
	"specific long-tail variants: measured across prior hunts, long-tail terms almost " +
	"always find nothing on the Ad Library while broad terms carry the round.\n" +
	"\n" +
	"Some turns ask you to report a just-finished round. Read its telemetry first " +
	"(get_round_report, get_round_results), name the promising kept apps, say which " +
	"terms worked and which came up empty, then propose the next round. For those " +
	"reports 2-5 sentences are fine; never reply with empty text."

// huntToolDecls is the fixed tool surface of the hunt agent. There is
// deliberately no tool that starts a round — rounds start only from the
// founder's Start hunt button (POST /hunts/:id/rounds).
func huntToolDecls() []*genai.FunctionDeclaration {
	return []*genai.FunctionDeclaration{
		{
			Name:        "get_round_report",
			Description: "Telemetry for one prior round: settings used, per-term pages/found/new and why each term stopped, triage counts.",
			Parameters: &genai.Schema{
				Type:       genai.TypeObject,
				Properties: map[string]*genai.Schema{"round_id": {Type: genai.TypeInteger, Description: "The round number to inspect."}},
				Required:   []string{"round_id"},
			},
		},
		{
			Name:        "get_round_results",
			Description: "Result rows for one prior round (page name, store, source term, was_new, triage, relevance, gem score); optional triage filter kept|vetoed|pending.",
			Parameters: &genai.Schema{
				Type: genai.TypeObject,
				Properties: map[string]*genai.Schema{
					"round_id": {Type: genai.TypeInteger, Description: "The round number to inspect."},
					"triage":   {Type: genai.TypeString, Description: "Optional triage filter: kept, vetoed, or pending. Omit for all rows."},
				},
				Required: []string{"round_id"},
			},
		},
		{
			Name:        "propose_round",
			Description: "Suggest a round for the founder to approve with the Start hunt button. The only terminal action; you cannot start rounds yourself.",
			Parameters:  planSchema(),
		},
	}
}

// serverToolLines maps server-side built-in tool invocations (Gemini runs
// google_search/url_context itself) to the plain tool_log sentences the chat
// shows under Peon's reply.
func serverToolLines(parts []*genai.Part) []string {
	var out []string
	for _, p := range parts {
		if p == nil || p.ToolCall == nil {
			continue
		}
		switch p.ToolCall.ToolType {
		case genai.ToolTypeGoogleSearchWeb, genai.ToolTypeGoogleSearchImage:
			out = append(out, "searched the web")
		case genai.ToolTypeURLContext:
			out = append(out, "read a page")
		}
	}
	return out
}

// GenerateHuntTurn runs Peon as a Gemini function-calling agent: it can read
// round telemetry via the tool executor and research niches with built-in web
// tools, then finish with a plain reply or a terminal propose_round. It mirrors
// GenerateExplainer's 3-attempt backoff (per model call) and caps the loop at 6
// iterations. Returns an error (callers respond 502) when the helper is nil or a
// model call fails after retries.
func (h *GeminiHelper) GenerateHuntTurn(ctx context.Context, in HuntTurnInput, tools HuntToolExecutor) (HuntTurn, error) {
	if h == nil {
		return HuntTurn{}, fmt.Errorf("gemini helper not initialized")
	}
	// A nil executor would panic on the first reader-tool dispatch (nil-interface
	// call). Fail soft the same way the nil-helper guard does.
	if tools == nil {
		return HuntTurn{}, fmt.Errorf("hunt tools are not configured")
	}

	config := &genai.GenerateContentConfig{
		SystemInstruction: &genai.Content{Parts: []*genai.Part{{Text: huntAgentInstruction}}},
		Tools:             []*genai.Tool{{FunctionDeclarations: huntToolDecls()}},
		MaxOutputTokens:   2048,
	}
	if h.huntWebTools {
		// Built-in tools run server-side inside one model call; combining them
		// with function declarations needs this flag (verified live: rejected
		// without it, and rejected entirely on gemini-2.5-flash).
		config.Tools = append(config.Tools,
			&genai.Tool{GoogleSearch: &genai.GoogleSearch{}},
			&genai.Tool{URLContext: &genai.URLContext{}},
		)
		config.ToolConfig = &genai.ToolConfig{IncludeServerSideToolInvocations: genai.Ptr(true)}
	}

	contents := buildHuntTurnContents(in)
	var toolLog []string

	const maxIterations = 6
	for iter := 0; iter < maxIterations; iter++ {
		resp, err := h.generateWithRetry(ctx, h.huntModel, contents, config)
		if err != nil {
			return HuntTurn{}, err
		}

		if len(resp.Candidates) > 0 && resp.Candidates[0].Content != nil {
			toolLog = append(toolLog, serverToolLines(resp.Candidates[0].Content.Parts)...)
		}

		calls := resp.FunctionCalls()
		if len(calls) == 0 {
			return HuntTurn{Reply: firstText(resp), ToolLog: toolLog}, nil
		}

		// Append the model's own content (the function-call turn) before the
		// tool responses, so the next call sees a well-formed transcript.
		if len(resp.Candidates) > 0 && resp.Candidates[0].Content != nil {
			contents = append(contents, resp.Candidates[0].Content)
		}

		var responseParts []*genai.Part
		for _, call := range calls {
			switch call.Name {
			case "propose_round":
				// propose_round keeps the tried-term strip (avoid re-proposing
				// terms already run) unless the model set allow_tried.
				plan := planFromArgs(call.Args, in.TriedTerms)
				turn := HuntTurn{Reply: firstText(resp), ToolLog: toolLog}
				turn.ToolLog = append(turn.ToolLog, describePlan("proposed a round", plan))
				turn.Proposal = &plan
				return turn, nil // terminal tools end the turn
			case "get_round_report":
				id := intArg(call.Args, "round_id")
				out, rerr := tools.GetRoundReport(id)
				if rerr != nil {
					out = "error: " + rerr.Error()
				}
				toolLog = append(toolLog, fmt.Sprintf("checked round %d's report", id))
				responseParts = append(responseParts, genai.NewPartFromFunctionResponse(call.Name, map[string]any{"report": out}))
			case "get_round_results":
				id := intArg(call.Args, "round_id")
				triage := stringArg(call.Args, "triage")
				out, rerr := tools.GetRoundResults(id, triage)
				if rerr != nil {
					out = "error: " + rerr.Error()
				}
				toolLog = append(toolLog, fmt.Sprintf("looked at round %d's results", id))
				responseParts = append(responseParts, genai.NewPartFromFunctionResponse(call.Name, map[string]any{"results": out}))
			default:
				responseParts = append(responseParts, genai.NewPartFromFunctionResponse(call.Name, map[string]any{"error": "unknown tool"}))
			}
		}
		contents = append(contents, genai.NewContentFromParts(responseParts, genai.RoleUser))
	}

	return HuntTurn{Reply: "Peon could not finish that request. Try rephrasing.", ToolLog: toolLog}, nil
}

// generateWithRetry runs one model call with the same 3-attempt exponential
// backoff as GenerateExplainer.
func (h *GeminiHelper) generateWithRetry(ctx context.Context, model string, contents []*genai.Content, config *genai.GenerateContentConfig) (*genai.GenerateContentResponse, error) {
	var result *genai.GenerateContentResponse
	var err error
	maxRetries := 3
	for attempt := 0; attempt < maxRetries; attempt++ {
		result, err = h.client.Models.GenerateContent(ctx, model, contents, config)
		if err == nil {
			return result, nil
		}
		waitTime := time.Duration(1<<attempt) * time.Second
		log.Printf("Gemini API error (attempt %d/%d): %v, retrying in %v", attempt+1, maxRetries, err, waitTime)
		time.Sleep(waitTime)
	}
	return nil, fmt.Errorf("gemini API error after %d retries: %w", maxRetries, err)
}

// settingsSchema declares the three optional integer knobs with their bounds in
// the descriptions (the server still clamps).
func settingsSchema() *genai.Schema {
	return &genai.Schema{
		Type: genai.TypeObject,
		Properties: map[string]*genai.Schema{
			"page_cap":      {Type: genai.TypeInteger, Description: "Max Ad Library pages to scrape per term (1-100)."},
			"patience":      {Type: genai.TypeInteger, Description: "Empty pages tolerated before stopping a term (1-10)."},
			"min_pages":     {Type: genai.TypeInteger, Description: "Pages to read per term before the dry early-exit may fire (1-30). Early pages are often full of repeat incumbents; a higher floor reaches new apps."},
			"listing_pages": {Type: genai.TypeInteger, Description: "Store listing pages to fetch per app (1-30)."},
		},
	}
}

// planSchema is the parameter schema for propose_round.
func planSchema() *genai.Schema {
	return &genai.Schema{
		Type: genai.TypeObject,
		Properties: map[string]*genai.Schema{
			"terms":           {Type: genai.TypeArray, Description: "Generic niche search queries. Short, plain, no brand names. At most 15.", Items: &genai.Schema{Type: genai.TypeString}},
			"competitor_apps": {Type: genai.TypeArray, Description: "Brand or app names the founder named as competitors. At most 3.", Items: &genai.Schema{Type: genai.TypeString}},
			"settings":        settingsSchema(),
			"intent":          {Type: genai.TypeString, Description: "One plain sentence: what the founder is hunting."},
			"allow_tried":     {Type: genai.TypeBoolean, Description: "True only when the founder explicitly asked to re-run previous terms."},
		},
		Required: []string{"terms", "intent"},
	}
}

// planFromArgs builds a cleaned HuntPlan from a tool-call args map: terms are
// normalized + de-duped + stripped of already-tried (cleanHuntTerms),
// competitors are capped at 3, and settings are clamped. JSON numbers arrive as
// float64, which intPtrArg narrows to int.
func planFromArgs(args map[string]any, tried []string) HuntPlan {
	if b, ok := args["allow_tried"].(bool); ok && b {
		// Explicit founder re-run: re-emitted prior terms are allowed.
		tried = nil
	}
	plan := HuntPlan{
		Terms:          cleanHuntTerms(stringSlice(args["terms"]), tried),
		CompetitorApps: capStrings(stringSlice(args["competitor_apps"]), 3),
		Settings:       map[string]int{},
		Intent:         stringArg(args, "intent"),
	}
	if raw, ok := args["settings"].(map[string]any); ok {
		plan.Settings = ClampHuntSettings(HuntSettings{
			PageCap:      intPtrArg(raw, "page_cap"),
			Patience:     intPtrArg(raw, "patience"),
			ListingPages: intPtrArg(raw, "listing_pages"),
			MinPages:     intPtrArg(raw, "min_pages"),
		})
	}
	return plan
}

// describePlan renders a tool_log line like
// "started a round — 3 terms · depth 30 · patience 6". Settings segments that
// aren't set are omitted.
func describePlan(verb string, plan HuntPlan) string {
	segs := []string{fmt.Sprintf("%d terms", len(plan.Terms))}
	if d, ok := plan.Settings["page_cap"]; ok {
		segs = append(segs, fmt.Sprintf("depth %d", d))
	}
	if p, ok := plan.Settings["patience"]; ok {
		segs = append(segs, fmt.Sprintf("patience %d", p))
	}
	if m, ok := plan.Settings["min_pages"]; ok {
		segs = append(segs, fmt.Sprintf("min %d", m))
	}
	if l, ok := plan.Settings["listing_pages"]; ok {
		segs = append(segs, fmt.Sprintf("listing %d", l))
	}
	return verb + " — " + strings.Join(segs, " · ")
}

// buildHuntTurnContents renders the chat history as alternating user/model
// genai contents, then a final user turn that carries the tried-terms +
// prior-round context block above the founder's new message.
func buildHuntTurnContents(in HuntTurnInput) []*genai.Content {
	var contents []*genai.Content
	for _, turn := range in.History {
		text := strings.TrimSpace(turn.Content)
		if text == "" {
			continue
		}
		var role genai.Role = genai.RoleUser
		if strings.EqualFold(turn.Role, "assistant") || strings.EqualFold(turn.Role, "model") {
			role = genai.RoleModel
		}
		contents = append(contents, genai.NewContentFromText(text, role))
	}

	var b strings.Builder
	if len(in.TriedTerms) > 0 {
		fmt.Fprintf(&b, "Already tried terms: %s\n", strings.Join(in.TriedTerms, ", "))
	}
	if len(in.RoundSummaries) > 0 {
		b.WriteString("Prior rounds:\n")
		for _, s := range in.RoundSummaries {
			if s = strings.TrimSpace(s); s != "" {
				fmt.Fprintf(&b, "- %s\n", s)
			}
		}
	}
	if len(in.PriorHunts) > 0 {
		b.WriteString("Prior hunts (other threads):\n")
		for _, line := range in.PriorHunts {
			if line = strings.TrimSpace(line); line != "" {
				fmt.Fprintf(&b, "  %s\n", line)
			}
		}
	}
	if in.PendingProposal != "" {
		fmt.Fprintf(&b, "Current proposal awaiting approval: %s\n", in.PendingProposal)
	}
	if in.CardState != "" {
		fmt.Fprintf(&b, "The proposal card currently shows: %s\n", in.CardState)
	}
	if b.Len() > 0 {
		b.WriteString("\n")
	}
	b.WriteString(strings.TrimSpace(in.Message))

	contents = append(contents, genai.NewContentFromText(b.String(), genai.RoleUser))
	return contents
}

// firstText joins the text parts of the top candidate, trimmed.
func firstText(resp *genai.GenerateContentResponse) string {
	if resp == nil || len(resp.Candidates) == 0 || resp.Candidates[0].Content == nil {
		return ""
	}
	var b strings.Builder
	for _, p := range resp.Candidates[0].Content.Parts {
		if p == nil {
			continue
		}
		if t := strings.TrimSpace(p.Text); t != "" {
			if b.Len() > 0 {
				b.WriteString(" ")
			}
			b.WriteString(t)
		}
	}
	return strings.TrimSpace(b.String())
}

// stringSlice narrows a JSON array arg ([]any of strings) to []string.
func stringSlice(v any) []string {
	arr, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(arr))
	for _, e := range arr {
		if s, ok := e.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// intArg reads an integer arg, tolerating the float64 JSON numbers carry.
func intArg(args map[string]any, key string) int {
	switch n := args[key].(type) {
	case float64:
		return int(n)
	case int:
		return n
	case int64:
		return int(n)
	}
	return 0
}

// intPtrArg returns a *int for a present numeric key, else nil (key absent).
func intPtrArg(m map[string]any, key string) *int {
	v, ok := m[key]
	if !ok {
		return nil
	}
	switch n := v.(type) {
	case float64:
		i := int(n)
		return &i
	case int:
		return &n
	case int64:
		i := int(n)
		return &i
	}
	return nil
}

// stringArg reads a string arg, empty when absent or wrong type.
func stringArg(args map[string]any, key string) string {
	if s, ok := args[key].(string); ok {
		return strings.TrimSpace(s)
	}
	return ""
}

// buildExplainerPrompt renders the grounding material as a compact, labelled
// prompt. It caps the creative list at 10 and skips empty fields so the model
// is never tempted to fill a blank label with invention.
func buildExplainerPrompt(in ExplainerInput) string {
	var b strings.Builder
	b.WriteString("Here is an app and the real ad copy it runs. Describe it factually, using only this material.\n\n")

	if s := strings.TrimSpace(in.AppName); s != "" {
		fmt.Fprintf(&b, "app name: %s\n", s)
	}
	if s := strings.TrimSpace(in.Genre); s != "" {
		fmt.Fprintf(&b, "genre: %s\n", s)
	}
	if s := strings.TrimSpace(in.AppDescription); s != "" {
		fmt.Fprintf(&b, "app store description: %s\n", s)
	}
	if s := strings.TrimSpace(in.CTA); s != "" {
		fmt.Fprintf(&b, "call to action: %s\n", s)
	}
	if s := strings.TrimSpace(in.LandingURL); s != "" {
		fmt.Fprintf(&b, "landing page: %s\n", s)
	}

	const maxCreatives = 10
	creatives := in.Creatives
	if len(creatives) > maxCreatives {
		creatives = creatives[:maxCreatives]
	}
	wrote := false
	for _, c := range creatives {
		title := strings.TrimSpace(c.Title)
		body := strings.TrimSpace(c.BodyText)
		if title == "" && body == "" {
			continue
		}
		if !wrote {
			b.WriteString("\nad copy:\n")
			wrote = true
		}
		switch {
		case title != "" && body != "":
			fmt.Fprintf(&b, "- %s — %s\n", title, body)
		case title != "":
			fmt.Fprintf(&b, "- %s\n", title)
		default:
			fmt.Fprintf(&b, "- %s\n", body)
		}
	}

	return b.String()
}
