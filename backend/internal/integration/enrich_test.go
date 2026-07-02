package integration

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/controllers"
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

func TestEnrichTriggerInsertsAndSpawns(t *testing.T) {
	var spawned [][2]int
	controllers.SetSpawnEnrichForTest(func(advertiserID, jobID int) error {
		spawned = append(spawned, [2]int{advertiserID, jobID})
		return nil
	})
	defer controllers.ResetSpawnEnrichForTest()

	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	w := postEnrich(t, r, advID)
	require.Equal(t, http.StatusAccepted, w.Code)
	require.Contains(t, w.Body.String(), `"status":"running"`)
	require.Len(t, spawned, 1, "spawn called exactly once")
	require.Equal(t, advID, spawned[0][0])
}

func TestEnrichTriggerDedupesRunningJob(t *testing.T) {
	count := 0
	controllers.SetSpawnEnrichForTest(func(advertiserID, jobID int) error {
		count++
		return nil
	})
	defer controllers.ResetSpawnEnrichForTest()

	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	w1 := postEnrich(t, r, adv2ID)
	require.Equal(t, http.StatusAccepted, w1.Code)
	w2 := postEnrich(t, r, adv2ID)
	require.Equal(t, http.StatusAccepted, w2.Code)
	require.Equal(t, 1, count, "second call must not spawn a new subprocess (dedupe)")
}

func TestEnrichStatusReturnsLatest(t *testing.T) {
	controllers.SetSpawnEnrichForTest(func(int, int) error { return nil })
	defer controllers.ResetSpawnEnrichForTest()

	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	_ = postEnrich(t, r, adv3ID)
	w, body := get(t, r, "/api/enrich/"+strconv.Itoa(adv3ID))
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, string(body["status"]), "running")
}
