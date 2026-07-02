// Package reco holds the pure math for recommended gems: parsing Postgres REAL[]
// embeddings, building a liked-apps centroid, and ranking a feed by cosine
// similarity to that centroid. It has no database or gin dependency so the math
// is unit-testable in isolation; the SQL that feeds it lives in
// backend/repositories/embeddings_repo.go.
package reco

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

// RecoItem is one feed row in the recommendation ranking. Embedded rows carry a
// content vector (set via SetVec) and receive a Similarity score; rows without
// an embedding are flagged Unscored and sink below every scored row.
type RecoItem struct {
	AdvertiserID int64    `json:"advertiser_id"`
	AppName      string   `json:"app_name"`
	Cohort       string   `json:"cohort"`
	GemScore     float64  `json:"gem_score"`
	Similarity   *float64 `json:"similarity"`
	Unscored     bool     `json:"unscored"`

	vec []float32
}

// SetVec attaches the item's parsed content embedding. Repositories populate
// this before calling RankByCosine; the field is unexported so ranking is the
// only thing that reads it.
func (it *RecoItem) SetVec(v []float32) { it.vec = v }

// ParsePgFloatArray parses a Postgres float array text literal like
// "{0.1,-0.2,0.3}" into a slice. Embeddings are selected as embedding::text
// because GORM cannot scan REAL[] directly. An empty array "{}" yields an empty
// (non-nil) slice; anything not shaped like a brace-wrapped list, or a element
// that is not a float, is an error.
func ParsePgFloatArray(s string) ([]float32, error) {
	s = strings.TrimSpace(s)
	if len(s) < 2 || s[0] != '{' || s[len(s)-1] != '}' {
		return nil, fmt.Errorf("reco: not a pg array literal: %q", s)
	}
	inner := strings.TrimSpace(s[1 : len(s)-1])
	if inner == "" {
		return []float32{}, nil
	}
	parts := strings.Split(inner, ",")
	out := make([]float32, len(parts))
	for i, p := range parts {
		f, err := strconv.ParseFloat(strings.TrimSpace(p), 32)
		if err != nil {
			return nil, fmt.Errorf("reco: bad float %q in array: %w", p, err)
		}
		out[i] = float32(f)
	}
	return out, nil
}

// LikedCentroid returns the L2-normalized element-wise mean of the input
// vectors — the direction of the user's taste. It returns nil when there are no
// vectors (the caller reads nil as "not personalized"). Vectors are assumed to
// share the leading vector's length; shorter ones are padded with zeros so a
// malformed short row cannot panic the ranking.
func LikedCentroid(vecs [][]float32) []float32 {
	if len(vecs) == 0 {
		return nil
	}
	dim := len(vecs[0])
	if dim == 0 {
		return nil
	}
	sum := make([]float64, dim)
	for _, v := range vecs {
		for i := 0; i < dim && i < len(v); i++ {
			sum[i] += float64(v[i])
		}
	}
	n := float64(len(vecs))
	mean := make([]float32, dim)
	var norm float64
	for i := range sum {
		m := sum[i] / n
		mean[i] = float32(m)
		norm += m * m
	}
	norm = math.Sqrt(norm)
	if norm == 0 {
		return mean
	}
	for i := range mean {
		mean[i] = float32(float64(mean[i]) / norm)
	}
	return mean
}

// RankByCosine orders items by cosine similarity to the centroid: embedded rows
// first (highest similarity first), then every embedding-less row in the order
// given (the feed's GemScore order), each flagged Unscored with a nil
// Similarity. It mutates and returns a reordered copy of items.
func RankByCosine(centroid []float32, items []RecoItem) []RecoItem {
	scored := make([]RecoItem, 0, len(items))
	unscored := make([]RecoItem, 0, len(items))
	for _, it := range items {
		if len(it.vec) == 0 {
			it.Unscored = true
			it.Similarity = nil
			unscored = append(unscored, it)
			continue
		}
		c := cosine(centroid, it.vec)
		it.Unscored = false
		it.Similarity = &c
		scored = append(scored, it)
	}
	sort.SliceStable(scored, func(i, j int) bool {
		return *scored[i].Similarity > *scored[j].Similarity
	})
	return append(scored, unscored...)
}

// cosine returns the cosine similarity of a float32 vector against a float64
// centroid, 0 when either has zero magnitude or the lengths disagree past the
// shorter one.
func cosine(centroid []float32, v []float32) float64 {
	n := len(centroid)
	if len(v) < n {
		n = len(v)
	}
	var dot, na, nb float64
	for i := 0; i < n; i++ {
		a := float64(centroid[i])
		b := float64(v[i])
		dot += a * b
		na += a * a
		nb += b * b
	}
	if na == 0 || nb == 0 {
		return 0
	}
	return dot / (math.Sqrt(na) * math.Sqrt(nb))
}
