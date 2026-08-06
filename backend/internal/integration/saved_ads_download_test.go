package integration

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/storage"
)

// TestSavedAdsDownloadIsScopedToTheCaller pins the identity source of GET
// /api/saved-ads/download. The endpoint streams the whole of a user's saved
// creative collection, so whoever chooses the email chooses the victim: reading
// it from ?user_email let any valid fd_ key download anybody's saves.
//
// The load-bearing assertion is the first one. User A, holding A's own key,
// asks for user B's email and must come back empty — A has no saved ads, so
// the only correct answer is the 404. If the handler ever honours the param
// again, A receives B's zip and this test fails.
func TestSavedAdsDownloadIsScopedToTheCaller(t *testing.T) {
	ctx := context.Background()
	g := db.DB()

	// The zip assembly fetches every creative from the object store, and no
	// integration test configures one (storage.Init runs in main.go), which
	// leaves the backend nil and panics inside the handler. Point it at a closed
	// port instead: every fetch fails, writeSavedAdsZip skips the entry — the
	// documented behaviour for an unreadable object — and the response headers
	// this test asserts on are already out by then.
	require.NoError(t, storage.Init(ctx, "http://127.0.0.1:9", "k", "s", "us-east-1", "it-bucket"))

	// Two members, each holding their own personal fd_ key.
	emailA, keyA := seedKeyedUser(t, ctx, "member")
	emailB, keyB := seedKeyedUser(t, ctx, "member")

	// B saves one ad with a stored creative; A saves nothing. media_s3_key lives
	// inline on frontend.saved_ads (the save freezes a render snapshot), so this
	// single row is the entire download payload — no join to public.creatives.
	adID := fmt.Sprintf("SAVEDAD-%d", time.Now().UnixNano())
	require.NoError(t, g.Exec(
		`INSERT INTO frontend.saved_ads
		   (user_email, external_ad_id, page_name, media_type, media_s3_key)
		 VALUES (?, ?, 'Bravo Co', 'image', ?)`,
		emailB, adID, "creatives/"+strings.Repeat("b", 64)+".jpg",
	).Error)
	// Registered after seedKeyedUser, so LIFO runs it FIRST: the saves are
	// cleared before the accounts they belong to.
	t.Cleanup(func() {
		if err := g.Exec(`DELETE FROM frontend.saved_ads WHERE user_email IN (?, ?)`, emailA, emailB).Error; err != nil {
			t.Errorf("cleanup saved_ads: %v", err)
		}
		var left int64
		if err := g.Raw(`SELECT count(*) FROM frontend.saved_ads WHERE user_email IN (?, ?)`,
			emailA, emailB).Scan(&left).Error; err != nil {
			t.Errorf("cleanup check: %v", err)
		}
		if left != 0 {
			t.Errorf("%d saved_ads rows leaked into the shared test DB", left)
		}
	})

	r := router()
	download := func(bearer, query string) *httptest.ResponseRecorder {
		t.Helper()
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/api/saved-ads/download"+query, nil)
		if bearer != "" {
			req.Header.Set("Authorization", "Bearer "+bearer)
		}
		r.ServeHTTP(w, req)
		return w
	}
	asB := "?user_email=" + url.QueryEscape(emailB)

	// --- the IDOR: A asks for B's collection ---
	w := download(keyA, asB)
	require.Equal(t, http.StatusNotFound, w.Code,
		"A has no saved ads, so anything but the 404 means ?user_email picked the identity")
	require.Contains(t, w.Body.String(), "no saved ad creatives to download.")
	require.NotEqual(t, "application/zip", w.Header().Get("Content-Type"),
		"A must never receive an archive built from B's saves")

	// --- the positive path: B, on B's own key, gets the archive ---
	w = download(keyB, "")
	require.Equal(t, http.StatusOK, w.Code, w.Body.String())
	require.Equal(t, "application/zip", w.Header().Get("Content-Type"))
	require.Equal(t, `attachment; filename="saved-ads.zip"`, w.Header().Get("Content-Disposition"))

	// The param is inert, not cross-checked: B pointing it at A still gets B's
	// own archive. The web client sends it and the handler ignores it.
	w = download(keyB, "?user_email="+url.QueryEscape(emailA))
	require.Equal(t, http.StatusOK, w.Code, "identity comes from the key; the param changes nothing")
	require.Equal(t, "application/zip", w.Header().Get("Content-Type"))

	// No credential at all is rejected by the group's KeyAuth, so the handler's
	// own 401 is a backstop rather than the only gate.
	w = download("", asB)
	require.Equal(t, http.StatusUnauthorized, w.Code)
}
