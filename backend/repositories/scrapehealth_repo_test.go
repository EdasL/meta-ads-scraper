package repositories

import (
	"reflect"
	"testing"
)

// sortByDesc must match the Python reference _concept_groups (metrics.py):
// np.argsort(sizes)[::-1] resolves equal-size clusters to the HIGHER original
// cluster id first (stable ascending, then reversed). Verified against numpy:
// sizes [5,3,5] -> [2,0,1]; all-equal [3,3,3] -> [2,1,0].
func TestSortByDescTieBreak(t *testing.T) {
	cases := []struct {
		name  string
		sizes []int
		want  []int
	}{
		{"two equal size-5 clusters, higher id first", []int{5, 3, 5}, []int{2, 0, 1}},
		{"all equal -> descending id", []int{3, 3, 3}, []int{2, 1, 0}},
		{"mixed with equal biggest", []int{5, 3, 5, 5, 2}, []int{3, 2, 0, 1, 4}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			order := make([]int, len(tc.sizes))
			for i := range order {
				order[i] = i
			}
			sortByDesc(order, func(c int) int { return tc.sizes[c] })
			if !reflect.DeepEqual(order, tc.want) {
				t.Fatalf("sortByDesc(sizes=%v) = %v, want %v", tc.sizes, order, tc.want)
			}
		})
	}
}

// conceptGroups surfaces the largest clusters first; on equal size the higher
// cluster id must win the tie, so its representative app leads. Two size-2
// clusters (ids 0 and 1) -> cluster 1's representative first.
func TestConceptGroupsTieBreak(t *testing.T) {
	name := func(s string) *string { return &s }
	rows := []ideaRow{
		{PageName: name("c0-a"), SourceKeyword: name("kw0")},
		{PageName: name("c0-b"), SourceKeyword: name("kw0")},
		{PageName: name("c1-a"), SourceKeyword: name("kw1")},
		{PageName: name("c1-b"), SourceKeyword: name("kw1")},
	}
	labels := []int{0, 0, 1, 1}
	vecs := [][]float32{{1, 0}, {1, 0}, {0, 1}, {0, 1}}
	centroids := [][]float64{{1, 0}, {0, 1}}
	sizes := []int{2, 2}

	groups := conceptGroups(rows, labels, vecs, centroids, sizes, 12)
	if len(groups) != 2 {
		t.Fatalf("got %d groups, want 2: %v", len(groups), groups)
	}
	// Higher cluster id (1) leads on the tie; its most-central member is c1-a.
	if got := groups[0]["representative_app"]; got != rows[2].PageName {
		t.Fatalf("groups[0] rep = %v, want cluster 1 (%q first on equal size)", got, "c1-a")
	}
	if got := groups[1]["representative_app"]; got != rows[0].PageName {
		t.Fatalf("groups[1] rep = %v, want cluster 0 second", got)
	}
}
