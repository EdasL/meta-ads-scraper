package reco

import (
	"math"
	"testing"
)

// unitVec l2-normalizes in float64, mirroring the numpy fixture generator.
func unitVec(v []float64) []float64 {
	var n float64
	for _, x := range v {
		n += x * x
	}
	n = math.Sqrt(n)
	out := make([]float64, len(v))
	for i, x := range v {
		out[i] = x / n
	}
	return out
}

// goldenFixture builds the exact fixture used to generate the Python golden
// numbers: 8 likes clustered near e1, 2 dislikes near e2, dim=4.
func goldenFixture() (liked, disliked []LabeledVec) {
	for i := 0; i < 8; i++ {
		liked = append(liked, LabeledVec{
			Name: "liked-" + string(rune('0'+i)),
			Vec:  unitVec([]float64{1.0, 0.1 * float64(i), 0.05, 0.0}),
		})
	}
	for i := 0; i < 2; i++ {
		disliked = append(disliked, LabeledVec{
			Name: "disliked-" + string(rune('0'+i)),
			Vec:  unitVec([]float64{0.05, 1.0, 0.1 * float64(i), 0.0}),
		})
	}
	return liked, disliked
}

// Golden values from src/agf/scoring/content_preference.py on goldenFixture
// (PYTHONPATH=src .venv/bin/python, 2026-07-06).
func TestContentModelGoldenParity(t *testing.T) {
	liked, disliked := goldenFixture()
	m := FitContentModel(liked, disliked)
	if m.Coldstart() {
		t.Fatal("8 likes must not be coldstart")
	}

	cases := []struct {
		vec   []float64
		pLike float64
		why   string
	}{
		{unitVec([]float64{1, 0, 0, 0}), 0.9990970037, "similar to liked-0"},
		{unitVec([]float64{0, 1, 0, 0}), 0.0298404249, "similar to liked-7"},
		{unitVec([]float64{1, 1, 0, 0}), 0.8840827902, "similar to liked-7"},
		{unitVec([]float64{0, 0, 1, 1}), 0.7504451316, "similar to liked-0"},
	}
	for i, c := range cases {
		p := m.PLike(m.Score(c.vec))
		if math.Abs(p-c.pLike) > 1e-6 {
			t.Errorf("case %d: p_like = %.10f, want %.10f", i, p, c.pLike)
		}
		if got := m.Why(c.vec); got != c.why {
			t.Errorf("case %d: why = %q, want %q", i, got, c.why)
		}
	}
}

func TestContentModelColdstart(t *testing.T) {
	liked, disliked := goldenFixture()
	m := FitContentModel(liked[:7], disliked) // 7 < MinLikes
	if !m.Coldstart() {
		t.Fatal("7 likes must be coldstart")
	}
	if raw := m.Score(unitVec([]float64{1, 0, 0, 0})); raw != 0 {
		t.Fatalf("coldstart Score = %v, want 0", raw)
	}
	if p := m.PLike(0); math.Abs(p-0.5) > 1e-12 {
		t.Fatalf("coldstart PLike(0) = %v, want 0.5 (identity calib)", p)
	}
	if w := m.Why(unitVec([]float64{1, 0, 0, 0})); w != "" {
		t.Fatalf("coldstart Why = %q, want empty", w)
	}
}

func TestContentModelNoDislikes(t *testing.T) {
	liked, _ := goldenFixture()
	m := FitContentModel(liked, nil) // dis centroid = zero vector
	if m.Coldstart() {
		t.Fatal("8 likes, 0 dislikes must not be coldstart")
	}
	// All labels are 1 → calibration pushes p_like toward 1 for on-taste vecs.
	if p := m.PLike(m.Score(liked[0].Vec)); p < 0.5 {
		t.Fatalf("on-taste p_like = %v, want > 0.5", p)
	}
}

func TestParsePgFloat64Array(t *testing.T) {
	got, err := ParsePgFloat64Array("{0.1,-0.2,0.3}")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := []float64{0.1, -0.2, 0.3}
	for i := range want {
		if math.Abs(got[i]-want[i]) > 1e-12 {
			t.Fatalf("got[%d] = %v, want %v", i, got[i], want[i])
		}
	}
	if empty, err := ParsePgFloat64Array("{}"); err != nil || empty == nil || len(empty) != 0 {
		t.Fatalf("empty array: got %v err %v, want empty non-nil slice", empty, err)
	}
	for _, bad := range []string{"", "0.1,0.2", "{0.1,foo}", "[1,2]"} {
		if _, err := ParsePgFloat64Array(bad); err == nil {
			t.Fatalf("ParsePgFloat64Array(%q) = nil error, want error", bad)
		}
	}
}
