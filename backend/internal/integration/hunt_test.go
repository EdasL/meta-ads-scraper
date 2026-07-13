package integration

// Hunt HTTP endpoints — NON-Gemini paths only. The two Gemini-dependent turns
// (POST /api/hunts and POST /api/hunts/:id/messages, which call Peon) are
// covered by the live gate, not here: they require GEMINI_API_KEY and a real
// model round-trip. This file exercises everything the router does WITHOUT
// touching Gemini — list, ownership 404s, round validation + enqueue, and the
// read aggregate.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
	"golang.org/x/crypto/bcrypt"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

const huntForeignEmail = "hunt-foreign@test.local"

// seedHunt inserts a hunt owned by email and registers cleanup (pipeline_jobs
// for its rounds first — no FK cascade reaches them — then the hunt, which
// cascades to messages/rounds/results).
func seedHunt(t *testing.T, email, title string) int {
	t.Helper()
	var id int
	require.NoError(t, db.DB().Raw(
		`INSERT INTO frontend.hunts (user_email, title) VALUES (?, ?) RETURNING id`,
		email, title).Scan(&id).Error)
	t.Cleanup(func() {
		require.NoError(t, db.DB().Exec(
			`DELETE FROM public.pipeline_jobs WHERE kind = 'hunt' AND advertiser_id IN (
				SELECT id FROM frontend.hunt_rounds WHERE hunt_id = ?)`, id).Error)
		require.NoError(t, db.DB().Exec(`DELETE FROM frontend.hunts WHERE id = ?`, id).Error)
	})
	return id
}

func seedHuntMessage(t *testing.T, huntID int, role, content string) int {
	t.Helper()
	var id int
	require.NoError(t, db.DB().Raw(
		`INSERT INTO frontend.hunt_messages (hunt_id, role, content) VALUES (?, ?, ?) RETURNING id`,
		huntID, role, content).Scan(&id).Error)
	return id
}

// freshHuntKey mints a brand-new user + personal fd_ key so a read request can
// present an identity with zero hunts of its own.
func freshHuntKey(t *testing.T, email string) string {
	t.Helper()
	ctx := context.Background()
	hash, _ := bcrypt.GenerateFromPassword([]byte("x"), bcrypt.DefaultCost)
	require.NoError(t, repositories.UpsertUser(ctx, db.DB(), email, string(hash), "IT", "member"))
	k, err := repositories.CreateAPIKey(ctx, db.DB(), email, "hunt-it")
	require.NoError(t, err)
	return k
}

func getWithKey(t *testing.T, r http.Handler, path, key string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	req.Header.Set("Authorization", "Bearer "+key)
	r.ServeHTTP(w, req)
	return w
}

func postRounds(t *testing.T, r http.Handler, huntID int, body string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req := authedInternal(httptest.NewRequest(http.MethodPost,
		"/api/hunts/"+strconv.Itoa(huntID)+"/rounds", strings.NewReader(body)))
	req.Header.Set("Content-Type", "application/json")
	r.ServeHTTP(w, req)
	return w
}

func TestHuntListEmptyForFreshEmail(t *testing.T) {
	r := router()
	key := freshHuntKey(t, "hunt-empty@test.local")
	w := getWithKey(t, r, "/api/hunts", key)
	require.Equal(t, http.StatusOK, w.Code)
	require.Equal(t, "[]", strings.TrimSpace(w.Body.String()), "no hunts → empty array, not null")
}

func TestHuntStartRoundHappyPath(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()

	huntID := seedHunt(t, authedEmail, "sleep stories")
	msgID := seedHuntMessage(t, huntID, "assistant", "here are some angles")

	// Mixed-case, padded, duplicate terms exercise normalization.
	body := `{"message_id":` + strconv.Itoa(msgID) +
		`,"terms":["Sleep Stories App","  ai bedtime audio  ","Sleep Stories App"],` +
		`"competitor_apps":["Calm"]}`
	w := postRounds(t, r, huntID, body)
	require.Equal(t, http.StatusAccepted, w.Code)

	var round struct {
		ID          int             `json:"id"`
		MessageID   *int            `json:"message_id"`
		Terms       []string        `json:"terms"`
		Competitors []string        `json:"competitor_apps"`
		Status      string          `json:"status"`
		Progress    json.RawMessage `json:"progress"`
	}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &round))
	require.Greater(t, round.ID, 0)
	require.NotNil(t, round.MessageID)
	require.Equal(t, msgID, *round.MessageID)
	require.Equal(t, "queued", round.Status)
	// Normalized: lowercased, trimmed, deduped.
	require.Equal(t, []string{"sleep stories app", "ai bedtime audio"}, round.Terms)
	require.Equal(t, []string{"Calm"}, round.Competitors)

	// The round landed in the DB, queued.
	var status string
	require.NoError(t, db.DB().Raw(
		`SELECT status FROM frontend.hunt_rounds WHERE id = ?`, round.ID).Scan(&status).Error)
	require.Equal(t, "queued", status)

	// Exactly one pipeline_jobs row: kind=hunt, advertiser_id=roundID.
	var kind, jobStatus string
	require.NoError(t, db.DB().Raw(
		`SELECT kind, status FROM public.pipeline_jobs WHERE kind = 'hunt' AND advertiser_id = ?`,
		round.ID).Row().Scan(&kind, &jobStatus))
	require.Equal(t, "hunt", kind)
	require.Equal(t, "queued", jobStatus)
}

