package reco

import (
	"fmt"
	"math"
	"os"
	"slices"
	"sort"
	"strconv"
	"strings"
)

// ContentModel is the Go port of src/agf/scoring/content_preference.py's
// ContentPreferenceModel (v2, multi-centroid): likes are clustered and each
// cluster gets its own direction = l2norm(mean(cluster) − dis_c); score(app) =
// max over cluster directions of dot(app, dir) (vectors are unit-norm, so
// cosine == dot); p_like is a Platt-style 1-D calibration of the raw score
// against the labels; why names the nearest liked app. Pure — no DB. The Python
// model stays the canonical reference; content_model_test.go pins parity with
// golden vectors.
type ContentModel struct {
	version    string
	dirs       [][]float64
	a, b       float64
	likedVecs  [][]float64
	likedNames []string
}

// MinLikes is the coldstart threshold: fewer liked apps (with embeddings) than
// this and the model refuses to fit a direction (mirrors Python MIN_LIKES).
const MinLikes = 8

// LabeledVec is one rated app's content embedding plus identity. ID orders the
// like set before clustering so Go and Python fit identical models regardless
// of scan order. Name feeds the "similar to <name>" why-string.
type LabeledVec struct {
	ID   int64
	Name string
	Vec  []float64
}

// FitOpts are the v2 taste knobs. DefaultFitOpts reads the env once per call —
// cheap, and keeps this package free of load-order coupling.
type FitOpts struct {
	NeutralWeight    float64 // weight of a neutral vec in the negative centroid
	LikesPerCentroid int     // K = clamp(nLikes/this, 1, MaxCentroids)
	MaxCentroids     int
	SplitMaxCos      float64 // structure guard: dirs closer than this collapse to one
}

func DefaultFitOpts() FitOpts {
	return FitOpts{
		NeutralWeight:    envFloat("AGF_NEUTRAL_WEIGHT", 0.25),
		LikesPerCentroid: envInt("AGF_TASTE_LIKES_PER_CENTROID", 12),
		MaxCentroids:     envInt("AGF_TASTE_CENTROIDS_MAX", 4),
		SplitMaxCos:      envFloat("AGF_TASTE_SPLIT_MAX_COS", 0.85),
	}
}

