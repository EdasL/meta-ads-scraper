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
	rows, err := repositories.RoundResults(t.ctx, t.g, t.huntID, roundID, triage, 50, 0)
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
// backoff and may browse the web mid-turn, so 90s covers a slow-but-succeeding
// call without hanging the client.
const huntProposalTimeout = 90 * time.Second

// maxHuntTerms / maxHuntCompetitors bound a round the founder approves before
// it hits the scraper. They alias the helpers constants so this path and the
// run_hunt MCP tool cannot drift apart on what a legal round is.
// maxHuntTitleLen truncates the first user message into a thread title.
const (
	maxHuntTerms       = helpers.MaxHuntTerms
	maxHuntCompetitors = helpers.MaxHuntCompetitors
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

// Delete handles DELETE /api/hunts/:hunt_id — removes an owned thread and its
// messages, rounds, and results (cascade). 404s on a foreign or missing hunt via
// requireOwner, so it can never delete another user's hunt.
func (c *HuntController) Delete(ctx *gin.Context) {
	huntID, _, ok := c.requireOwner(ctx)
	if !ok {
		return
	}
	rctx, cancel := reqCtx(ctx)
	defer cancel()
	if err := repositories.DeleteHunt(rctx, db.DB(), huntID); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not delete hunt.")
		return
	}
	ctx.Status(http.StatusNoContent)
}

// huntCardState is the founder's live proposal card — the terms/competitors the
// chips currently show, which may differ from the last proposal if they edited.
type huntCardState struct {
	Terms          []string `json:"terms"`
	CompetitorApps []string `json:"competitor_apps"`
}

type huntMessageBody struct {
	Message   string         `json:"message"`
	ProductID *int64         `json:"product_id"`
	FolderID  *int64         `json:"folder_id"`
	CardState *huntCardState `json:"card_state"`
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
	huntID, err := repositories.CreateHuntWithProduct(rctx, db.DB(), u.Email, huntTitle(message), body.FolderID, body.ProductID)
	if errors.Is(err, repositories.ErrProductFolderMismatch) {
		helpers.Detail(ctx, http.StatusUnprocessableEntity, err.Error())
		return
	}
	if errors.Is(err, repositories.ErrProductNotFound) || errors.Is(err, repositories.ErrFolderNotFound) {
		helpers.Detail(ctx, http.StatusNotFound, "product or folder not found.")
		return
	}
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
	c.writeThread(ctx, huntID, http.StatusCreated)
}

// Message handles POST /api/hunts/:hunt_id/messages — the same flow as Create on
// an existing, owned thread.
func (c *HuntController) Message(ctx *gin.Context) {
	huntID, email, ok := c.requireOwner(ctx)
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
	rounds, err := repositories.ListHuntRounds(rctx, db.DB(), huntID)
	if err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	priorHunts, err := repositories.PriorHuntLines(rctx, db.DB(), email, huntID)
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
		Message:         message,
		History:         history,
		TriedTerms:      tried,
		RoundSummaries:  roundLines,
		PendingProposal: pendingProposalText(prior, rounds),
		CardState:       cardStateText(body.CardState),
		PriorHunts:      priorHunts,
	}) {
		return
	}
	c.writeThread(ctx, huntID, http.StatusCreated)
}

// executeTurn runs one Peon agent turn and persists the assistant message,
// returning the new message id and the reply text. Shared by the chat turns
// (runTurn) and the internal round-completion hook (RoundCompleted). parent
// should be the request context; the turn itself is bounded by
// huntProposalTimeout. When requireReply is true an empty model reply is a
// failure returned BEFORE any DB write, so no message row lands (the completion
// hook relies on this so its daemon fallback cannot double-message). Rounds
// start only from the founder's Start hunt button, never from a turn.
func (c *HuntController) executeTurn(parent context.Context, huntID int, input helpers.HuntTurnInput, requireReply bool) (int, string, error) {
	gctx, cancel := context.WithTimeout(parent, huntProposalTimeout)
	defer cancel()
	folder, err := repositories.HuntFolderContext(gctx, db.DB(), huntID)
	if err != nil {
		return 0, "", err
	}
	if folder != nil {
		encoded, err := json.Marshal(folder)
		if err != nil {
			return 0, "", err
		}
		input.FolderContext = string(encoded)
	}

	tools := huntTools{ctx: gctx, g: db.DB(), huntID: huntID}
	turn, err := c.gemini.GenerateHuntTurn(gctx, input, tools)
	if err != nil {
		return 0, "", err
	}
	if requireReply && strings.TrimSpace(turn.Reply) == "" {
		return 0, "", fmt.Errorf("empty completion reply")
	}

	var proposalJSON json.RawMessage
	if turn.Proposal != nil {
		if proposalJSON, err = marshalHuntPlan(*turn.Proposal); err != nil {
			return 0, "", err
		}
	}
	var toolLogJSON json.RawMessage
	if len(turn.ToolLog) > 0 {
		if toolLogJSON, err = json.Marshal(turn.ToolLog); err != nil {
			return 0, "", err
		}
	}

	wctx, cancel2 := context.WithTimeout(parent, queryTimeout)
	defer cancel2()
	var msgID int
	err = db.WithWriteTx(wctx, db.DB(), func(tx *gorm.DB) error {
		var e error
		msgID, e = repositories.InsertHuntMessageTx(tx, huntID, "assistant", turn.Reply, proposalJSON, toolLogJSON)
		return e
	})
	if err != nil {
		return 0, "", err
	}
	return msgID, turn.Reply, nil
}

