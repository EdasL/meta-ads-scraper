package reco

import (
	"math"
	"testing"
)

func TestParsePgFloatArray(t *testing.T) {
	got, err := ParsePgFloatArray("{0.1,-0.2,0.3}")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := []float32{0.1, -0.2, 0.3}
	if len(got) != len(want) {
		t.Fatalf("len = %d, want %d", len(got), len(want))
	}
	for i := range want {
		if math.Abs(float64(got[i]-want[i])) > 1e-6 {
			t.Fatalf("got[%d] = %v, want %v", i, got[i], want[i])
		}
	}

	if empty, err := ParsePgFloatArray("{}"); err != nil || empty == nil || len(empty) != 0 {
		t.Fatalf("empty array: got %v err %v, want empty non-nil slice", empty, err)
	}

	for _, bad := range []string{"", "0.1,0.2", "{0.1,foo}", "[1,2]"} {
		if _, err := ParsePgFloatArray(bad); err == nil {
			t.Fatalf("ParsePgFloatArray(%q) = nil error, want error", bad)
		}
	}
}

func TestLikedCentroid(t *testing.T) {
	if c := LikedCentroid(nil); c != nil {
		t.Fatalf("empty input: got %v, want nil", c)
	}
	c := LikedCentroid([][]float32{{1, 0}, {0, 1}})
	want := float32(math.Sqrt2 / 2) // ~0.7071
	if len(c) != 2 {
		t.Fatalf("len = %d, want 2", len(c))
	}
	for i, v := range c {
		if math.Abs(float64(v-want)) > 1e-6 {
			t.Fatalf("centroid[%d] = %v, want %v", i, v, want)
		}
	}
}

func TestRankByCosine(t *testing.T) {
	centroid := LikedCentroid([][]float32{{1, 0}, {0, 1}}) // {.707,.707}

	aligned := RecoItem{AdvertiserID: 1, vec: []float32{1, 1}}   // cosine 1
	off := RecoItem{AdvertiserID: 2, vec: []float32{1, 0}}       // cosine ~.707
	none := RecoItem{AdvertiserID: 3, GemScore: 99, Unscored: false} // no vec

	got := RankByCosine(centroid, []RecoItem{off, none, aligned})

	if got[0].AdvertiserID != 1 {
		t.Fatalf("first = %d, want the aligned vector (1)", got[0].AdvertiserID)
	}
	if got[0].Similarity == nil || math.Abs(*got[0].Similarity-1) > 1e-6 {
		t.Fatalf("aligned similarity = %v, want ~1", got[0].Similarity)
	}
	if got[1].AdvertiserID != 2 || got[1].Similarity == nil {
		t.Fatalf("second = %+v, want off (2) with a similarity", got[1])
	}
	last := got[len(got)-1]
	if last.AdvertiserID != 3 || !last.Unscored || last.Similarity != nil {
		t.Fatalf("last = %+v, want unembedded row 3 flagged Unscored, nil similarity", last)
	}
}
