package repositories

import (
	"math/rand"
	"testing"

	"github.com/stretchr/testify/require"
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
	// fresh target = 15% of 20 = 3; allow tight slack for rounding/interleave.
	require.GreaterOrEqual(t, nFresh, 2)
	require.LessOrEqual(t, nFresh, 4)
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
	// Starve the scored buckets so the primary per-bucket targets cannot be met.
	// With limit 10 the targets are ~exploit 6 / uncertain 3 / fresh 1, but only
	// 2 scored candidates exist: exploit and uncertain exhaust early and the
	// primary round-robin stops at 3 picks (2 scored + 1 fresh, fresh capped at
	// its target of 1). Only the secondary underfill pass — which ignores the
	// per-bucket targets — can pull the remaining fresh candidates to fill the
	// limit. Each candidate is its own niche, so the per-niche cap never bites.
	var cands []prefCandidate
	cands = append(cands, cand(1, "scoredA", 0.9, 0.1, "scored"))
	cands = append(cands, cand(2, "scoredB", 0.8, 0.2, "scored"))
	for i := 0; i < 8; i++ {
		cands = append(cands, cand(100+i, string(rune('a'+i)), 0.5, 1.0, "fresh"))
	}
	out := assembleQueue(cands, baseOpts(10, 5))
	require.Len(t, out, 10, "underfill pass must fill the limit from leftover fresh candidates")
	// All 10 distinct advertisers surface; primary alone would stop at 3.
	seen := map[int]bool{}
	for _, c := range out {
		seen[c.AdvertiserID] = true
	}
	require.Len(t, seen, 10, "all distinct advertisers must be present")
}

func TestNicheKey_FallbackAndExempt(t *testing.T) {
	str := func(s string) *string { return &s }
	cases := []struct {
		name   string
		niche  *string
		cohort string
		want   string
	}{
		{"niche set, cohort set -> niche key", str("yoga"), "wellness", "n:yoga"},
		{"niche nil -> cohort fallback", nil, "wellness", "c:wellness"},
		{"niche empty -> cohort fallback", str(""), "wellness", "c:wellness"},
		{"niche nil, cohort empty -> exempt", nil, "", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var c prefCandidate
			c.NicheKeyword = tc.niche
			c.Cohort = tc.cohort
			require.Equal(t, tc.want, nicheKey(c))
		})
	}
}

func TestWeightedSampleByPLike_PermutationAndWeighting(t *testing.T) {
	// Permutation: output is the same set, same length.
	var in []prefCandidate
	for i := 0; i < 50; i++ {
		in = append(in, cand(i+1, "n", 0.2+float64(i)*0.01, 0.5, "scored"))
	}
	out := weightedSampleByPLike(in, rand.New(rand.NewSource(1)))
	require.Len(t, out, len(in))
	inSet, outSet := map[int]bool{}, map[int]bool{}
	for _, c := range in {
		inSet[c.AdvertiserID] = true
	}
	for _, c := range out {
		outSet[c.AdvertiserID] = true
	}
	require.Equal(t, inSet, outSet, "sample must be a permutation (no drops/dupes)")

	// Variety: two seeds give different orderings.
	a := ids(weightedSampleByPLike(in, rand.New(rand.NewSource(1))))
	b := ids(weightedSampleByPLike(in, rand.New(rand.NewSource(2))))
	require.NotEqual(t, a, b)

	// Weighting: averaged over many requests, the first card's p_like exceeds the
	// pool mean — sampling is biased toward higher p_like, not uniform.
	var poolMean float64
	for _, c := range in {
		poolMean += c.PLike
	}
	poolMean /= float64(len(in))
	var sumFirst float64
	const trials = 200
	for s := int64(0); s < trials; s++ {
		got := weightedSampleByPLike(in, rand.New(rand.NewSource(s)))
		sumFirst += got[0].PLike
	}
	require.Greater(t, sumFirst/trials, poolMean,
		"first card should average above the pool mean (weighting), mean=%.3f", poolMean)
}

func TestAssembleQueue_ExploitSamplesTopWindowAndVaries(t *testing.T) {
	// 100 scored apps, id i has p_like 1.0 - i*0.009 (distinct), unique niche.
	var cands []prefCandidate
	for i := 0; i < 100; i++ {
		cands = append(cands, cand(i, "niche"+string(rune('A'+i%26))+string(rune('a'+i/26)), 1.0-float64(i)*0.009, 0.5, "scored"))
	}
	opts := func(seed int64) queueOpts {
		return queueOpts{Limit: 10, PerNicheCap: defaultPerNicheCap, ExploitFrac: 1.0, UncertainFrac: 0.0, Rng: rand.New(rand.NewSource(seed))}
	}
	out1 := assembleQueue(cands, opts(1))
	require.Len(t, out1, 10)
	for _, c := range out1 {
		require.Less(t, c.AdvertiserID, exploitSampleWindow,
			"every taste card must come from the top-%d p_like window", exploitSampleWindow)
	}
	// Variety: a different request returns a different set of top cards.
	out2 := assembleQueue(cands, opts(2))
	require.NotEqual(t, ids(out1), ids(out2), "consecutive requests should vary the taste deck")
}
