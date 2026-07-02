package integration

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/internal/reco"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// oneHot768 builds a 768-dim Postgres REAL[] literal that is 1.0 at idx and 0
// elsewhere. app_embeddings valid rows are dim=768 and the ranking SQL filters
// on that, so tests must seed real-width vectors; orthogonal one-hot vectors
// give deterministic cosines (identical → 1, different index → 0).
func oneHot768(idx int) string {
	parts := make([]string, 768)
	for i := range parts {
		if i == idx {
			parts[i] = "1"
		} else {
			parts[i] = "0"
		}
	}
	return "{" + strings.Join(parts, ",") + "}"
}

// keepSeededItems filters ranked reco items down to this package's seeded gems,
// preserving order. RecommendedGems ranks the WHOLE product matview, into which
// the sibling repositories-package fixtures (DEDUP_TEST / PREF_TEST /
// RATINGS_TEST cohorts) insert their own advertisers before refreshing it, so
// the full-suite feed carries foreign rows. Scoping to our own ids keeps the
// ranking assertions (similar-app-first, unscored flag, GemScore fallback)
// hermetic without serializing every package. Mirrors keepSeeded in
// shortlist_filters_test.go.
func keepSeededItems(items []reco.RecoItem) []reco.RecoItem {
	s := seededSet()
	out := make([]reco.RecoItem, 0, len(items))
	for i := range items {
		if s[int(items[i].AdvertiserID)] {
			out = append(out, items[i])
		}
	}
	return out
}

// TestRecommendedGems exercises the centroid-cosine ranking end to end against
// the seeded product feed. A user who likes an app with a stored embedding gets
// a personalized ranking (that app first, an embedding-less feed row flagged
// unscored); a user with no likes gets the plain GemScore order.
func TestRecommendedGems(t *testing.T) {
	ctx := context.Background()
	g := db.DB()

	uniq := time.Now().UnixNano()
	me := fmt.Sprintf("reco-%d@test.local", uniq)
	noLikes := fmt.Sprintf("reco-nolikes-%d@test.local", uniq)

	// Two feed advertisers get 768-dim embeddings on orthogonal axes: advID on
	// axis 0, adv3ID on axis 1. The rest of the feed (adv2ID, adv4ID) has none.
	if err := g.Exec(`INSERT INTO frontend.app_embeddings (advertiser_id, embedding, content_hash, dim)
		VALUES (?, ?::real[], ?, 768), (?, ?::real[], ?, 768)
		ON CONFLICT (advertiser_id) DO UPDATE SET embedding = EXCLUDED.embedding, dim = EXCLUDED.dim`,
		advID, oneHot768(0), "h-advid", adv3ID, oneHot768(1), "h-adv3id").Error; err != nil {
		t.Fatal(err)
	}
	// me likes advID (axis-0 app) → centroid is the axis-0 direction.
	if err := g.Exec(`INSERT INTO frontend.app_ratings (user_email, advertiser_id, rating)
		VALUES (?, ?, 'like')`, me, advID).Error; err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		g.Exec(`DELETE FROM frontend.app_embeddings WHERE advertiser_id IN (?, ?)`, advID, adv3ID)
		g.Exec(`DELETE FROM frontend.app_ratings WHERE user_email IN (?, ?)`, me, noLikes)
	})

	// --- personalized: liked app first, cosine-scored, embedding-less flagged ---
	// A high limit avoids the ranking being truncated by foreign matview rows
	// before our seeded gems; keepSeededItems then scopes the assertions.
	allItems, personalized, err := repositories.RecommendedGems(ctx, g, me, 100000)
	if err != nil {
		t.Fatal(err)
	}
	items := keepSeededItems(allItems)
	if !personalized {
		t.Fatalf("personalized = false, want true (user liked an embedded app)")
	}
	if len(items) == 0 || items[0].AdvertiserID != int64(advID) {
		t.Fatalf("items[0] = %+v, want advID (%d) first with cosine ~1", items, advID)
	}
	if items[0].Similarity == nil || *items[0].Similarity < 0.99 {
		t.Fatalf("advID similarity = %v, want ~1", items[0].Similarity)
	}

	var adv3, unscored *reco.RecoItem
	for i := range items {
		switch items[i].AdvertiserID {
		case int64(adv3ID):
			adv3 = &items[i]
		case int64(adv2ID), int64(adv4ID):
			if items[i].Unscored {
				unscored = &items[i]
			}
		}
	}
	if adv3 == nil || adv3.Unscored || adv3.Similarity == nil {
		t.Fatalf("adv3ID row = %+v, want embedded (Unscored=false, Similarity set)", adv3)
	}
	if unscored == nil || unscored.Similarity != nil {
		t.Fatalf("expected an embedding-less feed row flagged Unscored with nil Similarity, got %+v", unscored)
	}

	// --- not personalized: no likes → GemScore order, nothing scored ---
	allPlain, personalized2, err := repositories.RecommendedGems(ctx, g, noLikes, 100000)
	if err != nil {
		t.Fatal(err)
	}
	plain := keepSeededItems(allPlain)
	if personalized2 {
		t.Fatalf("personalized = true for a user with no likes, want false")
	}
	if len(plain) == 0 || plain[0].AdvertiserID != int64(advID) {
		t.Fatalf("plain[0] = %+v, want advID first (highest GemScore 88.5)", plain)
	}
	for i := 1; i < len(plain); i++ {
		if plain[i-1].GemScore < plain[i].GemScore {
			t.Fatalf("plain feed not in GemScore DESC order at %d: %v", i, plain)
		}
	}
}
