package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
)

// The per-user endpoints used to take the caller's identity from a
// caller-supplied ?user_email (reads) or body.user_email (writes). Nothing bound
// either to the credential, so any authenticated caller could name somebody else
// and read or write their rows. These tests pin the fix: identity comes from
// KeyAuth via UserFrom, and the param/field is inert.
//
// The write routes are RequireToken()-guarded, which admits ONLY the internal
// token — but that is not a defence. The Next proxy holds that token and
// forwards the browser's request body verbatim (web/src/app/backend/[...path]/
// route.ts), so a signed-in user posting {"user_email": "victim@…"} reached the
// handler with the victim's name in the body and the proxy's trusted
// X-User-Email in the headers. The body must lose.
//
// Every assertion below is written so it FAILS against the param-reading
// handler: each one names a victim who has data the caller does not.

// identityUsers seeds two members, each with their own fd_ key, and registers
// the row cleanup. Cleanup is registered AFTER seedKeyedUser so LIFO runs it
// FIRST — the rows go before the accounts they hang off (app_ratings,
// saved_ads and preference_scores have no FK to users, so nothing else would
// reap them).
func identityUsers(t *testing.T, ctx context.Context) (emailA, keyA, emailB, keyB string) {
	t.Helper()
	emailA, keyA = seedKeyedUser(t, ctx, "member")
	emailB, keyB = seedKeyedUser(t, ctx, "member")
	g := db.DB()
	t.Cleanup(func() {
		for _, tbl := range []string{
			"frontend.app_ratings", "frontend.saved_gems",
			"frontend.saved_ads", "frontend.preference_scores",
		} {
			if err := g.Exec(
				`DELETE FROM `+tbl+` WHERE user_email IN (?, ?)`, emailA, emailB).Error; err != nil {
				t.Errorf("cleanup %s: %v", tbl, err)
				continue
			}
			var left int64
			if err := g.Raw(
				`SELECT count(*) FROM `+tbl+` WHERE user_email IN (?, ?)`,
				emailA, emailB).Scan(&left).Error; err != nil {
				t.Errorf("cleanup check %s: %v", tbl, err)
			} else if left != 0 {
				t.Errorf("%d %s rows leaked into the shared test DB", left, tbl)
			}
		}
	})
	return emailA, keyA, emailB, keyB
}