// runTurn runs one Peon chat turn and appends the assistant message. On any
// executeTurn error it writes 502 and returns ok=false, leaving the
// already-persisted user message intact. The assistant message carries the
// reply as content, a {terms, competitor_apps, settings} proposal jsonb when
// the model proposed a round, and a tool_log jsonb when the loop read any round
// telemetry. Rounds start only from the founder's Start hunt button.
func (c *HuntController) runTurn(ctx *gin.Context, huntID int, input helpers.HuntTurnInput) bool {
	if _, _, err := c.executeTurn(ctx.Request.Context(), huntID, input, false); err != nil {
		helpers.Detail(ctx, http.StatusBadGateway, "Peon could not answer. Try again.")
		return false
	}
	return true
}

// roundCompletedInstruction is the synthetic turn the daemon triggers when a
// round finishes. It is NOT stored as a user message — only Peon's answer lands
// in the thread.
func roundCompletedInstruction(roundID int) string {
	return fmt.Sprintf(
		"Hunt round %d just finished scraping. Report the outcome to the founder: "+
			"call get_round_report and get_round_results for round id %d first. Name the "+
			"promising kept apps plainly, say which terms produced finds and which found "+
			"nothing, and note anything off target. Then, unless there is nothing worth "+
			"trying, call propose_round with a concrete next step — broader terms, deeper "+
			"paging (min_pages / page_cap), dropping dead terms. Your reply text must "+
			"never be empty.", roundID, roundID)
}

// RoundCompleted handles POST /api/internal/hunts/rounds/:round_id/completed —
// the hunt daemon's completion hook (RequireInternalToken; no user session). On
// success exactly one assistant message is written. Any non-200 tells the
// daemon to write its canned fallback message instead, so a round can never
// finish silently.
func (c *HuntController) RoundCompleted(ctx *gin.Context) {
	roundID, err := strconv.Atoi(ctx.Param("round_id"))
	if err != nil {
		helpers.Detail(ctx, http.StatusNotFound, "no such round.")
		return
	}
	if c.gemini == nil {
		helpers.Detail(ctx, http.StatusServiceUnavailable, "Peon is not configured.")
		return
	}
	rctx, cancel := reqCtx(ctx)
	huntID, email, err := repositories.HuntForRound(rctx, db.DB(), roundID)
	if err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	if huntID == 0 {
		cancel()
		helpers.Detail(ctx, http.StatusNotFound, "no such round.")
		return
	}
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
	priorHunts, err := repositories.PriorHuntLines(rctx, db.DB(), email, huntID)
	if err != nil {
		cancel()
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return
	}
	cancel()

	history := make([]helpers.HuntHistoryTurn, 0, len(prior))
	for _, m := range prior {
		history = append(history, helpers.HuntHistoryTurn{Role: m.Role, Content: m.Content})
	}
	tried, roundLines := summarizeRounds(summaries)

	msgID, _, err := c.executeTurn(ctx.Request.Context(), huntID, helpers.HuntTurnInput{
		Message:        roundCompletedInstruction(roundID),
		History:        history,
		TriedTerms:     tried,
		RoundSummaries: roundLines,
		PriorHunts:     priorHunts,
		RequireReply:   true,
	}, true)
	if err != nil {
		// A failed or empty wrap-up wrote no row (requireReply=true) — let the
		// daemon write the canned counts message instead so the round is never
		// silent.
		helpers.Detail(ctx, http.StatusBadGateway, "Peon could not report this round.")
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"message_id": msgID})
}

// writeThread returns {hunt_id, messages} after a chat turn. Rounds start only
// from the founder's Start hunt button, never from a chat turn. The GET
// /hunts/:id shape is untouched.
func (c *HuntController) writeThread(ctx *gin.Context, huntID, status int) {
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
	ctx.JSON(status, gin.H{"hunt_id": huntID, "messages": msgs})
}

