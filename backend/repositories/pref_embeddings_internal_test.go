package repositories

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

// TestPrefEmbeddingsParseFallback covers the branch a REAL[] column cannot
// seed via SQL (it rejects unparseable text at write time): a row that our
// parser rejects during a refresh must serve the warm snapshot, not error.
func TestPrefEmbeddingsParseFallback(t *testing.T) {
	warm := map[int64][]float64{1: {0.6, 0.8}}
	warmCrowd := map[int64]*float64{1: nil}
	bad := []prefEmbRow{{AdvertiserID: 2, Emb: "{notafloat}"}}

	// Warm cache: one unparseable row serves the stale snapshot and flags fallback.
	got, gotCrowd, usedFallback, err := buildPrefEmbMap(bad, warm, warmCrowd)
	require.NoError(t, err)
	require.True(t, usedFallback, "parse failure with a warm cache must signal fallback")
	require.Equal(t, warm, got, "warm snapshot must be served on parse failure")
	require.Equal(t, warmCrowd, gotCrowd, "warm crowding snapshot must be served too")

	// Cold cache: the parse error propagates and there is no fallback.
	_, _, usedFallback, err = buildPrefEmbMap(bad, nil, nil)
	require.Error(t, err)
	require.False(t, usedFallback)

	// All-good rows build a fresh map (no fallback) regardless of the warm snapshot.
	crowded := 0.9
	good := []prefEmbRow{{AdvertiserID: 3, Emb: "{1.0,0.0}", Crowding: &crowded}}
	m, crowd, usedFallback, err := buildPrefEmbMap(good, warm, warmCrowd)
	require.NoError(t, err)
	require.False(t, usedFallback)
	require.InDelta(t, 1.0, m[3][0], 1e-9)
	require.NotContains(t, m, int64(1), "fresh build must not carry warm entries")
	require.NotNil(t, crowd[3])
	require.InDelta(t, 0.9, *crowd[3], 1e-9)
}

// TestPrefEmbeddingsRefreshTTL exercises the stamp path directly: a fallback
// refresh must NOT reset loadedAt (so the next call retries), while a clean
// refresh installs the new snapshot and stamps the TTL.
func TestPrefEmbeddingsRefreshTTL(t *testing.T) {
	prefEmbCache.mu.Lock()
	defer prefEmbCache.mu.Unlock()
	defer func() {
		prefEmbCache.vecs = nil
		prefEmbCache.crowd = nil
		prefEmbCache.loadedAt = time.Time{}
	}()

	stale := map[int64][]float64{1: {0.6, 0.8}}
	staleCrowd := map[int64]*float64{1: nil}
	old := time.Now().Add(-time.Hour)
	prefEmbCache.vecs = stale
	prefEmbCache.crowd = staleCrowd
	prefEmbCache.loadedAt = old

	// Fallback: cache untouched — same map, same (old) loadedAt → next call retries.
	commitPrefEmbRefresh(stale, staleCrowd, true)
	require.Equal(t, old, prefEmbCache.loadedAt, "failed refresh must not reset the TTL")
	require.Equal(t, stale, prefEmbCache.vecs)

	// Clean refresh: new snapshot installed and TTL stamped fresh.
	fresh := map[int64][]float64{9: {1.0, 0.0}}
	freshCrowd := map[int64]*float64{9: nil}
	commitPrefEmbRefresh(fresh, freshCrowd, false)
	require.Equal(t, fresh, prefEmbCache.vecs)
	require.Equal(t, freshCrowd, prefEmbCache.crowd)
	require.True(t, prefEmbCache.loadedAt.After(old), "clean refresh must stamp a fresh TTL")
}
