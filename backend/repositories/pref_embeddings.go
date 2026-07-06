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

type prefEmbRow struct {
	AdvertiserID int64  `gorm:"column:advertiser_id"`
	Emb          string `gorm:"column:emb"`
}

// buildPrefEmbMap parses fetched rows into an advertiser_id → vector map. A
// refresh failure — including a single unparseable row — must not fail the
// whole review queue: when a warm snapshot exists it is served stale instead.
// With a cold cache (warm == nil) the parse error propagates.
func buildPrefEmbMap(rows []prefEmbRow, warm map[int64][]float64) (map[int64][]float64, error) {
	m := make(map[int64][]float64, len(rows))
	for _, r := range rows {
		v, err := reco.ParsePgFloat64Array(r.Emb)
		if err != nil {
			if warm != nil {
				return warm, nil // serve stale over failing the queue
			}
			return nil, err
		}
		m[r.AdvertiserID] = v
	}
	return m, nil
}

func prefEmbeddings(g *gorm.DB) (map[int64][]float64, error) {
	prefEmbCache.mu.Lock()
	defer prefEmbCache.mu.Unlock()
	if prefEmbCache.vecs != nil && time.Since(prefEmbCache.loadedAt) < prefEmbCacheTTL {
		return prefEmbCache.vecs, nil
	}
	var rows []prefEmbRow
	if err := g.Raw(
		`SELECT advertiser_id, embedding::text AS emb
		 FROM frontend.app_embeddings WHERE dim = 768`,
	).Scan(&rows).Error; err != nil {
		if prefEmbCache.vecs != nil {
			return prefEmbCache.vecs, nil // serve stale over failing the queue
		}
		return nil, err
	}
	m, err := buildPrefEmbMap(rows, prefEmbCache.vecs)
	if err != nil {
		return nil, err
	}
	prefEmbCache.vecs = m
	prefEmbCache.loadedAt = time.Now()
	return m, nil
}
