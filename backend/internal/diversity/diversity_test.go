package diversity

import (
	"math"
	"testing"
)

// KMeans on two obvious 2-D blobs must separate them, and be deterministic:
// the same seed yields the same labels across runs.
func TestKMeansTwoBlobsDeterministic(t *testing.T) {
	// Blob A near the origin, blob B near (10,10).
	vecs := [][]float32{
		{0, 0}, {0.1, 0.2}, {0.2, 0.1}, {-0.1, 0.1},
		{10, 10}, {10.1, 9.9}, {9.9, 10.2}, {10.2, 10.1},
	}
	a := KMeans(vecs, 2, 0)
	b := KMeans(vecs, 2, 0)

	if len(a) != len(vecs) {
		t.Fatalf("labels length = %d, want %d", len(a), len(vecs))
	}
	for i := range a {
		if a[i] != b[i] {
			t.Fatalf("non-deterministic: run1=%v run2=%v", a, b)
		}
	}
	// First four (blob A) share one label; last four (blob B) share the other.
	labelA, labelB := a[0], a[4]
	if labelA == labelB {
		t.Fatalf("blobs collapsed into one cluster: %v", a)
	}
	for i := 0; i < 4; i++ {
		if a[i] != labelA {
			t.Fatalf("blob A member %d has label %d, want %d (labels=%v)", i, a[i], labelA, a)
		}
	}
	for i := 4; i < 8; i++ {
		if a[i] != labelB {
			t.Fatalf("blob B member %d has label %d, want %d (labels=%v)", i, a[i], labelB, a)
		}
	}
}

func TestEffectiveCount(t *testing.T) {
	if got := EffectiveCount([]int{10, 10}); math.Abs(got-2.0) > 1e-9 {
		t.Fatalf("EffectiveCount([10,10]) = %v, want ~2.0", got)
	}
	if got := EffectiveCount([]int{20}); math.Abs(got-1.0) > 1e-9 {
		t.Fatalf("EffectiveCount([20]) = %v, want 1.0", got)
	}
	// Zeros are ignored (empty clusters contribute nothing).
	if got := EffectiveCount([]int{10, 10, 0}); math.Abs(got-2.0) > 1e-9 {
		t.Fatalf("EffectiveCount([10,10,0]) = %v, want ~2.0", got)
	}
}

func TestConcentration(t *testing.T) {
	top1, top5 := Concentration([]int{5, 3, 2})
	if math.Abs(top1-0.5) > 1e-9 {
		t.Fatalf("top1 = %v, want 0.5", top1)
	}
	if math.Abs(top5-1.0) > 1e-9 {
		t.Fatalf("top5 = %v, want 1.0 (all within top 5)", top5)
	}
}

// Novelty verdicts "broadening" when the newest finds are orthogonal to a
// tightly-clustered older baseline: recent nearest-neighbour distance (~1) far
// exceeds the older set's internal baseline (~0).
func TestNoveltyBroadening(t *testing.T) {
	var vecs [][]float32
	var ages []float64
	// 3 recent finds on axis 1 (most recent → smallest age).
	for i := 0; i < 3; i++ {
		vecs = append(vecs, []float32{0, 1})
		ages = append(ages, float64(i)) // 0..2 days
	}
	// 9 older finds tightly clustered on axis 0.
	for i := 0; i < 9; i++ {
		vecs = append(vecs, []float32{1, float32(i) * 0.01})
		ages = append(ages, float64(30+i)) // ~30 days
	}
	out := Novelty(vecs, ages)
	if v, ok := out["verdict"]; !ok || v != "broadening" {
		t.Fatalf("verdict = %v, want broadening (out=%v)", out["verdict"], out)
	}
	if out["recent_count"] != 3 {
		t.Fatalf("recent_count = %v, want 3", out["recent_count"])
	}
}

// Too few embeddings → a note, no verdict.
func TestNoveltyTooFew(t *testing.T) {
	vecs := [][]float32{{1, 0}, {0, 1}, {1, 1}}
	ages := []float64{1, 2, 3}
	out := Novelty(vecs, ages)
	if _, ok := out["note"]; !ok {
		t.Fatalf("expected a note for n<8, got %v", out)
	}
}