// marshalHuntPlan renders a proposed plan as the {terms, competitor_apps,
// settings, intent} proposal jsonb the assistant message stores. The settings
// key is always present (an empty object when nothing was set); intent is
// omitted when empty.
func marshalHuntPlan(p helpers.HuntPlan) (json.RawMessage, error) {
	return json.Marshal(struct {
		Terms          []string       `json:"terms"`
		CompetitorApps []string       `json:"competitor_apps"`
		Settings       map[string]int `json:"settings"`
		Intent         string         `json:"intent,omitempty"`
	}{
		Terms:          emptyIfNil(p.Terms),
		CompetitorApps: emptyIfNil(p.CompetitorApps),
		Settings:       clampedOrEmpty(p.Settings),
		Intent:         p.Intent,
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
	results, err := repositories.ListHuntResults(rctx, db.DB(), huntID, email)
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
	MinPages     *int `json:"min_pages"`
}

type startRoundBody struct {
	MessageID      *int              `json:"message_id"`
	Terms          []string          `json:"terms"`
	CompetitorApps []string          `json:"competitor_apps"`
	Settings       *huntSettingsBody `json:"settings"`
	Intent         *string           `json:"intent"`
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

	// Optional plain-sentence intent the founder is hunting for, trimmed and
	// capped; empty/whitespace becomes SQL NULL.
	var intent *string
	if body.Intent != nil {
		v := strings.TrimSpace(*body.Intent)
		if r := []rune(v); len(r) > 200 {
			v = string(r[:200])
		}
		if v != "" {
			intent = &v
		}
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
			MinPages:     body.Settings.MinPages,
		})
		settingsJSON, _ = json.Marshal(clamped)
	}

	rctx, cancel := reqCtx(ctx)
	defer cancel()
	roundID, err := repositories.InsertHuntRound(rctx, db.DB(), huntID, body.MessageID, termsJSON, compsJSON, settingsJSON, intent)
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
func normalizeHuntTerms(in []string) []string { return helpers.NormalizeHuntTerms(in) }

// trimNonEmpty trims each entry and drops empties, preserving order and case
// (competitor app names are display strings, not normalized search terms).
func trimNonEmpty(in []string) []string { return helpers.TrimHuntCompetitors(in) }

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
		if s.RelevantCount > 0 || s.OffTargetCount > 0 {
			fmt.Fprintf(&b, " (%d relevant, %d off-target)", s.RelevantCount, s.OffTargetCount)
		}
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

// pendingProposalText formats the newest assistant proposal that no round has
// consumed — the same rule the web uses to decide which proposal card renders.
// Returns "" when every proposal already started a round (or there are none).
func pendingProposalText(msgs []models.HuntMessageRow, rounds []models.HuntRoundRow) string {
	started := map[int]bool{}
	for _, r := range rounds {
		if r.MessageID != nil {
			started[*r.MessageID] = true
		}
	}
	for i := len(msgs) - 1; i >= 0; i-- {
		m := msgs[i]
		if m.Role != "assistant" || len(m.Proposal) == 0 || started[m.ID] {
			continue
		}
		var p struct {
			Terms          []string       `json:"terms"`
			CompetitorApps []string       `json:"competitor_apps"`
			Settings       map[string]int `json:"settings"`
			Intent         string         `json:"intent"`
		}
		if err := json.Unmarshal(m.Proposal, &p); err != nil {
			return ""
		}
		out := "terms: " + strings.Join(p.Terms, ", ")
		if len(p.CompetitorApps) > 0 {
			out += " · competitors: " + strings.Join(p.CompetitorApps, ", ")
		}
		if len(p.Settings) > 0 {
			out += " · settings: " + settingsLabel(p.Settings)
		}
		if p.Intent != "" {
			out += " · intent: " + p.Intent
		}
		return out
	}
	return ""
}

// settingsLabel renders a proposal's settings map in a stable key order.
func settingsLabel(s map[string]int) string {
	var parts []string
	for _, k := range []string{"page_cap", "patience", "min_pages", "listing_pages"} {
		if v, ok := s[k]; ok {
			parts = append(parts, fmt.Sprintf("%s %d", k, v))
		}
	}
	return strings.Join(parts, ", ")
}

// cardStateText renders the founder's live proposal card for the model context;
// "" when the card is empty or absent.
func cardStateText(cs *huntCardState) string {
	if cs == nil || len(cs.Terms) == 0 {
		return ""
	}
	out := "terms: " + strings.Join(cs.Terms, ", ")
	if len(cs.CompetitorApps) > 0 {
		out += " (competitors: " + strings.Join(cs.CompetitorApps, ", ") + ")"
	}
	return out
}

func emptyIfNil(in []string) []string {
	if in == nil {
		return []string{}
	}
	return in
}
