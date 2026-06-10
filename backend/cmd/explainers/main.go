// Command explainers generates the plain-voice advertiser explainers offline.
//
// It is decoupled from the request path on purpose: a multi-second metered
// model call must never block a READ ONLY GET transaction. The runner
// enumerates advertisers from frontend.emerging_scaling_apps, gathers their
// scraped ad copy + app data, and writes one adtech.advertiser_explainers row
// each (skipping any whose source fingerprint is unchanged).
//
//	GEMINI_API_KEY=… AGF_DATABASE_URL=… go run ./cmd/explainers
//	go run ./cmd/explainers -advertiser 123          # single advertiser
//	go run ./cmd/explainers -force -min-score 80     # regenerate top gems
package main

import (
	"context"
	"flag"
	"log"
	"sync"
	"sync/atomic"
	"time"

	"github.com/joho/godotenv"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/config"
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

const (
	concurrency = 3                // small cap: the model is metered + rate-limited
	callTimeout = 60 * time.Second // per-advertiser budget (gather + model + upsert)
)

func main() {
	advertiser := flag.Int("advertiser", 0, "generate for a single advertiser id (0 = all)")
	force := flag.Bool("force", false, "regenerate even when the source fingerprint is unchanged")
	minScore := flag.Float64("min-score", 0, "only advertisers with gem_score >= this")
	flag.Parse()

	_ = godotenv.Load()
	cfg := config.Load()

	gdb, err := db.Init(cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("database: %v", err)
	}
	defer db.Cleanup()

	gemini := helpers.NewGeminiHelper()
	if gemini == nil {
		log.Fatal("GEMINI_API_KEY is required to generate explainers")
	}

	ctx := context.Background()
	ids, err := advertiserIDs(ctx, gdb, *advertiser, *minScore)
	if err != nil {
		log.Fatalf("enumerate advertisers: %v", err)
	}
	if len(ids) == 0 {
		log.Println("no advertisers matched — nothing to do")
		return
	}
	log.Printf("generating explainers for %d advertiser(s) (concurrency=%d, force=%v)", len(ids), concurrency, *force)

	var wrote, skipped, failed int64
	sem := make(chan struct{}, concurrency)
	var wg sync.WaitGroup
	for _, id := range ids {
		id := id
		sem <- struct{}{}
		wg.Add(1)
		go func() {
			defer wg.Done()
			defer func() { <-sem }()

			callCtx, cancel := context.WithTimeout(ctx, callTimeout)
			defer cancel()

			didWrite, err := repositories.GenerateAndStore(callCtx, gdb, gemini, id, *force)
			switch {
			case err != nil:
				atomic.AddInt64(&failed, 1)
				log.Printf("advertiser %d: ERROR %v", id, err)
			case didWrite:
				atomic.AddInt64(&wrote, 1)
				log.Printf("advertiser %d: wrote explainer", id)
			default:
				atomic.AddInt64(&skipped, 1)
				log.Printf("advertiser %d: skip (unchanged)", id)
			}
		}()
	}
	wg.Wait()

	log.Printf("done: %d wrote, %d skipped, %d failed", wrote, skipped, failed)
}

// advertiserIDs returns the target advertiser ids from the product matview. A
// single -advertiser id short-circuits enumeration (it need not be in the
// matview). Otherwise every advertiser with gem_score >= minScore is returned,
// highest score first.
func advertiserIDs(ctx context.Context, g *gorm.DB, single int, minScore float64) ([]int, error) {
	if single > 0 {
		return []int{single}, nil
	}
	var ids []int
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT advertiser_id FROM frontend.emerging_scaling_apps
			 WHERE gem_score >= ?
			 ORDER BY gem_score DESC, advertiser_id`,
			minScore,
		).Scan(&ids).Error
	})
	return ids, err
}