func envFloat(key string, def float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func l2norm(v []float64) []float64 {
	var n float64
	for _, x := range v {
		n += x * x
	}
	n = math.Sqrt(n)
	if n < 1e-12 {
		return v
	}
	out := make([]float64, len(v))
	for i, x := range v {
		out[i] = x / n
	}
	return out
}

// fitPlatt is the tiny gradient-descent logistic of label ~ a*raw + b, ported
// verbatim (500 iterations, lr 0.5, init a=1 b=0, non-finite → identity).
func fitPlatt(raws []float64, labels []float64) (float64, float64) {
	a, b := 1.0, 0.0
	n := float64(len(raws))
	if n < 1 {
		n = 1
	}
	const iters, lr = 500, 0.5
	for it := 0; it < iters; it++ {
		var ga, gb float64
		for i := range raws {
			p := 1.0 / (1.0 + math.Exp(-(a*raws[i] + b)))
			ga += (p - labels[i]) * raws[i]
			gb += p - labels[i]
		}
		a -= lr * ga / n
		b -= lr * gb / n
	}
	if math.IsNaN(a) || math.IsInf(a, 0) || math.IsNaN(b) || math.IsInf(b, 0) {
		return 1.0, 0.0
	}
	return a, b
}

// clusterAssign is the Go port of content_preference._cluster: deterministic
// k-means over unit vectors. Farthest-point init from index 0 (next center =
// point with smallest max-dot to chosen centers, first-min wins); Lloyd's
// assignment by highest dot (first-max wins); centers are l2-normalized member
// means; empty clusters keep their old center. No RNG — golden-pinned to Python.
func clusterAssign(vecs [][]float64, k, iters int) []int {
	n := len(vecs)
	if k > n {
		k = n
	}
	if k <= 1 || n == 0 {
		return make([]int, n)
	}
	chosen := []int{0}
	inChosen := map[int]bool{0: true}
	for len(chosen) < k {
		bestI, bestSim := -1, math.Inf(1)
		for i := 0; i < n; i++ {
			if inChosen[i] {
				continue
			}
			sim := math.Inf(-1)
			for _, c := range chosen {
				if s := dot(vecs[i], vecs[c]); s > sim {
					sim = s
				}
			}
			if sim < bestSim {
				bestSim, bestI = sim, i
			}
		}
		chosen = append(chosen, bestI)
		inChosen[bestI] = true
	}
	dim := len(vecs[0])
	centers := make([][]float64, len(chosen))
	for j, c := range chosen {
		centers[j] = append([]float64(nil), vecs[c]...)
	}
	assign := make([]int, n)
	haveAssign := false
	for it := 0; it < iters; it++ {
		cur := make([]int, n)
		for i, v := range vecs {
			best, bestJ := math.Inf(-1), 0
			for j, c := range centers {
				if s := dot(v, c); s > best {
					best, bestJ = s, j
				}
			}
			cur[i] = bestJ
		}
		if haveAssign && slices.Equal(cur, assign) {
			break
		}
		assign, haveAssign = cur, true
		for j := range centers {
			mean := make([]float64, dim)
			cnt := 0
			for i, a := range assign {
				if a == j {
					for d, x := range vecs[i] {
						mean[d] += x
					}
					cnt++
				}
			}
			if cnt > 0 {
				for d := range mean {
					mean[d] /= float64(cnt)
				}
				centers[j] = l2norm(mean)
			}
		}
	}
	return assign
}

// FitContentModel fits the v2 multi-centroid model (port of ContentPreferenceModel.fit).
// Likes are sorted by ID, clustered deterministically; each cluster direction is
// l2norm(mean(cluster) − disC) where disC is the weighted mean of disliked plus
// opts.NeutralWeight × neutral vectors. Score = max over cluster directions.
// Neutrals never enter the Platt calibration. Fewer than MinLikes likes → coldstart
// (Score always 0; the serving layer substitutes gem_score/100, mirroring
// scripts/score_content_preferences.py).
func FitContentModel(liked, disliked, neutral []LabeledVec, opts FitOpts) *ContentModel {
	if len(liked) < MinLikes {
		return &ContentModel{version: "coldstart", a: 1.0, b: 0.0}
	}
	liked = append([]LabeledVec(nil), liked...)
	sort.Slice(liked, func(i, j int) bool { return liked[i].ID < liked[j].ID })

	dim := len(liked[0].Vec)
	disC := make([]float64, dim)
	denom := float64(len(disliked)) + opts.NeutralWeight*float64(len(neutral))
	if denom > 0 {
		for _, dv := range disliked {
			for i, x := range dv.Vec {
				disC[i] += x
			}
		}
		for _, nv := range neutral {
			for i, x := range nv.Vec {
				disC[i] += opts.NeutralWeight * x
			}
		}
		for i := range disC {
			disC[i] /= denom
		}
	}

	vecs := make([][]float64, len(liked))
	for i, lv := range liked {
		vecs[i] = lv.Vec
	}
	// Global single direction — the structure guard's fallback (mirrors the
	// Python model's `direction` property).
	globalMean := make([]float64, dim)
	for _, v := range vecs {
		for d, x := range v {
			globalMean[d] += x
		}
	}
	for d := range globalMean {
		globalMean[d] = globalMean[d]/float64(len(vecs)) - disC[d]
	}
	globalDir := l2norm(globalMean)

	perC := opts.LikesPerCentroid
	if perC < 1 {
		perC = 1
	}
	k := len(liked) / perC
	if k < 1 {
		k = 1
	}
	if k > opts.MaxCentroids {
		k = opts.MaxCentroids
	}
	assign := clusterAssign(vecs, k, 50)

	maxCluster := 0
	for _, a := range assign {
		if a > maxCluster {
			maxCluster = a
		}
	}
	var dirs [][]float64
	for j := 0; j <= maxCluster; j++ {
		mean := make([]float64, dim)
		cnt := 0
		for i, a := range assign {
			if a == j {
				for d, x := range vecs[i] {
					mean[d] += x
				}
				cnt++
			}
		}
		if cnt == 0 {
			continue
		}
		for d := range mean {
			mean[d] = mean[d]/float64(cnt) - disC[d]
		}
		dirs = append(dirs, l2norm(mean))
	}

	// Structure guard: near-coincident split directions mean the clustering cut
	// one blob, not real taste structure — collapse to the global direction
	// (2026-08-04 measurement: founder's likes best-k centroid cosine 0.92).
	if len(dirs) > 1 {
	guard:
		for i := range dirs {
			for j := i + 1; j < len(dirs); j++ {
				if dot(dirs[i], dirs[j]) > opts.SplitMaxCos {
					dirs = [][]float64{globalDir}
					break guard
				}
			}
		}
	}

	scoreMax := func(v []float64) float64 {
		best := math.Inf(-1)
		for _, d := range dirs {
			if s := dot(v, d); s > best {
				best = s
			}
		}
		return best
	}
	raws := make([]float64, 0, len(liked)+len(disliked))
	labels := make([]float64, 0, len(liked)+len(disliked))
	for _, lv := range liked {
		raws = append(raws, scoreMax(lv.Vec))
		labels = append(labels, 1)
	}
	for _, dv := range disliked {
		raws = append(raws, scoreMax(dv.Vec))
		labels = append(labels, 0)
	}
	a, b := fitPlatt(raws, labels)

	likedVecs := make([][]float64, len(liked))
	likedNames := make([]string, len(liked))
	for i, lv := range liked {
		likedVecs[i] = lv.Vec
		likedNames[i] = lv.Name
	}
	return &ContentModel{
		version: "content-sim-v2", dirs: dirs, a: a, b: b,
		likedVecs: likedVecs, likedNames: likedNames,
	}
}

func dot(a, b []float64) float64 {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	var s float64
	for i := 0; i < n; i++ {
		s += a[i] * b[i]
	}
	return s
}

// Coldstart reports whether the model has no fitted direction.
func (m *ContentModel) Coldstart() bool { return m.version == "coldstart" }

// Score is the raw taste score: max over cluster directions of dot(vec, dir);
// 0 in coldstart.
func (m *ContentModel) Score(vec []float64) float64 {
	if m.Coldstart() || len(m.dirs) == 0 {
		return 0
	}
	best := math.Inf(-1)
	for _, d := range m.dirs {
		if s := dot(vec, d); s > best {
			best = s
		}
	}
	return best
}

// PLike maps a raw score through the Platt calibration to a probability.
func (m *ContentModel) PLike(raw float64) float64 {
	return 1.0 / (1.0 + math.Exp(-(m.a*raw + m.b)))
}

// Why names the nearest liked app by dot product (first max wins, as numpy
// argmax); empty in coldstart.
func (m *ContentModel) Why(vec []float64) string {
	if m.Coldstart() || len(m.likedVecs) == 0 {
		return ""
	}
	best, bestI := math.Inf(-1), 0
	for i, lv := range m.likedVecs {
		if s := dot(lv, vec); s > best {
			best, bestI = s, i
		}
	}
	return "similar to " + m.likedNames[bestI]
}

// ParsePgFloat64Array parses a Postgres float array text literal like
// "{0.1,-0.2,0.3}" into float64s (the float32 variant is ParsePgFloatArray;
// the content model runs in float64 for parity with the numpy reference).
func ParsePgFloat64Array(s string) ([]float64, error) {
	s = strings.TrimSpace(s)
	if len(s) < 2 || s[0] != '{' || s[len(s)-1] != '}' {
		return nil, fmt.Errorf("reco: not a pg array literal: %q", s)
	}
	inner := strings.TrimSpace(s[1 : len(s)-1])
	if inner == "" {
		return []float64{}, nil
	}
	parts := strings.Split(inner, ",")
	out := make([]float64, len(parts))
	for i, p := range parts {
		f, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			return nil, fmt.Errorf("reco: bad float %q in pg array", p)
		}
		out[i] = f
	}
	return out, nil
}
