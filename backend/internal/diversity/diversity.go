// Package diversity holds the pure math behind the scraping-health idea
// diversity metric: k-means clustering of listing embeddings, effective concept
// count, cluster concentration, and a novelty verdict. It is a 1:1 port of the
// numeric helpers in src/agf_mcp/metrics.py and has no database or web
// dependency so the math is unit-testable in isolation; the SQL that feeds it
// lives in backend/repositories/scrapehealth_repo.go.
package diversity

import (
	"math"
	"math/rand"
	"sort"
)

// KMeans clusters vecs into at most k groups and returns a label per input row.
// Initialization is k-means++ seeded from math/rand.NewSource(seed) — so the
// same seed yields the same labels — followed by Lloyd's algorithm for up to 50
// iterations. It does not replicate scipy numerically; deterministic and
// reasonable is the bar. Callers on the unit sphere get cosine-like behaviour
// (Euclidean ≈ cosine on unit vectors).
func KMeans(vecs [][]float32, k int, seed int64) []int {
	n := len(vecs)
	labels := make([]int, n)
	if n == 0 {
		return labels
	}
	if k < 1 {
		k = 1
	}
	if k > n {
		k = n
	}
	dim := len(vecs[0])
	rng := rand.New(rand.NewSource(seed))

	// --- k-means++ initialization ---
	centroids := make([][]float64, 0, k)
	first := rng.Intn(n)
	centroids = append(centroids, toF64(vecs[first]))
	d2 := make([]float64, n)
	for len(centroids) < k {
		var total float64
		for i := range vecs {
			best := math.Inf(1)
			for _, c := range centroids {
				if dd := sqDist(vecs[i], c); dd < best {
					best = dd
				}
			}
			d2[i] = best
			total += best
		}
		// All remaining points coincide with a chosen centre → pad with copies.
		if total == 0 {
			centroids = append(centroids, toF64(vecs[rng.Intn(n)]))
			continue
		}
		target := rng.Float64() * total
		var cum float64
		pick := n - 1
		for i := range vecs {
			cum += d2[i]
			if cum >= target {
				pick = i
				break
			}
		}
		centroids = append(centroids, toF64(vecs[pick]))
	}

	// --- Lloyd iterations ---
	for iter := 0; iter < 50; iter++ {
		changed := false
		for i := range vecs {
			best, bestD := 0, math.Inf(1)
			for c := range centroids {
				if dd := sqDist(vecs[i], centroids[c]); dd < bestD {
					bestD, best = dd, c
				}
			}
			if labels[i] != best {
				labels[i] = best
				changed = true
			}
		}
		// Recompute centroids as the mean of assigned members; keep empty
		// clusters where they were so labels stay stable.
		sums := make([][]float64, k)
		counts := make([]int, k)
		for c := range sums {
			sums[c] = make([]float64, dim)
		}
		for i := range vecs {
			c := labels[i]
			counts[c]++
			for j := 0; j < dim && j < len(vecs[i]); j++ {
				sums[c][j] += float64(vecs[i][j])
			}
		}
		for c := range centroids {
			if counts[c] == 0 {
				continue
			}
			for j := 0; j < dim; j++ {
				centroids[c][j] = sums[c][j] / float64(counts[c])
			}
		}
		if !changed {
			break
		}
	}
	return labels
}

// EffectiveCount returns exp(Shannon entropy) over the cluster sizes — the
// "evenly-used concept count". Empty (zero) clusters are ignored. An even split
// of two clusters returns ≈2; a single cluster returns 1.
func EffectiveCount(sizes []int) float64 {
	var total float64
	for _, s := range sizes {
		total += float64(s)
	}
	if total == 0 {
		return 0
	}
	var entropy float64
	for _, s := range sizes {
		if s <= 0 {
			continue
		}
		p := float64(s) / total
		entropy -= p * math.Log(p)
	}
	return math.Exp(entropy)
}

// Concentration returns the share of the largest single cluster (top1) and the
// combined share of the five largest (top5). Both are 0 when there are no
// members; the caller decides how to render an empty corpus.
func Concentration(sizes []int) (top1, top5 float64) {
	var total int
	for _, s := range sizes {
		total += s
	}
	if total == 0 {
		return 0, 0
	}
	ordered := make([]int, len(sizes))
	copy(ordered, sizes)
	sort.Sort(sort.Reverse(sort.IntSlice(ordered)))
	top1 = float64(ordered[0]) / float64(total)
	var sum5 int
	for i := 0; i < 5 && i < len(ordered); i++ {
		sum5 += ordered[i]
	}
	top5 = float64(sum5) / float64(total)
	return top1, top5
}