func TestHuntStartRoundWithSettings(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	huntID := seedHunt(t, authedEmail, "with settings")

	w := postRounds(t, r, huntID,
		`{"terms":["sleep stories app"],"competitor_apps":[],"settings":{"page_cap":30,"patience":6}}`)
	require.Equal(t, http.StatusAccepted, w.Code)

	// The 202 response echoes the stored (clamped) settings.
	var round struct {
		Settings map[string]int `json:"settings"`
	}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &round))
	require.Equal(t, 30, round.Settings["page_cap"])
	require.Equal(t, 6, round.Settings["patience"])

	// GET shows the round's settings stored.
	gw := httptest.NewRecorder()
	r.ServeHTTP(gw, authed(httptest.NewRequest(http.MethodGet, "/api/hunts/"+strconv.Itoa(huntID), nil)))
	require.Equal(t, http.StatusOK, gw.Code)
	var resp struct {
		Rounds []struct {
			Settings map[string]int `json:"settings"`
		} `json:"rounds"`
	}
	require.NoError(t, json.Unmarshal(gw.Body.Bytes(), &resp))
	require.Len(t, resp.Rounds, 1)
	require.Equal(t, 30, resp.Rounds[0].Settings["page_cap"])
	require.Equal(t, 6, resp.Rounds[0].Settings["patience"])
}

func TestHuntStartRoundSettingsClamped(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	huntID := seedHunt(t, authedEmail, "clamp settings")

	// page_cap 999 is above the 100 ceiling → the stored row clamps to 100.
	w := postRounds(t, r, huntID, `{"terms":["sleep stories app"],"settings":{"page_cap":999}}`)
	require.Equal(t, http.StatusAccepted, w.Code)

	var stored string
	require.NoError(t, db.DB().Raw(
		`SELECT settings::text FROM frontend.hunt_rounds WHERE hunt_id = ?`, huntID).Scan(&stored).Error)
	var st map[string]int
	require.NoError(t, json.Unmarshal([]byte(stored), &st))
	require.Equal(t, 100, st["page_cap"], "page_cap 999 clamps to 100")
}

func TestHuntStartRoundNoSettings(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	huntID := seedHunt(t, authedEmail, "no settings")

	w := postRounds(t, r, huntID, `{"terms":["sleep stories app"],"competitor_apps":[]}`)
	require.Equal(t, http.StatusAccepted, w.Code)

	// Absent settings → the NOT NULL column defaults to '{}'.
	var stored string
	require.NoError(t, db.DB().Raw(
		`SELECT settings::text FROM frontend.hunt_rounds WHERE hunt_id = ?`, huntID).Scan(&stored).Error)
	require.Equal(t, "{}", stored)

	gw := httptest.NewRecorder()
	r.ServeHTTP(gw, authed(httptest.NewRequest(http.MethodGet, "/api/hunts/"+strconv.Itoa(huntID), nil)))
	require.Equal(t, http.StatusOK, gw.Code)
	var resp struct {
		Rounds []struct {
			Settings json.RawMessage `json:"settings"`
		} `json:"rounds"`
	}
	require.NoError(t, json.Unmarshal(gw.Body.Bytes(), &resp))
	require.Len(t, resp.Rounds, 1)
	require.Equal(t, "{}", strings.TrimSpace(string(resp.Rounds[0].Settings)))
}

func TestHuntGetReturnsToolLog(t *testing.T) {
	r := router()
	huntID := seedHunt(t, authedEmail, "tool log thread")

	// An assistant turn that read round telemetry carries a tool_log jsonb array.
	require.NoError(t, db.DB().Exec(
		`INSERT INTO frontend.hunt_messages (hunt_id, role, content, tool_log)
		 VALUES (?, 'assistant', 'checked the last round', CAST(? AS jsonb))`,
		huntID, `["checked round 1's report","looked at round 1's results"]`).Error)

	gw := httptest.NewRecorder()
	r.ServeHTTP(gw, authed(httptest.NewRequest(http.MethodGet, "/api/hunts/"+strconv.Itoa(huntID), nil)))
	require.Equal(t, http.StatusOK, gw.Code)
	var resp struct {
		Messages []struct {
			Role    string          `json:"role"`
			ToolLog json.RawMessage `json:"tool_log"`
		} `json:"messages"`
	}
	require.NoError(t, json.Unmarshal(gw.Body.Bytes(), &resp))
	require.Len(t, resp.Messages, 1)
	require.Equal(t, "assistant", resp.Messages[0].Role)
	var log []string
	require.NoError(t, json.Unmarshal(resp.Messages[0].ToolLog, &log))
	require.Equal(t, []string{"checked round 1's report", "looked at round 1's results"}, log)
}

