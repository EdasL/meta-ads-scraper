package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// orphanAdvertiserID is an advertiser that exists in nobody's tables — no
// advertisers row, no scores, not in the matview. It stands in for the real case
// the Liked collection has to survive: an app that was liked and later dropped
// out of the matview (vetoed, no longer emerging). frontend.app_ratings has no
// FK on advertiser_id, so the rating outlives the app.
const orphanAdvertiserID int64 = 999000001

// TestLikedGemsRepo covers the built-in Liked collection: likes with and without
// a saved row, the saved flag, rated_at ordering, non-like ratings staying out,
// per-user scoping, and the features fallback for gem_score when no live score
// can be joined. The shared test DB is reused by other packages, so everything
// is scoped to per-run-unique emails and cleaned up on the way out.
func TestLikedGemsRepo(t *testing.T) {
	ctx := context.Background()
	g := db.DB()

	uniq := time.Now().UnixNano()
	me := fmt.Sprintf("liked-%d@test.local", uniq)
	other := fmt.Sprintf("liked-other-%d@test.local", uniq)

	t.Cleanup(func() {
		if err := g.Exec(`DELETE FROM frontend.app_ratings WHERE user_email IN (?, ?)`, me, other).Error; err != nil {
			t.Errorf("cleanup app_ratings: %v", err)
		}
		if err := g.Exec(`DELETE FROM frontend.saved_gems WHERE user_email IN (?, ?)`, me, other).Error; err != nil {
			t.Errorf("cleanup saved_gems: %v", err)
		}
	})

	// A user who has liked nothing gets an empty list, never null.
	empty, err := repositories.ListLikedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if empty == nil || len(empty) != 0 {
		t.Fatalf("ListLikedGems for a user with no ratings = %+v, want an empty non-nil slice", empty)
	}

	// rated_at is set explicitly so the ordering assertion is not a race with
	// three now() values landing in the same microsecond.
	like := func(advertiserID int64, rating string, ratedAt string, features string) {
		t.Helper()
		if err := g.Exec(
			`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating, features, rated_at)
			 VALUES (?, ?, ?, ?::jsonb, ?::timestamptz)`,
			me, advertiserID, rating, features, ratedAt,
		).Error; err != nil {
			t.Fatal(err)
		}
	}
	like(int64(advID), "like", "2026-08-01T10:00:00Z", `{}`)
	like(int64(adv2ID), "like", "2026-08-02T10:00:00Z", `{}`)
	// Liked, never saved, and no longer joinable anywhere — only the frozen
	// features snapshot can supply a score.
	like(orphanAdvertiserID, "like", "2026-08-03T10:00:00Z", `{"gem_score": 61.5}`)
	// A thumbs-down is a rating too, and must not show up in Liked.
	like(int64(adv3ID), "dislike", "2026-08-04T10:00:00Z", `{}`)

	// Only the first liked app is also saved: the saved flag has to tell the two
	// apart so the web can decide whether to render notes/tags/folders.
	if err := repositories.UpsertSavedGem(ctx, g, models.SavedGemIn{
		UserEmail: me,
		Cohort:    "ios",
		GemScore:  88.5,
	}, advID); err != nil {
		t.Fatal(err)
	}

	// Another user's like must not leak into this list.
	if err := g.Exec(
		`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating) VALUES (?, ?, 'like')`,
		other, advID,
	).Error; err != nil {
		t.Fatal(err)
	}

	rows, err := repositories.ListLikedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 3 {
		t.Fatalf("ListLikedGems returned %d rows, want 3 (three likes; the dislike excluded): %+v", len(rows), rows)
	}

	// --- ordering: newest like first ---
	wantOrder := []int64{orphanAdvertiserID, int64(adv2ID), int64(advID)}
	for i, want := range wantOrder {
		if rows[i].AdvertiserID != want {
			t.Fatalf("row[%d] advertiser_id = %d, want %d (rated_at DESC)", i, rows[i].AdvertiserID, want)
		}
	}
	for i := 1; i < len(rows); i++ {
		if rows[i-1].RatedAt == nil || rows[i].RatedAt == nil {
			t.Fatalf("rated_at is null on row %d or %d", i-1, i)
		}
		if time.Time(*rows[i-1].RatedAt).Before(time.Time(*rows[i].RatedAt)) {
			t.Fatalf("rows are not rated_at DESC: %v then %v", *rows[i-1].RatedAt, *rows[i].RatedAt)
		}
	}

	// --- the saved flag ---
	saved := rows[2] // advID, the one with a saved_gems row
	if !saved.Saved {
		t.Fatalf("liked app %d has a saved_gems row but saved = false", saved.AdvertiserID)
	}
	if saved.PageName == nil || *saved.PageName != "Acme Co" {
		t.Fatalf("page_name = %v, want Acme Co (from public.advertisers)", saved.PageName)
	}
	if saved.GemScore == nil || *saved.GemScore != 88.5 {
		t.Fatalf("gem_score = %v, want 88.5 (live adtech.advertiser_scores)", saved.GemScore)
	}

	unsaved := rows[1] // adv2ID, liked but never saved
	if unsaved.Saved {
		t.Fatalf("liked app %d was never saved but saved = true", unsaved.AdvertiserID)
	}
	if unsaved.GemScore == nil || *unsaved.GemScore != 75 {
		t.Fatalf("unsaved like gem_score = %v, want 75", unsaved.GemScore)
	}
	// PAGE2 is seeded with a null page_name — a missing name must not drop the row.
	if unsaved.PageName != nil {
		t.Fatalf("page_name = %v, want nil for the seeded null-name advertiser", *unsaved.PageName)
	}

	// --- the orphan: every join misses, the row still renders ---
	orphan := rows[0]
	if orphan.Saved {
		t.Fatalf("orphan like saved = true, want false")
	}
	if orphan.PageName != nil || orphan.AppName != nil || orphan.IconURL != nil {
		t.Fatalf("orphan row should have null name/icon, got %+v", orphan)
	}
	if orphan.GemScore == nil || *orphan.GemScore != 61.5 {
		t.Fatalf("orphan gem_score = %v, want 61.5 from the features snapshot", orphan.GemScore)
	}

	// --- scoping: the other user sees only their own like ---
	theirs, err := repositories.ListLikedGems(ctx, g, other)
	if err != nil {
		t.Fatal(err)
	}
	if len(theirs) != 1 || theirs[0].AdvertiserID != int64(advID) {
		t.Fatalf("the other user's Liked = %+v, want exactly their own like on %d", theirs, advID)
	}
}

