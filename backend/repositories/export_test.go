package repositories

import (
	"time"

	"gorm.io/gorm"
)

// Test-only hooks (compiled into test binaries only).

// ResetPrefEmbCacheForTest empties the embedding cache so a test starts cold.
func ResetPrefEmbCacheForTest() {
	prefEmbCache.mu.Lock()
	defer prefEmbCache.mu.Unlock()
	prefEmbCache.vecs = nil
	prefEmbCache.loadedAt = time.Time{}
}

// AgePrefEmbCacheForTest backdates the cache past its TTL to force a reload.
func AgePrefEmbCacheForTest() {
	prefEmbCache.mu.Lock()
	defer prefEmbCache.mu.Unlock()
	prefEmbCache.loadedAt = prefEmbCache.loadedAt.Add(-2 * prefEmbCacheTTL)
}

// PrefEmbeddingsForTest exposes the unexported accessor to the _test package.
func PrefEmbeddingsForTest(g *gorm.DB) (map[int64][]float64, error) {
	return prefEmbeddings(g)
}