// Novelty compares the newest finds against the older baseline by nearest-
// neighbour cosine distance. It splits by recency rank (newest recentFrac of the
// corpus vs the older remainder), so it works regardless of the corpus's
// absolute age. A higher recent distance than the older set's internal baseline
// means the engine is broadening into new idea space; lower means it is
// rehashing existing ideas. Returns a map mirroring metrics.py's _novelty: a
// {"note": …} form when there are too few finds, otherwise the counts, the two
// median distances (rounded to 2 dp), and a verdict of broadening/steady/
// converging at ±10% bands. Vectors are L2-normalized internally.
func Novelty(vecs [][]float32, ageDays []float64) map[string]any {
	const recentFrac = 0.25
	n := len(ageDays)
	if n < 8 {
		return map[string]any{"note": "not enough embeddings to measure novelty"}
	}
	unit := make([][]float32, n)
	for i := range vecs {
		unit[i] = l2normalize(vecs[i])
	}

	// Ascending age → most-recent first.
	order := make([]int, n)
	for i := range order {
		order[i] = i
	}
	sort.SliceStable(order, func(a, b int) bool { return ageDays[order[a]] < ageDays[order[b]] })

	nRecent := int(math.Round(float64(n) * recentFrac))
	if nRecent < 2 {
		nRecent = 2
	}
	recentIdx := order[:nRecent]
	priorIdx := order[nRecent:]
	if len(priorIdx) < 2 {
		return map[string]any{"note": "not enough older finds to measure novelty"}
	}

	// Each recent vector's distance to its nearest prior vector.
	recentNN := make([]float64, len(recentIdx))
	for a, ri := range recentIdx {
		best := math.Inf(-1) // max cosine
		for _, pi := range priorIdx {
			if s := dot(unit[ri], unit[pi]); s > best {
				best = s
			}
		}
		recentNN[a] = 1.0 - best
	}
	// Older set's internal nearest-neighbour baseline.
	priorNN := make([]float64, len(priorIdx))
	for a, pi := range priorIdx {
		best := math.Inf(-1)
		for b, pj := range priorIdx {
			if a == b {
				continue
			}
			if s := dot(unit[pi], unit[pj]); s > best {
				best = s
			}
		}
		priorNN[a] = 1.0 - best
	}

	recentMed := median(recentNN)
	baseMed := median(priorNN)
	verdict := "steady"
	if recentMed > baseMed*1.1 {
		verdict = "broadening"
	} else if recentMed < baseMed*0.9 {
		verdict = "converging"
	}
	return map[string]any{
		"recent_count":                      nRecent,
		"older_count":                       len(priorIdx),
		"recent_nn_distance_median":         round2(recentMed),
		"older_baseline_nn_distance_median": round2(baseMed),
		"verdict":                           verdict,
	}
}

// --------------------------------------------------------------------------- //
// helpers
// --------------------------------------------------------------------------- //

func toF64(v []float32) []float64 {
	out := make([]float64, len(v))
	for i, x := range v {
		out[i] = float64(x)
	}
	return out
}

func sqDist(a []float32, b []float64) float64 {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	var d float64
	for i := 0; i < n; i++ {
		diff := float64(a[i]) - b[i]
		d += diff * diff
	}
	return d
}

func dot(a, b []float32) float64 {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	var d float64
	for i := 0; i < n; i++ {
		d += float64(a[i]) * float64(b[i])
	}
	return d
}

func l2normalize(v []float32) []float32 {
	var norm float64
	for _, x := range v {
		norm += float64(x) * float64(x)
	}
	norm = math.Sqrt(norm)
	if norm == 0 {
		return v
	}
	out := make([]float32, len(v))
	for i, x := range v {
		out[i] = float32(float64(x) / norm)
	}
	return out
}

func median(xs []float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	s := make([]float64, len(xs))
	copy(s, xs)
	sort.Float64s(s)
	mid := len(s) / 2
	if len(s)%2 == 1 {
		return s[mid]
	}
	return (s[mid-1] + s[mid]) / 2
}

func round2(x float64) float64 {
	return math.Round(x*100) / 100
}