// TestLikedGemsEndpoint drives GET /api/liked-gems through the real router. It
// doubles as the raw-key wire test: the literal JSON keys are read off an actual
// response, so it pins the DB → Go → JSON path rather than a hand-built struct.
// Identity comes from the presented key, never a param.
func TestLikedGemsEndpoint(t *testing.T) {
	g := db.DB()
	t.Cleanup(func() {
		if err := g.Exec(`DELETE FROM frontend.app_ratings WHERE user_email = ?`, authedEmail).Error; err != nil {
			t.Errorf("cleanup app_ratings: %v", err)
		}
	})
	if err := g.Exec(
		`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating) VALUES (?, ?, 'like')
		 ON CONFLICT (user_email, advertiser_id) DO UPDATE SET rating = 'like'`,
		authedEmail, advID,
	).Error; err != nil {
		t.Fatal(err)
	}

	w := httptest.NewRecorder()
	router().ServeHTTP(w, authed(httptest.NewRequest(http.MethodGet, "/api/liked-gems", nil)))
	if w.Code != http.StatusOK {
		t.Fatalf("GET /api/liked-gems = %d, want 200: %s", w.Code, w.Body.String())
	}

	var raw []map[string]json.RawMessage
	if err := json.Unmarshal(w.Body.Bytes(), &raw); err != nil {
		t.Fatalf("response is not a JSON array: %v (%s)", err, w.Body.String())
	}
	if len(raw) == 0 {
		t.Fatalf("no liked rows returned for the authed key: %s", w.Body.String())
	}
	// Field renames in Go are invisible to TypeScript, so the keys are literal.
	for _, k := range []string{
		"advertiser_id", "page_name", "app_name", "icon_url",
		"gem_score", "rated_at", "saved",
	} {
		if _, ok := raw[0][k]; !ok {
			t.Errorf("key %q missing from the liked row: %s", k, w.Body.String())
		}
	}
	// saved is a JSON boolean, not a string — the web renders on it directly.
	if s := string(raw[0]["saved"]); s != "true" && s != "false" {
		t.Errorf("saved = %s, want a JSON boolean", s)
	}
	if string(raw[0]["rated_at"]) == "null" {
		t.Errorf("rated_at is null on a row that exists: %s", w.Body.String())
	}

	// An unauthenticated caller gets 401, never somebody else's likes.
	w = httptest.NewRecorder()
	router().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/liked-gems", nil))
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("keyless GET /api/liked-gems = %d, want 401", w.Code)
	}
}
