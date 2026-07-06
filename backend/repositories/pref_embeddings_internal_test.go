package repositories

import (
	"testing"

	"github.com/stretchr/testify/require"
)

// TestPrefEmbeddingsParseFallback covers the branch a REAL[] column cannot
// seed via SQL (it rejects unparseable text at write time): a row that our
// parser rejects during a refresh must serve the warm snapshot, not error.
func TestPrefEmbeddingsParseFallback(t *testing.T) {
	warm := map[int64][]float64{1: {0.6, 0.8}}
	bad := []prefEmbRow{{AdvertiserID: 2, Emb: "{notafloat}"}}

	// Warm cache: one unparseable row serves the stale snapshot.
	got, err := buildPrefEmbMap(bad, warm)
	require.NoError(t, err)
	require.Equal(t, warm, got, "warm snapshot must be served on parse failure")

	// Cold cache: the parse error propagates.
	_, err = buildPrefEmbMap(bad, nil)
	require.Error(t, err)

	// All-good rows build a fresh map regardless of the warm snapshot.
	good := []prefEmbRow{{AdvertiserID: 3, Emb: "{1.0,0.0}"}}
	m, err := buildPrefEmbMap(good, warm)
	require.NoError(t, err)
	require.InDelta(t, 1.0, m[3][0], 1e-9)
	require.NotContains(t, m, int64(1), "fresh build must not carry warm entries")
}
