package repositories

import (
	"sync"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/internal/reco"
	"gorm.io/gorm"
)

// prefEmbCacheTTL bounds how stale the in-process embedding snapshot may get;
// newly embedded apps appear in the queue within this window.
const prefEmbCacheTTL = 10 * time.Minute

// prefEmbCache holds every frontend.app_embeddings row (dim=768) in memory so
// warm review-queue refills do no embedding I/O — the table is ~1k rows and
// immutable per app, so a whole-table snapshot with a short TTL is enough.
// The returned map is a shared snapshot: callers must treat it as read-only
// (refreshes swap in a new map, they never mutate the old one).
var prefEmbCache struct {
	mu       sync.Mutex
	vecs     map[int64][]float64
	loadedAt time.Time
}

func prefEmbeddings(g *gorm.DB) (map[int64][]float64, error) {
	prefEmbCache.mu.Lock()
	defer prefEmbCache.mu.Unlock()
	if prefEmbCache.vecs != nil && time.Since(prefEmbCache.loadedAt) < prefEmbCacheTTL {
		return prefEmbCache.vecs, nil
	}
	var rows []struct {
		AdvertiserID int64  `gorm:"column:advertiser_id"`
		Emb          string `gorm:"column:emb"`
	}
	if err := g.Raw(
		`SELECT advertiser_id, embedding::text AS emb
		 FROM frontend.app_embeddings WHERE dim = 768`,
	).Scan(&rows).Error; err != nil {
		if prefEmbCache.vecs != nil {
			return prefEmbCache.vecs, nil // serve stale over failing the queue
		}
		return nil, err
	}
	m := make(map[int64][]float64, len(rows))
	for _, r := range rows {
		v, err := reco.ParsePgFloat64Array(r.Emb)
		if err != nil {
			return nil, err
		}
		m[r.AdvertiserID] = v
	}
	prefEmbCache.vecs = m
	prefEmbCache.loadedAt = time.Now()
	return m, nil
}
