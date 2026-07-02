package integration

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// TestMemberRepo exercises the personal read paths the MCP serves: a member's
// own ratings, saved gems, and derived taste profile. The shared test DB is
// reseeded once per binary and used by other tests, so every assertion is
// scoped to freshly-minted, per-run-unique user emails — never a global count.
func TestMemberRepo(t *testing.T) {
	ctx := context.Background()
	g := db.DB()

	// Unique emails so re-runs (rows persist) and other tests never collide.
	uniq := time.Now().UnixNano()
	me := fmt.Sprintf("member-%d@test.local", uniq)
	other := fmt.Sprintf("other-%d@test.local", uniq)

	// Two likes on matview-backed advertisers (advID → Acme App/ios,
	// adv3ID → Walk Tracker/Health & Fitness×US) carrying a numeric feature
	// snapshot, plus one dislike. rated_at is staggered so DESC order is fixed.
	if err := g.Exec(`INSERT INTO frontend.app_ratings
		(user_email, advertiser_id, rating, comment, features, rated_at) VALUES
		(?, ?, 'like',    'love it', '{"demand":0.9,"has_funnel":1}'::jsonb, now()),
		(?, ?, 'like',    '',        '{"demand":0.9,"has_funnel":1}'::jsonb, now() - interval '1 minute'),
		(?, ?, 'dislike', 'meh',     '{"demand":0.2}'::jsonb,                now() - interval '2 minutes')`,
		me, advID, me, adv3ID, me, adv4ID).Error; err != nil {
		t.Fatal(err)
	}
	// One saved gem (FK → advertisers, so use a seeded advertiser).
	if err := g.Exec(`INSERT INTO frontend.saved_gems
		(user_email, advertiser_id, page_name, app_name, cohort, gem_score, tags, note, stage, verdict, confidence)
		VALUES (?, ?, 'Acme Co', 'Acme App', 'ios', 88.5, '["fintech"]', 'watch this', 'watching', 'build', 4)`,
		me, advID).Error; err != nil {
		t.Fatal(err)
	}

	// --- MyRatings: all three, newest first, matview join fills app/cohort ---
	all, total, err := repositories.MyRatings(ctx, g, me, "", 100, 0)
	if err != nil {
		t.Fatal(err)
	}
	if total != 3 || len(all) != 3 {
		t.Fatalf("MyRatings all: got %d rows total=%d, want 3/3", len(all), total)
	}
	if all[0].AdvertiserID != int64(advID) || all[0].Rating != "like" ||
		all[0].AppName != "Acme App" || all[0].Cohort != "ios" || all[0].Comment != "love it" {
		t.Fatalf("MyRatings[0] = %+v", all[0])
	}
	if all[1].AppName != "Walk Tracker" || all[1].Cohort != "Health & Fitness×US" {
		t.Fatalf("MyRatings[1] join = %+v", all[1])
	}

	// --- MyRatings: filter by rating + honor limit; total is the pre-limit count ---
	likes, likeTotal, err := repositories.MyRatings(ctx, g, me, "like", 1, 0)
	if err != nil {
		t.Fatal(err)
	}
	if likeTotal != 2 {
		t.Fatalf("MyRatings like total = %d, want 2 (count before limit)", likeTotal)
	}
	if len(likes) != 1 {
		t.Fatalf("MyRatings like limit=1 returned %d rows, want 1", len(likes))
	}
	if likes[0].Rating != "like" {
		t.Fatalf("filtered row not a like: %+v", likes[0])
	}

	// --- MySavedGems ---
	saved, err := repositories.MySavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(saved) != 1 {
		t.Fatalf("MySavedGems returned %d rows, want 1", len(saved))
	}
	s := saved[0]
	if s.AdvertiserID != int64(advID) || s.AppName != "Acme App" || s.Cohort != "ios" ||
		s.GemScore != 88.5 || s.Stage != "watching" || s.Tags != `["fintech"]` || s.Note != "watch this" {
		t.Fatalf("MySavedGems[0] = %+v", s)
	}
	if s.Verdict == nil || *s.Verdict != "build" {
		t.Fatalf("MySavedGems verdict = %v, want build", s.Verdict)
	}

	// --- MyTasteProfile ---
	tp, err := repositories.MyTasteProfile(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if tp.Counts["like"] != 2 || tp.Counts["dislike"] != 1 {
		t.Fatalf("taste counts = %+v, want like:2 dislike:1", tp.Counts)
	}
	// demand is the marquee like signal: both likes carry 0.9.
	var demand *repositories.SignalAvg
	for i := range tp.LikeSignals {
		if tp.LikeSignals[i].Signal == "demand" {
			demand = &tp.LikeSignals[i]
		}
	}
	if demand == nil {
		t.Fatalf("LikeSignals missing demand: %+v", tp.LikeSignals)
	}
	if demand.LikeAvg < 0.89 || demand.LikeAvg > 0.91 {
		t.Fatalf("demand LikeAvg = %v, want ~0.9", demand.LikeAvg)
	}
	// Top liked cohorts derive from the matview join.
	if len(tp.TopLikedCohorts) == 0 {
		t.Fatalf("TopLikedCohorts empty, want the two liked cohorts")
	}

	// --- Scoping: a different user sees nothing ---
	oRatings, oTotal, err := repositories.MyRatings(ctx, g, other, "", 100, 0)
	if err != nil {
		t.Fatal(err)
	}
	if oTotal != 0 || len(oRatings) != 0 {
		t.Fatalf("other user MyRatings not empty: %d/%d", len(oRatings), oTotal)
	}
	oSaved, err := repositories.MySavedGems(ctx, g, other)
	if err != nil {
		t.Fatal(err)
	}
	if len(oSaved) != 0 {
		t.Fatalf("other user MySavedGems not empty: %d", len(oSaved))
	}
	oTaste, err := repositories.MyTasteProfile(ctx, g, other)
	if err != nil {
		t.Fatal(err)
	}
	if len(oTaste.Counts) != 0 || len(oTaste.LikeSignals) != 0 {
		t.Fatalf("other user taste profile not empty: %+v", oTaste)
	}
}