// TestIdentityScopingReads covers the five GETs that took their identity from
// ?user_email. In each case B owns data and A owns none of it, so A naming B in
// the param must come back with A's (empty, or A-ordered) view.
func TestIdentityScopingReads(t *testing.T) {
	ctx := context.Background()
	g := db.DB()
	emailA, keyA, emailB, keyB := identityUsers(t, ctx)

	r := router()
	get := func(bearer, path string) *httptest.ResponseRecorder {
		t.Helper()
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.Header.Set("Authorization", "Bearer "+bearer)
		r.ServeHTTP(w, req)
		return w
	}
	asB := "user_email=" + url.QueryEscape(emailB)
	asA := "user_email=" + url.QueryEscape(emailA)

	// B's data, in every store the reads below serve from. A gets nothing.
	require.NoError(t, g.Exec(
		`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, comment)
		 VALUES (?, ?, 'like', 'B likes this')`, emailB, advID).Error)
	require.NoError(t, g.Exec(
		`INSERT INTO frontend.saved_gems (user_email, advertiser_id, gem_score, page_name)
		 VALUES (?, ?, 88.5, 'Acme Co')`, emailB, advID).Error)
	savedAdID := fmt.Sprintf("IDENTITY-AD-%d", time.Now().UnixNano())
	require.NoError(t, g.Exec(
		`INSERT INTO frontend.saved_ads (user_email, external_ad_id, page_name)
		 VALUES (?, ?, 'Acme Co')`, emailB, savedAdID).Error)

	t.Run("GET /ratings", func(t *testing.T) {
		// A holds A's key and names B. A has rated nothing, so the only correct
		// body is the empty map; B's 'like' on advID coming back means the param
		// chose the identity.
		w := get(keyA, "/api/ratings?"+asB)
		require.Equal(t, http.StatusOK, w.Code)
		var mine map[string]json.RawMessage
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &mine))
		require.Empty(t, mine, "A has no ratings; anything here came from B")

		// The legitimate path: B on B's own key sees B's rating.
		w = get(keyB, "/api/ratings")
		require.Equal(t, http.StatusOK, w.Code)
		var theirs map[string]json.RawMessage
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &theirs))
		require.Contains(t, theirs, strconv.Itoa(advID))

		// The param is inert, not cross-checked: B pointing it at A still gets B's.
		w = get(keyB, "/api/ratings?"+asA)
		require.Equal(t, http.StatusOK, w.Code)
		theirs = nil
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &theirs))
		require.Contains(t, theirs, strconv.Itoa(advID), "identity comes from the key")
	})

	t.Run("GET /saved-gems", func(t *testing.T) {
		w := get(keyA, "/api/saved-gems?"+asB)
		require.Equal(t, http.StatusOK, w.Code)
		require.Equal(t, "[]", strings.TrimSpace(w.Body.String()),
			"A has saved no gems; B's watchlist must not leak through the param")

		w = get(keyB, "/api/saved-gems")
		require.Equal(t, http.StatusOK, w.Code)
		var rows []map[string]any
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
		require.Len(t, rows, 1)
		require.EqualValues(t, advID, rows[0]["advertiser_id"])
	})

	t.Run("GET /saved-ads", func(t *testing.T) {
		w := get(keyA, "/api/saved-ads?"+asB)
		require.Equal(t, http.StatusOK, w.Code)
		require.Equal(t, "[]", strings.TrimSpace(w.Body.String()),
			"A has saved no ads; B's collection must not leak through the param")

		w = get(keyB, "/api/saved-ads")
		require.Equal(t, http.StatusOK, w.Code)
		var rows []map[string]any
		require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
		require.Len(t, rows, 1)
		require.Equal(t, savedAdID, rows[0]["external_ad_id"])
	})

	t.Run("GET /preference-queue", func(t *testing.T) {
		// The queue never serves an app the user has already rated. B rated advID
		// (above), A rated nothing — so advID is in A's queue and can never be in
		// B's. A naming B must still see it. The limit is the endpoint's ceiling
		// so the per-niche cap and bucket sampling cannot drop advID from a deck
		// far larger than the seeded matview.
		ids := func(w *httptest.ResponseRecorder) map[int]bool {
			t.Helper()
			require.Equal(t, http.StatusOK, w.Code, w.Body.String())
			var rows []struct {
				AdvertiserID int `json:"advertiser_id"`
			}
			require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
			out := map[int]bool{}
			for _, row := range rows {
				out[row.AdvertiserID] = true
			}
			return out
		}
		require.True(t, ids(get(keyA, "/api/preference-queue?limit=5000&"+asB))[advID],
			"A's queue holds advID; B's never can, so naming B must not have worked")
		require.False(t, ids(get(keyB, "/api/preference-queue?limit=5000"))[advID],
			"B rated advID, so B's own queue excludes it")
	})

	t.Run("GET /shortlist sort=foryou", func(t *testing.T) {
		// foryou orders by the caller's preference_scores. Give B a strong score
		// on adv2ID — the seeded gem that sorts LAST for anyone without scores
		// (its active_ad_count is NULL, and the tie-break is active_ad_count DESC
		// NULLS LAST). So "adv2ID first" is B's ordering and nobody else's.
		require.NoError(t, g.Exec(
			`INSERT INTO frontend.preference_scores
			   (user_email, advertiser_id, p_like, uncertainty, queue_rank)
			 VALUES (?, ?, 0.99, 0.02, 1)`, emailB, adv2ID).Error)

		// The matview is shared with sibling packages, so compare only the seeded
		// confirmed gems: their relative order is what identity decides.
		seeded := map[int]bool{advID: true, adv2ID: true, adv3ID: true}
		firstSeeded := func(w *httptest.ResponseRecorder) int {
			t.Helper()
			require.Equal(t, http.StatusOK, w.Code, w.Body.String())
			var rows []struct {
				AdvertiserID int `json:"advertiser_id"`
			}
			require.NoError(t, json.Unmarshal(w.Body.Bytes(), &rows))
			for _, row := range rows {
				if seeded[row.AdvertiserID] {
					return row.AdvertiserID
				}
			}
			t.Fatal("no seeded gem in the feed")
			return 0
		}

		require.Equal(t, adv3ID, firstSeeded(get(keyA, "/api/shortlist?sort=foryou&limit=500&"+asB)),
			"A has no preference_scores, so A's foryou falls back to active_ad_count DESC — "+
				"adv2ID leading would mean the param ranked the feed by B's model")
		require.Equal(t, adv2ID, firstSeeded(get(keyB, "/api/shortlist?sort=foryou&limit=500")),
			"B's own scores put adv2ID on top")
	})
}

