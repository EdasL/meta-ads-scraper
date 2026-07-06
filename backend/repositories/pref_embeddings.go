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
// whole review queue: when a warm snapshot exists it is served stale instead,
// and usedFallback is true so the caller can leave the cache TTL untouched (a
// failed refresh must retry on the next call, not wait out a fresh window).
// With a cold cache (warm == nil) the parse error propagates.
func buildPrefEmbMap(rows []prefEmbRow, warm map[int64][]float64) (m map[int64][]float64, usedFallback bool, err error) {
	m = make(map[int64][]float64, len(rows))
	for _, r := range rows {
		v, perr := reco.ParsePgFloat64Array(r.Emb)
		if perr != nil {
			if warm != nil {
				return warm, true, nil // serve stale over failing the queue
			}
			return nil, false, perr
		}
		m[r.AdvertiserID] = v
	}
	return m, false, nil
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
	m, usedFallback, err := buildPrefEmbMap(rows, prefEmbCache.vecs)
	if err != nil {
		return nil, err
	}
	commitPrefEmbRefresh(m, usedFallback)
	return m, nil
}

// commitPrefEmbRefresh installs a fresh snapshot and resets the TTL. A fallback
// (stale) result is NOT committed: leaving loadedAt untouched means a failed
// refresh retries on the very next call instead of waiting out a fresh window.
// The caller must hold prefEmbCache.mu.
func commitPrefEmbRefresh(m map[int64][]float64, usedFallback bool) {
	if usedFallback {
		return
	}
	prefEmbCache.vecs = m
	prefEmbCache.loadedAt = time.Now()
}
