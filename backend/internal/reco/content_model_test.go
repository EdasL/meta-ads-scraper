package reco

import (
	"fmt"
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

// goldenFixture is the small v1-era fixture still used by the coldstart and
// no-dislikes cases: 8 likes clustered near e1, 2 dislikes near e2, dim=4.
func goldenFixture() (liked, disliked []LabeledVec) {
	for i := 0; i < 8; i++ {
		liked = append(liked, LabeledVec{
			ID:   int64(100 + i),
			Name: "liked-" + string(rune('0'+i)),
			Vec:  unitVec([]float64{1.0, 0.1 * float64(i), 0.05, 0.0}),
		})
	}
	for i := 0; i < 2; i++ {
		disliked = append(disliked, LabeledVec{
			ID:   int64(900 + i),
			Name: "disliked-" + string(rune('0'+i)),
			Vec:  unitVec([]float64{0.05, 1.0, 0.1 * float64(i), 0.0}),
		})
	}
	return liked, disliked
}

// goldenFixtureV2 mirrors scratchpad gen_goldens_v2.py: 12 likes near e1,
// 12 likes near e2, 2 dislikes near e4, 3 neutrals near e3, dim=4.
func goldenFixtureV2() (liked, disliked, neutral []LabeledVec) {
	for i := 0; i < 12; i++ {
		liked = append(liked, LabeledVec{ID: int64(100 + i), Name: fmt.Sprintf("A%d", i),
			Vec: unitVec([]float64{1.0, 0.0, 0.05 + 0.05*float64(i), 0.0})})
	}
	for i := 0; i < 12; i++ {
		liked = append(liked, LabeledVec{ID: int64(200 + i), Name: fmt.Sprintf("B%d", i),
			Vec: unitVec([]float64{0.0, 1.0, 0.05 + 0.05*float64(i), 0.0})})
	}
	for i := 0; i < 2; i++ {
		disliked = append(disliked, LabeledVec{ID: int64(900 + i), Name: fmt.Sprintf("D%d", i),
			Vec: unitVec([]float64{0.0, 0.0, 0.05 * float64(i), 1.0})})
	}
	for i := 0; i < 3; i++ {
		neutral = append(neutral, LabeledVec{ID: int64(950 + i), Name: fmt.Sprintf("N%d", i),
			Vec: unitVec([]float64{0.0, 0.05 * float64(i), 1.0, 0.0})})
	}
	return liked, disliked, neutral
}

var fitOptsGolden = FitOpts{NeutralWeight: 0.25, LikesPerCentroid: 12, MaxCentroids: 4, SplitMaxCos: 0.85}

// Golden values from src/agf/scoring/content_preference.py (v2) on
// goldenFixtureV2 via scratchpad gen_goldens_v2.py, 2026-08-04.
func TestContentModelV2GoldenParity(t *testing.T) {
	liked, disliked, neutral := goldenFixtureV2()
	m := FitContentModel(liked, disliked, neutral, fitOptsGolden)
	if m.Coldstart() {
		t.Fatal("24 likes must not be coldstart")
	}
	cases := []struct {
		vec        []float64
		pLike, raw float64
		why        string
	}{
		{unitVec([]float64{1.0, 0.0, 0.0, 0.0}), 0.9966213204, 0.7916500098, "similar to A0"},
		{unitVec([]float64{0.0, 1.0, 0.0, 0.0}), 0.9965340819, 0.7873818265, "similar to B0"},
		{unitVec([]float64{1.0, 1.0, 0.0, 0.0}), 0.9863350028, 0.5567630289, "similar to A0"},
		{unitVec([]float64{0.0, 0.0, 1.0, 0.0}), 0.7276785955, 0.0067604572, "similar to A11"},
		{unitVec([]float64{0.0, 0.0, 0.0, 1.0}), 0.0618915116, -0.6108314201, "similar to A0"},
		{unitVec([]float64{0.7, 0.1, 0.1, 0.6}), 0.8952093649, 0.2006826413, "similar to A2"},
	}
	for i, c := range cases {
		raw := m.Score(c.vec)
		if math.Abs(raw-c.raw) > 1e-6 {
			t.Errorf("case %d: raw = %.10f, want %.10f", i, raw, c.raw)
		}
		if p := m.PLike(raw); math.Abs(p-c.pLike) > 1e-6 {
			t.Errorf("case %d: p_like = %.10f, want %.10f", i, p, c.pLike)
		}
		if got := m.Why(c.vec); got != c.why {
			t.Errorf("case %d: why = %q, want %q", i, got, c.why)
		}
	}
}

func TestClusterAssignDeterministicSeparation(t *testing.T) {
	liked, _, _ := goldenFixtureV2()
	vecs := make([][]float64, len(liked))
	for i, lv := range liked {
		vecs[i] = lv.Vec
	}
	a := clusterAssign(vecs, 2, 50)
	for i := 1; i < 12; i++ {
		if a[i] != a[0] {
			t.Fatalf("cluster A split: %v", a)
		}
	}
	for i := 13; i < 24; i++ {
		if a[i] != a[12] {
			t.Fatalf("cluster B split: %v", a)
		}
	}
	if a[0] == a[12] {
		t.Fatalf("clusters merged: %v", a)
	}
}

func TestStructureGuardCollapsesBlob(t *testing.T) {
	// One blob forced into k=4 must collapse to the single global direction:
	// scores identical to a MaxCentroids=1 fit.
	var likes, dis []LabeledVec
	for i := 0; i < 12; i++ {
		likes = append(likes, LabeledVec{ID: int64(100 + i), Name: fmt.Sprintf("A%d", i),
			Vec: unitVec([]float64{1.0, 0.0, 0.05 + 0.05*float64(i), 0.0})})
	}
	for i := 0; i < 2; i++ {
		dis = append(dis, LabeledVec{ID: int64(900 + i), Name: fmt.Sprintf("D%d", i),
			Vec: unitVec([]float64{0.0, 0.0, 0.05 * float64(i), 1.0})})
	}
	multi := FitContentModel(likes, dis, nil, FitOpts{NeutralWeight: 0.25, LikesPerCentroid: 1, MaxCentroids: 4, SplitMaxCos: 0.85})
	single := FitContentModel(likes, dis, nil, FitOpts{NeutralWeight: 0.25, LikesPerCentroid: 12, MaxCentroids: 1, SplitMaxCos: 0.85})
	for _, probe := range [][]float64{{1, 0, 0, 0}, {0, 1, 0, 0}, {0.5, 0.5, 0.5, 0}} {
		v := unitVec(probe)
		if math.Abs(multi.Score(v)-single.Score(v)) > 1e-12 {
			t.Errorf("guard failed to collapse: %v vs %v", multi.Score(v), single.Score(v))
		}
	}
}

func TestFitOrderInvariantAfterIDSort(t *testing.T) {
	liked, disliked, neutral := goldenFixtureV2()
	rev := make([]LabeledVec, len(liked))
	for i, lv := range liked {
		rev[len(liked)-1-i] = lv
	}
	m1 := FitContentModel(liked, disliked, neutral, fitOptsGolden)
	m2 := FitContentModel(rev, disliked, neutral, fitOptsGolden)
	probe := unitVec([]float64{0.5, 0.5, 0, 0})
	if math.Abs(m1.Score(probe)-m2.Score(probe)) > 1e-12 {
		t.Errorf("order-dependent fit: %v vs %v", m1.Score(probe), m2.Score(probe))
	}
}

func TestContentModelColdstart(t *testing.T) {
	liked, disliked := goldenFixture()
	m := FitContentModel(liked[:7], disliked, nil, DefaultFitOpts()) // 7 < MinLikes
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
	m := FitContentModel(liked, nil, nil, DefaultFitOpts()) // dis centroid = zero vector
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