func TestHuntStartRoundNullMessageID(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	huntID := seedHunt(t, authedEmail, "no message ref")

	w := postRounds(t, r, huntID, `{"message_id":null,"terms":["sleep stories app"],"competitor_apps":[]}`)
	require.Equal(t, http.StatusAccepted, w.Code)
	var round struct {
		MessageID *int `json:"message_id"`
	}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &round))
	require.Nil(t, round.MessageID, "message_id may be null")
}

func TestHuntStartRoundValidation(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	huntID := seedHunt(t, authedEmail, "validation")

	// 0 terms → 422.
	w := postRounds(t, r, huntID, `{"terms":[],"competitor_apps":[]}`)
	require.Equal(t, http.StatusUnprocessableEntity, w.Code, "0 terms rejected")

	// 16 terms → 422.
	terms := make([]string, 16)
	for i := range terms {
		terms[i] = "term " + strconv.Itoa(i)
	}
	tj, _ := json.Marshal(terms)
	w = postRounds(t, r, huntID, `{"terms":`+string(tj)+`,"competitor_apps":[]}`)
	require.Equal(t, http.StatusUnprocessableEntity, w.Code, "16 terms rejected")

	// 4 competitors → 422.
	w = postRounds(t, r, huntID,
		`{"terms":["sleep stories app"],"competitor_apps":["A","B","C","D"]}`)
	require.Equal(t, http.StatusUnprocessableEntity, w.Code, "4 competitors rejected")
}

func TestHuntForeignEmail404(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	// Hunt owned by someone else.
	huntID := seedHunt(t, huntForeignEmail, "not yours")

	// GET /:id as authedEmail → 404 (do not leak existence).
	w := httptest.NewRecorder()
	r.ServeHTTP(w, authed(httptest.NewRequest(http.MethodGet, "/api/hunts/"+strconv.Itoa(huntID), nil)))
	require.Equal(t, http.StatusNotFound, w.Code)

	// POST rounds as authedEmail → 404.
	w = postRounds(t, r, huntID, `{"terms":["sleep stories app"],"competitor_apps":[]}`)
	require.Equal(t, http.StatusNotFound, w.Code)

	// A missing hunt is also 404.
	w = httptest.NewRecorder()
	r.ServeHTTP(w, authed(httptest.NewRequest(http.MethodGet, "/api/hunts/999999999", nil)))
	require.Equal(t, http.StatusNotFound, w.Code)
}

func TestHuntGetReturnsThread(t *testing.T) {
	r := router()
	huntID := seedHunt(t, authedEmail, "full thread")
	seedHuntMessage(t, huntID, "user", "find me sleep apps")
	seedHuntMessage(t, huntID, "assistant", "here are some angles")

	// Seed a round (via SQL, bypassing the enqueue) and a result.
	var roundID int
	require.NoError(t, db.DB().Raw(
		`INSERT INTO frontend.hunt_rounds (hunt_id, terms, competitor_apps)
		 VALUES (?, '["sleep stories app"]'::jsonb, '["Calm"]'::jsonb) RETURNING id`,
		huntID).Scan(&roundID).Error)
	require.NoError(t, db.DB().Exec(
		`INSERT INTO frontend.hunt_results
		 (round_id, page_id, page_name, store, store_id, source_term, was_new, triage)
		 VALUES (?, 'PG1', 'Kept Co', 'play', 'com.kept', 'sleep stories app', true, 'pending')`,
		roundID).Error)

	w := httptest.NewRecorder()
	r.ServeHTTP(w, authed(httptest.NewRequest(http.MethodGet, "/api/hunts/"+strconv.Itoa(huntID), nil)))
	require.Equal(t, http.StatusOK, w.Code)

	var resp struct {
		Hunt struct {
			ID    int    `json:"id"`
			Title string `json:"title"`
		} `json:"hunt"`
		Messages []struct {
			Role    string `json:"role"`
			Content string `json:"content"`
		} `json:"messages"`
		Rounds []struct {
			ID          int      `json:"id"`
			Terms       []string `json:"terms"`
			Competitors []string `json:"competitor_apps"`
			Status      string   `json:"status"`
		} `json:"rounds"`
		Results []struct {
			PageID     string `json:"page_id"`
			SourceTerm string `json:"source_term"`
		} `json:"results"`
	}
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &resp))

	require.Equal(t, huntID, resp.Hunt.ID)
	require.Equal(t, "full thread", resp.Hunt.Title)
	require.Len(t, resp.Messages, 2)
	require.Equal(t, "user", resp.Messages[0].Role)
	require.Equal(t, "assistant", resp.Messages[1].Role)
	require.Len(t, resp.Rounds, 1)
	// jsonb fields unmarshal cleanly.
	require.Equal(t, []string{"sleep stories app"}, resp.Rounds[0].Terms)
	require.Equal(t, []string{"Calm"}, resp.Rounds[0].Competitors)
	require.Equal(t, "queued", resp.Rounds[0].Status)
	require.Len(t, resp.Results, 1)
	require.Equal(t, "PG1", resp.Results[0].PageID)
	require.Equal(t, "sleep stories app", resp.Results[0].SourceTerm)
}
