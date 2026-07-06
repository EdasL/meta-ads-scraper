package reco

import (
	"fmt"
	"math"
	"strconv"
	"strings"
)

// ContentModel is the Go port of src/agf/scoring/content_preference.py's
// ContentPreferenceModel: direction = l2norm(mean(liked) − mean(disliked));
// score(app) = dot(app, direction) (vectors are unit-norm, so cosine == dot);
// p_like is a Platt-style 1-D calibration of the raw score against the labels;
// why names the nearest liked app. Pure — no DB. The Python model stays the
// canonical reference; content_model_test.go pins parity with golden vectors.
type ContentModel struct {
	version    string
	dir        []float64
	a, b       float64
	likedVecs  [][]float64
	likedNames []string
}

// MinLikes is the coldstart threshold: fewer liked apps (with embeddings) than
// this and the model refuses to fit a direction (mirrors Python MIN_LIKES).
const MinLikes = 8

// LabeledVec is one rated app's content embedding plus its display name (used
// only for the "similar to <name>" why-string of liked apps).
type LabeledVec struct {
	Name string
	Vec  []float64
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

// FitContentModel fits the like−dislike direction and Platt calibration.
// Fewer than MinLikes liked vectors → a coldstart model (Score always 0; the
// serving layer substitutes gem_score/100, mirroring
// scripts/score_content_preferences.py).
func FitContentModel(liked, disliked []LabeledVec) *ContentModel {
	if len(liked) < MinLikes {
		return &ContentModel{version: "coldstart", a: 1.0, b: 0.0}
	}
	dim := len(liked[0].Vec)
	likeC := make([]float64, dim)
	for _, lv := range liked {
		for i, x := range lv.Vec {
			likeC[i] += x
		}
	}
	for i := range likeC {
		likeC[i] /= float64(len(liked))
	}
	disC := make([]float64, dim)
	if len(disliked) > 0 {
		for _, dv := range disliked {
			for i, x := range dv.Vec {
				disC[i] += x
			}
		}
		for i := range disC {
			disC[i] /= float64(len(disliked))
		}
	}
	diff := make([]float64, dim)
	for i := range diff {
		diff[i] = likeC[i] - disC[i]
	}
	dir := l2norm(diff)

	raws := make([]float64, 0, len(liked)+len(disliked))
	labels := make([]float64, 0, len(liked)+len(disliked))
	for _, lv := range liked {
		raws = append(raws, dot(lv.Vec, dir))
		labels = append(labels, 1)
	}
	for _, dv := range disliked {
		raws = append(raws, dot(dv.Vec, dir))
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
		version: "content-sim-v1", dir: dir, a: a, b: b,
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

// Score is the raw taste score: dot(vec, direction); 0 in coldstart.
func (m *ContentModel) Score(vec []float64) float64 {
	if m.Coldstart() {
		return 0
	}
	return dot(vec, m.dir)
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
