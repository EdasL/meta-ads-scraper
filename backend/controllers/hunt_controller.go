package controllers

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// huntTools is the per-request read side the Peon agent loop calls into for round
// telemetry. It is scoped to a single hunt: a round id that belongs to another
// hunt comes back as an error string the model sees ("no such round in this
// hunt"), never another user's data.
type huntTools struct {
	ctx    context.Context
	g      *gorm.DB
	huntID int
}

// GetRoundReport returns the compact-JSON status digest for one round, or an
// error when the round does not belong to this hunt.
func (t huntTools) GetRoundReport(roundID int) (string, error) {
	rep, err := repositories.RoundReport(t.ctx, t.g, t.huntID, roundID)
	if err != nil {
		return "", err
	}
	if rep == nil {
		return "", errors.New("no such round in this hunt")
	}
	b, err := json.Marshal(rep)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// GetRoundResults returns up to 50 result rows for one round as a compact-JSON
// array string, optionally filtered by triage (kept|vetoed|pending; "" for all).
func (t huntTools) GetRoundResults(roundID int, triage string) (string, error) {
	rows, err := repositories.RoundResults(t.ctx, t.g, t.huntID, roundID, triage, 50)
	if err != nil {
		return "", err
	}
	if rows == nil {
		rows = []models.HuntResultRow{}
	}
	b, err := json.Marshal(rows)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// huntProposalTimeout bounds the Peon (Gemini) turn. It is deliberately longer
// than reqCtx's 15s DB budget: a proposal round-trips the model with 3-attempt
// backoff, so 30s covers a slow-but-succeeding call without hanging the client.
const huntProposalTimeout = 30 * time.Second

// maxHuntTerms / maxHuntCompetitors / maxHuntTermLen bound a round the founder
// approves before it hits the scraper. maxHuntTitleLen truncates the first user
// message into a thread title.
const (
	maxHuntTerms       = 15
	maxHuntCompetitors = 3
	maxHuntTermLen     = 80
	maxHuntTitleLen    = 80
)

// HuntController serves the on-demand discovery chat. It holds the Gemini helper
// (nil when GEMINI_API_KEY is unset — the read + round endpoints still work; the
// two chat turns return 503). All identity comes from KeyAuth via UserFrom, and
// every :hunt_id route 404s on an ownership miss so a caller cannot probe for
// other users' hunts.
type HuntController struct {
	gemini *helpers.GeminiHelper
}

func NewHuntController(gemini *helpers.GeminiHelper) *HuntController {
	return &HuntController{gemini: gemini}
}

// requireOwner resolves the signed-in caller and the hunt's owner, returning the
// hunt id and email when the caller owns it. It writes the response (401/404/500)
// and returns ok=false otherwise: a foreign or missing hunt is a 404 (never 403)
// so existence is not leaked.
func (c *HuntController) requireOwner(ctx *gin.Context) (huntID int, email string, ok bool) {
	u, authed := middlewares.UserFrom(ctx.Request.Context())
	if !authed {
		helpers.Detail(ctx, http.StatusUnauthorized, "not signed in.")
		return 0, "", false
	}
	huntID, err := strconv.Atoi(ctx.Param("hunt_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusNotFound, "no such hunt.")
		return 0, "", false
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	owner, err := repositories.HuntOwner(rctx, db.DB(), huntID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return 0, "", false
	}
	if owner == "" || !strings.EqualFold(owner, u.Email) {
		helpers.Detail(ctx, http.StatusNotFound, "no such hunt.")
		return 0, "", false
	}
	return huntID, u.Email, true
}

// List handles GET /api/hunts.
func (c *HuntController) List(ctx *gin.Context) {
	u, ok := middlewares.UserFrom(ctx.Request.Context())
	if !ok {
		helpers.Detail(ctx, http.StatusUnauthorized, "not signed in.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	rows, err := repositories.ListHunts(rctx, db.DB(), u.Email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if rows == nil {
		rows = []models.HuntRow{}
	}
	ctx.JSON(http.StatusOK, rows)
}

type huntMessageBody struct {
	Message string `json:"message"`
}

// Create handles POST /api/hunts. It opens a new thread, persists the user
// message, and appends Peon's proposal. On a Gemini failure the user message is
// KEPT (502) so the founder can retry against a consistent thread.
func (c *HuntController) Create(ctx *gin.Context) {
	u, ok := middlewares.UserFrom(ctx.Request.Context())
	if !ok {
		helpers.Detail(ctx, http.StatusUnauthorized, "not signed in.")
		return
	}
	var body huntMessageBody
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "message is required.")
		return
	}
	message := strings.TrimSpace(body.Message)
	if message == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "message is required.")
		return
	}
	if c.gemini == nil {
		helpers.Detail(ctx, http.StatusServiceUnavailable, "Peon is not configured.")
		return
	}

	rctx, cancel := reqCtx(ctx)
	defer cancel()
	huntID, err := repositories.CreateHunt(rctx, db.DB(), u.Email, huntTitle(message))
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not start hunt.")
		return
	}
	if _, err := repositories.InsertHuntMessage(rctx, db.DB(), huntID, "user", message, nil, nil); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save message.")
		return
	}

	// New thread → no history, no prior rounds.
	if !c.runTurn(ctx, huntID, helpers.HuntTurnInput{Message: message}) {
		return
	}
	c.writeThread(ctx, huntID, http.StatusCreated, nil)
}