// TestIdentityScopingWrites covers the three POST bodies that carried
// user_email. The caller authenticates exactly as the web proxy does — internal
// token plus a trusted X-User-Email — and puts the VICTIM's address in the body,
// which is precisely what a logged-in browser can do through the proxy.
func TestIdentityScopingWrites(t *testing.T) {
	ctx := context.Background()
	g := db.DB()
	// Both middlewares read AGF_API_TOKEN once at construction, so set it first.
	t.Setenv(middlewares.APITokenEnv, writeToken)
	emailA, _, emailB, _ := identityUsers(t, ctx)

	r := router()
	// postAs sends body as the internal token would from the proxy, with `who`
	// as the session identity in X-User-Email.
	postAs := func(who, path, body string) *httptest.ResponseRecorder {
		t.Helper()
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+writeToken)
		req.Header.Set("X-User-Email", who)
		req.Header.Set("Content-Type", "application/json")
		r.ServeHTTP(w, req)
		return w
	}
	count := func(table, where string, args ...any) int64 {
		t.Helper()
		var n int64
		require.NoError(t, g.Raw(`SELECT count(*) FROM `+table+` WHERE `+where, args...).Scan(&n).Error)
		return n
	}
	// bodyNaming builds a JSON object whose user_email is the victim's.
	bodyNaming := func(victim string, extra ...string) string {
		parts := append([]string{fmt.Sprintf("%q:%q", "user_email", victim)}, extra...)
		return "{" + strings.Join(parts, ",") + "}"
	}

	t.Run("POST /ratings", func(t *testing.T) {
		w := postAs(emailA, "/api/ratings/"+strconv.Itoa(advID),
			bodyNaming(emailB, `"rating":"like"`, `"comment":"planted"`))
		require.Equal(t, http.StatusOK, w.Code, w.Body.String())
		require.EqualValues(t, 0,
			count("frontend.app_ratings", "user_email = ? AND advertiser_id = ?", emailB, advID),
			"A rated on B's behalf — the body chose the owner")
		require.EqualValues(t, 1,
			count("frontend.app_ratings", "user_email = ? AND advertiser_id = ?", emailA, advID),
			"the rating belongs to the caller")

		// rating "none" is a DELETE, the more damaging half: B has a rating on
		// adv2ID and A tries to erase it by naming B.
		require.NoError(t, g.Exec(
			`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating)
			 VALUES (?, ?, 'like')`, emailB, adv2ID).Error)
		w = postAs(emailA, "/api/ratings/"+strconv.Itoa(adv2ID), bodyNaming(emailB, `"rating":"none"`))
		require.Equal(t, http.StatusOK, w.Code, w.Body.String())
		require.EqualValues(t, 1,
			count("frontend.app_ratings", "user_email = ? AND advertiser_id = ?", emailB, adv2ID),
			"A must not be able to delete B's rating by naming B in the body")
	})

	t.Run("POST /saved-gems", func(t *testing.T) {
		w := postAs(emailA, "/api/saved-gems/"+strconv.Itoa(adv3ID),
			bodyNaming(emailB, `"gem_score":72`, `"page_name":"planted"`))
		require.Equal(t, http.StatusOK, w.Code, w.Body.String())
		require.EqualValues(t, 0,
			count("frontend.saved_gems", "user_email = ? AND advertiser_id = ?", emailB, adv3ID),
			"A saved onto B's watchlist — the body chose the owner")
		require.EqualValues(t, 1,
			count("frontend.saved_gems", "user_email = ? AND advertiser_id = ?", emailA, adv3ID))

		// The unsave: B's saved gem must survive A naming B.
		require.NoError(t, g.Exec(
			`INSERT INTO frontend.saved_gems (user_email, advertiser_id, gem_score)
			 VALUES (?, ?, 88.5)`, emailB, advID).Error)
		w = postAs(emailA, "/api/saved-gems/"+strconv.Itoa(advID)+"/remove", bodyNaming(emailB))
		require.Equal(t, http.StatusOK, w.Code, w.Body.String())
		require.EqualValues(t, 1,
			count("frontend.saved_gems", "user_email = ? AND advertiser_id = ?", emailB, advID),
			"A must not be able to unsave B's gem by naming B in the body")
	})

	t.Run("POST /saved-ads", func(t *testing.T) {
		planted := fmt.Sprintf("IDENTITY-W-%d", time.Now().UnixNano())
		w := postAs(emailA, "/api/saved-ads/"+planted, bodyNaming(emailB, `"page_name":"planted"`))
		require.Equal(t, http.StatusOK, w.Code, w.Body.String())
		require.EqualValues(t, 0,
			count("frontend.saved_ads", "user_email = ? AND external_ad_id = ?", emailB, planted),
			"A saved onto B's collection — the body chose the owner")
		require.EqualValues(t, 1,
			count("frontend.saved_ads", "user_email = ? AND external_ad_id = ?", emailA, planted))

		// The unsave: B's saved ad must survive A naming B.
		victim := fmt.Sprintf("IDENTITY-V-%d", time.Now().UnixNano())
		require.NoError(t, g.Exec(
			`INSERT INTO frontend.saved_ads (user_email, external_ad_id) VALUES (?, ?)`,
			emailB, victim).Error)
		w = postAs(emailA, "/api/saved-ads/"+victim+"/remove", bodyNaming(emailB))
		require.Equal(t, http.StatusOK, w.Code, w.Body.String())
		require.EqualValues(t, 1,
			count("frontend.saved_ads", "user_email = ? AND external_ad_id = ?", emailB, victim),
			"A must not be able to unsave B's ad by naming B in the body")
	})

	// A body that omits user_email entirely must now succeed: the field carries
	// no meaning, so requiring it would 422 a correct request. This is the half
	// of the contract that dropping `binding:"required"` buys.
	t.Run("body without user_email is accepted", func(t *testing.T) {
		w := postAs(emailA, "/api/ratings/"+strconv.Itoa(adv3ID), `{"rating":"neutral"}`)
		require.Equal(t, http.StatusOK, w.Code, w.Body.String())
		require.EqualValues(t, 1,
			count("frontend.app_ratings", "user_email = ? AND advertiser_id = ?", emailA, adv3ID))
	})
}
