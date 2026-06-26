package repositories

import (
	"math/rand"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

func cand(id int, niche string, pLike, unc float64, source string) prefCandidate {
	n := niche
	c := prefCandidate{Uncertainty: unc, Source: source}
	c.AdvertiserID = id
	c.NicheKeyword = &n
	c.PLike = pLike
	return c
}

func ids(cs []prefCandidate) []int {
	out := make([]int, len(cs))
	for i, c := range cs {
		out[i] = c.AdvertiserID
	}
	return out
}

func baseOpts(limit int, seed int64) queueOpts {
	return queueOpts{
		Limit:         limit,
		PerNicheCap:   defaultPerNicheCap,
		ExploitFrac:   exploitFrac,
		UncertainFrac: uncertainFrac,
		Rng:           rand.New(rand.NewSource(seed)),
	}
}

func TestAssembleQueue_ExcludesNothingAddedAndDedupesAdvertisers(t *testing.T) {
	cands := []prefCandidate{
		cand(1, "a", 0.9, 0.2, "scored"),
		cand(2, "b", 0.8, 0.3, "scored"),
		cand(3, "c", 0.5, 0.9, "scored"),
		cand(4, "d", 0.5, 1.0, "fresh"),
	}
	out := assembleQueue(cands, baseOpts(10, 1))
	seen := map[int]bool{}
	for _, c := range out {
		require.False(t, seen[c.AdvertiserID], "advertiser %d repeated", c.AdvertiserID)
		seen[c.AdvertiserID] = true
	}
	require.LessOrEqual(t, len(out), 4)
}

func TestAssembleQueue_PerNicheCap(t *testing.T) {
	var cands []prefCandidate
	for i := 0; i < 20; i++ {
		cands = append(cands, cand(i+1, "samenicheword", 0.9-float64(i)*0.01, 0.5, "scored"))
	}
	out := assembleQueue(cands, baseOpts(20, 7))
	require.LessOrEqual(t, len(out), defaultPerNicheCap,
		"a single niche must not exceed the cap")
}

func TestAssembleQueue_FreshReachable(t *testing.T) {
	var cands []prefCandidate
	// 30 scored exploit-y apps across many niches...
	for i := 0; i < 30; i++ {
		cands = append(cands, cand(i+1, string(rune('a'+i%10)), 0.95, 0.1, "scored"))
	}
	// ...plus one fresh app in its own niche.
	cands = append(cands, cand(999, "freshniche", 0.5, 1.0, "fresh"))
	out := assembleQueue(cands, baseOpts(20, 3))
	require.Contains(t, ids(out), 999, "fresh app must be reachable in the blend")
}

func TestAssembleQueue_RatioMix(t *testing.T) {
	var cands []prefCandidate
	// 100 high-p_like scored (exploit), each its own niche
	for i := 0; i < 100; i++ {
		cands = append(cands, cand(1000+i, string(rune('A'))+string(rune(i)), 0.9, 0.1, "scored"))
	}
	// 100 high-uncertainty scored, each its own niche
	for i := 0; i < 100; i++ {
		cands = append(cands, cand(2000+i, string(rune('B'))+string(rune(i)), 0.5, 0.99, "scored"))
	}
	// 100 fresh, each its own niche
	for i := 0; i < 100; i++ {
		cands = append(cands, cand(3000+i, string(rune('C'))+string(rune(i)), 0.5, 1.0, "fresh"))
	}
	out := assembleQueue(cands, baseOpts(20, 11))
	require.Len(t, out, 20)
	var nFresh int
	for _, c := range out {
		if c.Source == "fresh" {
			nFresh++
		}
	}
	// fresh target ≈ 15% of 20 = 3; allow slack for rounding/interleave.
	require.GreaterOrEqual(t, nFresh, 1)
	require.LessOrEqual(t, nFresh, 6)
}

func TestAssembleQueue_VariesAcrossRequests(t *testing.T) {
	var cands []prefCandidate
	for i := 0; i < 60; i++ {
		cands = append(cands, cand(i+1, string(rune('a'+i%15)), 0.6, 0.6, "scored"))
	}
	a := ids(assembleQueue(cands, baseOpts(20, 1)))
	b := ids(assembleQueue(cands, baseOpts(20, 2)))
	require.NotEqual(t, a, b, "different rng seeds must yield different batches")
}

func TestAssembleQueue_UnderfillRedistributes(t *testing.T) {
	// Only exploit candidates; uncertain+fresh empty. Must still fill toward limit.
	var cands []prefCandidate
	for i := 0; i < 10; i++ {
		cands = append(cands, cand(i+1, string(rune('a'+i)), 0.9-float64(i)*0.05, 0.1, "scored"))
	}
	out := assembleQueue(cands, baseOpts(8, 5))
	require.Len(t, out, 8)
}

var _ = models.ShortlistRow{} // keep import if unused elsewhere
