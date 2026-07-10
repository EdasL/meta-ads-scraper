package repositories_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

func TestPrefEmbeddingsCache(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	repositories.ResetPrefEmbCacheForTest()

	must(g.Exec(`DELETE FROM frontend.app_embeddings WHERE advertiser_id IN (990001, 990002)`).Error)
	must(g.Exec(`INSERT INTO frontend.app_embeddings (advertiser_id, embedding, content_hash, dim, crowding)
	             VALUES (990001, '{0.6,0.8}', 'h1', 768, 0.91)`).Error)

	m1, crowd1, err := repositories.PrefEmbeddingsForTest(g)
	require.NoError(t, err)
	require.InDelta(t, 0.6, m1[990001][0], 1e-9)
	require.InDelta(t, 0.8, m1[990001][1], 1e-9)
	require.NotNil(t, crowd1[990001], "crowding must be read alongside the embedding")
	require.InDelta(t, 0.91, *crowd1[990001], 1e-6) // stored as real (float32)

	// Within the TTL a new row is NOT visible (cache serves the snapshot)…
	// This row leaves crowding NULL to prove NULL scans to a nil pointer.
	must(g.Exec(`INSERT INTO frontend.app_embeddings (advertiser_id, embedding, content_hash, dim)
	             VALUES (990002, '{1.0,0.0}', 'h2', 768)`).Error)
	m2, _, err := repositories.PrefEmbeddingsForTest(g)
	require.NoError(t, err)
	require.NotContains(t, m2, int64(990002), "warm cache must serve the snapshot")

	// …and after the TTL elapses it is.
	repositories.AgePrefEmbCacheForTest()
	m3, crowd3, err := repositories.PrefEmbeddingsForTest(g)
	require.NoError(t, err)
	require.Contains(t, m3, int64(990002), "stale cache must reload")
	require.Nil(t, crowd3[990002], "NULL crowding scans to a nil pointer (not crowded)")

	must(g.Exec(`DELETE FROM frontend.app_embeddings WHERE advertiser_id IN (990001, 990002)`).Error)
	repositories.ResetPrefEmbCacheForTest()
}
