package integration

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
)

// postEnrich fires POST /api/enrich/<id> with an empty JSON body and returns
// the recorder. Enrich is a write route: RequireToken fails closed and accepts
// only the internal token, so the request carries the internal token +
// X-User-Email. Callers MUST t.Setenv(APITokenEnv, writeToken) before building
// the router they pass in, since the middleware reads the env at construction.
func postEnrich(t *testing.T, r http.Handler, id int) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	req := authedInternal(httptest.NewRequest(http.MethodPost, "/api/enrich/"+strconv.Itoa(id), strings.NewReader("{}")))
	req.Header.Set("Content-Type", "application/json")
	r.ServeHTTP(w, req)
	return w
}

// jobRow reads the latest enrich_jobs row for an advertiser straight from the
// DB — the wire shape maps 'queued' to "running", so DB state is asserted here.
func jobRow(t *testing.T, advertiserID int) (status string, count int) {
	t.Helper()
	require.NoError(t, db.DB().Raw(
		`SELECT status FROM public.enrich_jobs WHERE advertiser_id = ?
		 ORDER BY started_at DESC LIMIT 1`, advertiserID).Scan(&status).Error)
	require.NoError(t, db.DB().Raw(
		`SELECT count(*) FROM public.enrich_jobs WHERE advertiser_id = ?`,
		advertiserID).Scan(&count).Error)
	return status, count
}

func TestEnrichTriggerEnqueuesJob(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	w := postEnrich(t, r, advID)
	require.Equal(t, http.StatusAccepted, w.Code)
	// The wire status is "running" (UI contract); the DB row is 'queued' for
	// the worker-VM daemon to claim.
	require.Contains(t, w.Body.String(), `"status":"running"`)
	status, count := jobRow(t, advID)
	require.Equal(t, "queued", status)
	require.Equal(t, 1, count)
}

func TestEnrichTriggerDedupesQueuedJob(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	w1 := postEnrich(t, r, adv2ID)
	require.Equal(t, http.StatusAccepted, w1.Code)
	w2 := postEnrich(t, r, adv2ID)
	require.Equal(t, http.StatusAccepted, w2.Code)
	_, count := jobRow(t, adv2ID)
	require.Equal(t, 1, count, "second call must not enqueue a new job (dedupe)")
}

func TestEnrichStatusReturnsLatest(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	_ = postEnrich(t, r, adv3ID)
	w, body := get(t, r, "/api/enrich/"+strconv.Itoa(adv3ID))
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, string(body["status"]), "running")
}