// Message handles POST /api/hunts/:hunt_id/messages — the same flow as Create on
// an existing, owned thread.
func (c *HuntController) Message(ctx *gin.Context) {
	huntID, _, ok := c.requireOwner(ctx)
	if !ok {
		return
	}
	var body huntMessageBody
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "message is required.")
		return
	}
	message := strings.TrimSpace(body.Message)
	if message == "" {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "message is required.")
		return
	}
	if c.gemini == nil {
		helpers.Detail(ctx, http.StatusServiceUnavailable, "Peon is not configured.")
		return
	}

	rctx, cancel := reqCtx(ctx)
	// History = the transcript BEFORE this new turn.
	prior, err := repositories.ListHuntMessages(rctx, db.DB(), huntID)
	if err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	summaries, err := repositories.HuntRoundSummaries(rctx, db.DB(), huntID)
	if err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if _, err := repositories.InsertHuntMessage(rctx, db.DB(), huntID, "user", message, nil, nil); err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save message.")
		return
	}
	cancel()

	history := make([]helpers.HuntHistoryTurn, 0, len(prior))
	for _, m := range prior {
		history = append(history, helpers.HuntHistoryTurn{Role: m.Role, Content: m.Content})
	}
	tried, roundLines := summarizeRounds(summaries)
	if !c.runTurn(ctx, huntID, helpers.HuntTurnInput{
		Message:        message,
		History:        history,
		TriedTerms:     tried,
		RoundSummaries: roundLines,
	}) {
		return
	}
	c.writeThread(ctx, huntID, http.StatusCreated, nil)
}

// runTurn runs one Peon agent turn under a 30s budget and appends the assistant
// message. It writes the error response (502) and returns ok=false on a Gemini
// failure — leaving the already-persisted user message intact. The assistant
// message carries the reply as content, a {terms, competitor_apps, settings}
// proposal jsonb when the model proposed a round, and a tool_log jsonb when the
// loop read any round telemetry. Rounds start only from the founder's Start hunt
// button, never from a chat turn.
func (c *HuntController) runTurn(ctx *gin.Context, huntID int, input helpers.HuntTurnInput) bool {
	gctx, cancel := context.WithTimeout(ctx.Request.Context(), huntProposalTimeout)
	defer cancel()

	tools := huntTools{ctx: gctx, g: db.DB(), huntID: huntID}
	turn, err := c.gemini.GenerateHuntTurn(gctx, input, tools)
	if err != nil {
		helpers.Detail(ctx, http.StatusBadGateway, "Peon could not answer. Try again.")
		return false
	}

	var proposalJSON json.RawMessage
	if turn.Proposal != nil {
		proposalJSON, err = marshalHuntPlan(*turn.Proposal)
		if err != nil {
			helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
			return false
		}
	}
	var toolLogJSON json.RawMessage
	if len(turn.ToolLog) > 0 {
		toolLogJSON, err = json.Marshal(turn.ToolLog)
		if err != nil {
			helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
			return false
		}
	}

	reply := turn.Reply

	rctx, cancel2 := reqCtx(ctx)
	defer cancel2()

	// Commit the assistant message. Rounds start only from the founder's Start
	// hunt button (POST /hunts/:id/rounds), never from a chat turn. Gemini
	// FAILURES are handled above and leave the user message intact.
	err = db.WithWriteTx(rctx, db.DB(), func(tx *gorm.DB) error {
		_, e := repositories.InsertHuntMessageTx(tx, huntID, "assistant", reply, proposalJSON, toolLogJSON)
		return e
	})
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save reply.")
		return false
	}
	return true
}

