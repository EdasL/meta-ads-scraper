package integration

// On-demand gem add from a pasted Ad Library link. Covers link rejection
// (writes nothing), the happy path (one advertiser + one queued add_page job),
// idempotency (a second paste returns the same job, never a duplicate), and
// the status poll. The scrape itself runs on the worker VM and is out of scope
// here — this file asserts what the API does to the DB.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
)

const addGemPageID = "900900900900"

type addGemBody struct {
	JobID        int64   `json:"job_id"`
	AdvertiserID int64   `json:"advertiser_id"`
	Status       string  `json:"status"`
	Phase        *string `json:"phase"`
	AdCount      int     `json:"ad_count"`
	AlreadyKnown bool    `json:"already_known"`
}

// postAddGem fires POST /api/add-gem with the given url. Like enrich, this is a
// write route behind RequireToken, so the request carries the internal token.
func postAddGem(t *testing.T, r http.Handler, url string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	body := `{"url":` + strconv.Quote(url) + `}`
	req := authedInternal(httptest.NewRequest(http.MethodPost, "/api/add-gem", strings.NewReader(body)))
	req.Header.Set("Content-Type", "application/json")
	r.ServeHTTP(w, req)
	return w
}

func decodeAddGem(t *testing.T, w *httptest.ResponseRecorder) addGemBody {
	t.Helper()
	var out addGemBody
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out), "body: %s", w.Body.String())
	return out
}

// cleanupAddGem removes the advertiser this file creates and its jobs. Ordered:
// pipeline_jobs has no FK cascade to advertisers.
func cleanupAddGem(t *testing.T) {
	t.Helper()
	t.Cleanup(func() {
		require.NoError(t, db.DB().Exec(
			`DELETE FROM public.pipeline_jobs WHERE advertiser_id IN (
				SELECT id FROM public.advertisers WHERE page_id = ?)`, addGemPageID).Error)
		require.NoError(t, db.DB().Exec(
			`DELETE FROM public.advertisers WHERE page_id = ?`, addGemPageID).Error)
	})
}

func advertiserCount(t *testing.T, pageID string) int {
	t.Helper()
	var n int
	require.NoError(t, db.DB().Raw(
		`SELECT count(*) FROM public.advertisers WHERE page_id = ?`, pageID).Scan(&n).Error)
	return n
}

func TestAddGemRejectsAnUnparseableLinkAndWritesNothing(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	cleanupAddGem(t)
	r := router()

	w := postAddGem(t, r, "https://example.com/not-ad-library")
	require.Equal(t, http.StatusUnprocessableEntity, w.Code)
	require.Contains(t, w.Body.String(), "Ad Library")
	require.Zero(t, advertiserCount(t, addGemPageID))
}

func TestAddGemRejectsAKeywordSearchLink(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()

	w := postAddGem(t, r,
		"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&q=sleep")
	require.Equal(t, http.StatusUnprocessableEntity, w.Code)
	require.Contains(t, w.Body.String(), "one advertiser")
}

func TestAddGemCreatesOneAdvertiserAndOneQueuedJob(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	cleanupAddGem(t)
	r := router()

	link := "https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&view_all_page_id=" + addGemPageID
	w := postAddGem(t, r, link)
	require.Equal(t, http.StatusAccepted, w.Code)

	first := decodeAddGem(t, w)
	require.NotZero(t, first.JobID)
	require.NotZero(t, first.AdvertiserID)
	require.False(t, first.AlreadyKnown, "a page we have never seen is not already known")
	require.Equal(t, "queued", first.Status)
	require.Equal(t, 1, advertiserCount(t, addGemPageID))

	var kind, status string
	require.NoError(t, db.DB().Raw(
		`SELECT kind, status FROM public.pipeline_jobs WHERE id = ?`, first.JobID).
		Row().Scan(&kind, &status))
	require.Equal(t, "add_page", kind)
	require.Equal(t, "queued", status)

	// A second paste of the same link returns the SAME advertiser and the SAME
	// in-flight job. pipeline_jobs_active_uq makes a duplicate impossible, but
	// the API must also report it rather than erroring.
	w2 := postAddGem(t, r, link)
	require.Equal(t, http.StatusAccepted, w2.Code)
	second := decodeAddGem(t, w2)
	require.Equal(t, first.AdvertiserID, second.AdvertiserID)
	require.Equal(t, first.JobID, second.JobID)
	require.True(t, second.AlreadyKnown, "the advertiser existed by the second paste")

	var jobCount int
	require.NoError(t, db.DB().Raw(
		`SELECT count(*) FROM public.pipeline_jobs WHERE kind = 'add_page' AND advertiser_id = ?`,
		first.AdvertiserID).Scan(&jobCount).Error)
	require.Equal(t, 1, jobCount, "a second paste must not queue a second scrape")

	// A bare page id is accepted too, and resolves to the same advertiser.
	w3 := postAddGem(t, r, addGemPageID)
	require.Equal(t, http.StatusAccepted, w3.Code)
	require.Equal(t, first.AdvertiserID, decodeAddGem(t, w3).AdvertiserID)

	// Status poll reports the job, including the phase once the worker writes one.
	require.NoError(t, db.DB().Exec(
		`UPDATE public.pipeline_jobs
		    SET status = 'running',
		        payload = jsonb_set(payload, '{phase}', to_jsonb('Pulling ads'::text), true)
		  WHERE id = ?`, first.JobID).Error)
	sw, body := get(t, r, "/api/add-gem/"+strconv.FormatInt(first.JobID, 10))
	require.Equal(t, http.StatusOK, sw.Code)
	require.Contains(t, string(body["status"]), "running")
	require.Contains(t, string(body["phase"]), "Pulling ads")
}

func TestAddGemStatus404sForAnUnknownJob(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	w, _ := get(t, r, "/api/add-gem/999999999")
	require.Equal(t, http.StatusNotFound, w.Code)
}
