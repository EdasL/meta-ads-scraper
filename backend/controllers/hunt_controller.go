package controllers

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

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
	if !c.proposeAndAppend(ctx, huntID, message, nil, nil, nil) {
		return
	}
	c.writeThread(ctx, huntID, http.StatusCreated)
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
	if !c.proposeAndAppend(ctx, huntID, message, history, tried, roundLines) {
		return
	}
	c.writeThread(ctx, huntID, http.StatusCreated)
}

// proposeAndAppend runs the Gemini turn under a 30s budget and appends the
// assistant message. It writes the error response (502) and returns false on
// failure — leaving the already-persisted user message intact. The stored
// proposal jsonb carries only {terms, competitor_apps}; the reply is the message
// content.
func (c *HuntController) proposeAndAppend(ctx *gin.Context, huntID int, message string,
	history []helpers.HuntHistoryTurn, tried, roundLines []string) bool {
	gctx, cancel := context.WithTimeout(ctx.Request.Context(), huntProposalTimeout)
	defer cancel()
	prop, err := c.gemini.GenerateHuntProposal(gctx, helpers.HuntProposalInput{
		Message:        message,
		History:        history,
		TriedTerms:     tried,
		RoundSummaries: roundLines,
	})
	if err != nil {
		helpers.Detail(ctx, http.StatusBadGateway, "Peon could not answer. Try again.")
		return false
	}

	proposalJSON, err := json.Marshal(struct {
		Terms          []string `json:"terms"`
		CompetitorApps []string `json:"competitor_apps"`
	}{Terms: emptyIfNil(prop.Terms), CompetitorApps: emptyIfNil(prop.CompetitorApps)})
	if err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "something went wrong.")
		return false
	}

	rctx, cancel2 := reqCtx(ctx)
	defer cancel2()
	if _, err := repositories.InsertHuntMessage(rctx, db.DB(), huntID, "assistant", prop.Reply, proposalJSON, nil); err != nil {
		helpers.Detail(ctx, http.StatusInternalServerError, "could not save reply.")
		return false
	}
	return true
}

// writeThread returns {hunt_id, messages} after a chat turn.
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

type startRoundBody struct {
	MessageID      *int     `json:"message_id"`
	Terms          []string `json:"terms"`
	CompetitorApps []string `json:"competitor_apps"`
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

	rctx, cancel := reqCtx(ctx)
	defer cancel()
	roundID, err := repositories.InsertHuntRound(rctx, db.DB(), huntID, body.MessageID, termsJSON, compsJSON, nil)
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
// renders one compact line per round for the model context.
func summarizeRounds(summaries []models.HuntRoundSummary) (tried, lines []string) {
	for i, s := range summaries {
		var terms []string
		if len(s.Terms) > 0 {
			_ = json.Unmarshal(s.Terms, &terms)
		}
		tried = append(tried, terms...)
		lines = append(lines, fmt.Sprintf("round %d: %d terms, %d found, %d kept",
			i+1, s.TermCount, s.FoundCount, s.KeptCount))
	}
	return tried, lines
}

func emptyIfNil(in []string) []string {
	if in == nil {
		return []string{}
	}
	return in
}