// writeThread returns {hunt_id, messages} after a chat turn, plus an optional
// "round" when the turn started one. The GET /hunts/:id shape is untouched.
func (c *HuntController) writeThread(ctx *gin.Context, huntID, status int, round *models.HuntRoundRow) {
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	msgs, err := repositories.ListHuntMessages(rctx, db.DB(), huntID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if msgs == nil {
		msgs = []models.HuntMessageRow{}
	}
	resp := gin.H{"hunt_id": huntID, "messages": msgs}
	if round != nil {
		resp["round"] = round
	}
	ctx.JSON(status, resp)
}

// marshalHuntPlan renders a proposed plan as the {terms, competitor_apps,
// settings} proposal jsonb the assistant message stores. The settings key is
// always present (an empty object when nothing was set).
func marshalHuntPlan(p helpers.HuntPlan) (json.RawMessage, error) {
	return json.Marshal(struct {
		Terms          []string       `json:"terms"`
		CompetitorApps []string       `json:"competitor_apps"`
		Settings       map[string]int `json:"settings"`
	}{
		Terms:          emptyIfNil(p.Terms),
		CompetitorApps: emptyIfNil(p.CompetitorApps),
		Settings:       clampedOrEmpty(p.Settings),
	})
}

// clampedOrEmpty returns a non-nil settings map so it marshals to "{}" rather
// than "null".
func clampedOrEmpty(s map[string]int) map[string]int {
	if s == nil {
		return map[string]int{}
	}
	return s
}

// Get handles GET /api/hunts/:hunt_id → {hunt, messages, rounds, results}.
func (c *HuntController) Get(ctx *gin.Context) {
	huntID, email, ok := c.requireOwner(ctx)
	if !ok {
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()

	// The hunt row itself: reuse the owner's list (indexed on user_email) and
	// pick out this id — no dedicated single-row repo func needed.
	hunts, err := repositories.ListHunts(rctx, db.DB(), email)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	var hunt *models.HuntRow
	for i := range hunts {
		if hunts[i].ID == huntID {
			hunt = &hunts[i]
			break
		}
	}
	if hunt == nil {
		// Raced with a delete between ownership check and read.
		helpers.Detail(ctx, http.StatusNotFound, "no such hunt.")
		return
	}

	msgs, err := repositories.ListHuntMessages(rctx, db.DB(), huntID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	rounds, err := repositories.ListHuntRounds(rctx, db.DB(), huntID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	results, err := repositories.ListHuntResults(rctx, db.DB(), huntID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if msgs == nil {
		msgs = []models.HuntMessageRow{}
	}
	if rounds == nil {
		rounds = []models.HuntRoundRow{}
	}
	if results == nil {
		results = []models.HuntResultRow{}
	}
	ctx.JSON(http.StatusOK, gin.H{
		"hunt":     hunt,
		"messages": msgs,
		"rounds":   rounds,
		"results":  results,
	})
}

type huntSettingsBody struct {
	PageCap      *int `json:"page_cap"`
	Patience     *int `json:"patience"`
	ListingPages *int `json:"listing_pages"`
}

type startRoundBody struct {
	MessageID      *int              `json:"message_id"`
	Terms          []string          `json:"terms"`
	CompetitorApps []string          `json:"competitor_apps"`
	Settings       *huntSettingsBody `json:"settings"`
}

// StartRound handles POST /api/hunts/:hunt_id/rounds. It normalizes and validates
// the founder-approved terms, then enqueues the round (InsertHuntRound writes the
// round and a pipeline_jobs('hunt', roundID) in one txn).
func (c *HuntController) StartRound(ctx *gin.Context) {
	huntID, _, ok := c.requireOwner(ctx)
	if !ok {
		return
	}
	var body startRoundBody
	if err := ctx.ShouldBindJSON(&body); err != nil {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, "invalid request body.")
		return
	}

	terms := normalizeHuntTerms(body.Terms)
	if len(terms) < 1 || len(terms) > maxHuntTerms {
		helpers.Detail(ctx, http.StatusUnprocessableEntity,
			fmt.Sprintf("provide between 1 and %d terms.", maxHuntTerms))
		return
	}
	competitors := trimNonEmpty(body.CompetitorApps)
	if len(competitors) > maxHuntCompetitors {
		helpers.Detail(ctx, http.StatusUnprocessableEntity,
			fmt.Sprintf("at most %d competitor apps.", maxHuntCompetitors))
		return
	}

	termsJSON, _ := json.Marshal(terms)
	compsJSON, _ := json.Marshal(competitors)

	// Optional founder-set knobs, clamped to their server-side bounds. Absent
	// settings marshal to a nil json.RawMessage → the repo defaults '{}'.
	var settingsJSON json.RawMessage
	if body.Settings != nil {
		clamped := helpers.ClampHuntSettings(helpers.HuntSettings{
			PageCap:      body.Settings.PageCap,
			Patience:     body.Settings.Patience,
			ListingPages: body.Settings.ListingPages,
		})
		settingsJSON, _ = json.Marshal(clamped)
	}

	rctx, cancel := reqCtx(ctx)
	defer cancel()
	roundID, err := repositories.InsertHuntRound(rctx, db.DB(), huntID, body.MessageID, termsJSON, compsJSON, settingsJSON, nil)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not start round.")
		return
	}

	rounds, err := repositories.ListHuntRounds(rctx, db.DB(), huntID)
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	for i := range rounds {
		if rounds[i].ID == roundID {
			ctx.JSON(http.StatusAccepted, rounds[i])
			return
		}
	}
	// Should never happen (we just inserted it), but fail loud rather than 200.
	helpers.Detail(ctx, http.StatusInternalServerError, "round not found after insert.")
}

// huntTitle derives a thread title from the first user message: trimmed and
// truncated to maxHuntTitleLen runes.
func huntTitle(message string) string {
	title := strings.TrimSpace(message)
	if r := []rune(title); len(r) > maxHuntTitleLen {
		title = strings.TrimSpace(string(r[:maxHuntTitleLen]))
	}
	if title == "" {
		title = "Untitled hunt"
	}
	return title
}

// normalizeHuntTerms trims + lowercases, drops empties and terms over the length
// cap, and dedupes (preserving first-seen order).
func normalizeHuntTerms(in []string) []string {
	seen := make(map[string]struct{}, len(in))
	out := make([]string, 0, len(in))
	for _, t := range in {
		norm := strings.ToLower(strings.TrimSpace(t))
		if norm == "" || len([]rune(norm)) > maxHuntTermLen {
			continue
		}
		if _, dup := seen[norm]; dup {
			continue
		}
		seen[norm] = struct{}{}
		out = append(out, norm)
	}
	return out
}

// trimNonEmpty trims each entry and drops empties, preserving order and case
// (competitor app names are display strings, not normalized search terms).
func trimNonEmpty(in []string) []string {
	out := make([]string, 0, len(in))
	for _, s := range in {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// summarizeRounds flattens prior-round terms into the never-repeat list and
// renders one compact line per round for the model context, e.g.
// "round 2: 3 terms, depth 40, 7 found, 2 kept; stops: dry x2, exhausted x1".
// The depth segment comes from the round's page_cap setting and the stops
// segment aggregates per-term stop reasons from the progress jsonb; both are
// omitted when absent.
func summarizeRounds(summaries []models.HuntRoundSummary) (tried, lines []string) {
	for _, s := range summaries {
		var terms []string
		if len(s.Terms) > 0 {
			_ = json.Unmarshal(s.Terms, &terms)
		}
		tried = append(tried, terms...)

		var b strings.Builder
		// The label carries the REAL round id — it is what the report/results
		// tools take, and an ordinal here sent the model chasing "round 1"
		// when the actual id was 4 (live-gate finding, 2026-07-14).
		fmt.Fprintf(&b, "round id %d: %d terms", s.RoundID, s.TermCount)
		if depth := settingInt(s.Settings, "page_cap"); depth > 0 {
			fmt.Fprintf(&b, ", depth %d", depth)
		}
		fmt.Fprintf(&b, ", %d found, %d kept", s.FoundCount, s.KeptCount)
		if stops := stopSummary(s.Progress); stops != "" {
			fmt.Fprintf(&b, "; stops: %s", stops)
		}
		lines = append(lines, b.String())
	}
	return tried, lines
}

// settingInt reads one integer key from a settings jsonb object, 0 when absent
// or unparseable.
func settingInt(raw json.RawMessage, key string) int {
	if len(raw) == 0 {
		return 0
	}
	var m map[string]int
	if err := json.Unmarshal(raw, &m); err != nil {
		return 0
	}
	return m[key]
}

// stopSummary aggregates the per-term stop reasons in a round's progress jsonb
// into a compact "dry x2, exhausted x1" string (reasons sorted for stability).
// Returns "" when there is no per-term progress to report.
func stopSummary(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var p struct {
		Terms map[string]struct {
			Stop string `json:"stop"`
		} `json:"terms"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		return ""
	}
	counts := map[string]int{}
	for _, t := range p.Terms {
		if t.Stop != "" {
			counts[t.Stop]++
		}
	}
	if len(counts) == 0 {
		return ""
	}
	reasons := make([]string, 0, len(counts))
	for r := range counts {
		reasons = append(reasons, r)
	}
	sort.Strings(reasons)
	segs := make([]string, 0, len(reasons))
	for _, r := range reasons {
		segs = append(segs, fmt.Sprintf("%s x%d", r, counts[r]))
	}
	return strings.Join(segs, ", ")
}

func emptyIfNil(in []string) []string {
	if in == nil {
		return []string{}
	}
	return in
}
