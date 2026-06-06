// Command agf-api is the Field Desk read-only HTTP API in Go (Gin + GORM).
// It replaces the Python FastAPI service (src/agf/api) and is a second client of
// the same Postgres + MinIO; the Python worker/scoring/migrations are unchanged.
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	sentrygin "github.com/getsentry/sentry-go/gin"
	"github.com/gin-gonic/gin"
	"github.com/joho/godotenv"

	"github.com/edaslakavicius/emerging-app-finder/backend/config"
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/monitoring"
	"github.com/edaslakavicius/emerging-app-finder/backend/routers"
	"github.com/edaslakavicius/emerging-app-finder/backend/storage"
)

func main() {
	_ = godotenv.Load()
	cfg := config.Load()

	if os.Getenv("GIN_MODE") == "release" {
		gin.SetMode(gin.ReleaseMode)
	}

	// Optional error tracking (no-op unless SENTRY_DSN is set) — mirrors femrun.
	if err := monitoring.InitSentry(); err != nil {
		log.Printf("sentry init failed: %v", err)
	}
	defer monitoring.Flush()

	ctx := context.Background()
	gdb, err := db.Init(cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("database: %v", err)
	}
	defer db.Cleanup()
	db.SanityCheck(ctx, gdb)

	if err := storage.Init(ctx, cfg.S3Endpoint, cfg.S3AccessKey, cfg.S3SecretKey, cfg.S3Region, cfg.S3Bucket); err != nil {
		log.Fatalf("s3: %v", err)
	}

	// Explicit middleware stack (not gin.Default) so panics render our JSON error
	// shape and Sentry — when enabled — captures them.
	r := gin.New()
	r.Use(gin.Logger(), middlewares.Recovery())
	if monitoring.Enabled() {
		r.Use(sentrygin.New(sentrygin.Options{Repanic: true}))
	}
	r.Use(middlewares.CORS(cfg.CorsOrigins))
	routers.Init(r)

	srv := &http.Server{
		Addr:    ":" + cfg.Port,
		Handler: r,
		// Slowloris guard. WriteTimeout is intentionally left unset (0): the media
		// proxy streams arbitrarily large objects, and per-request context
		// timeouts already bound the DB-backed handlers.
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	go func() {
		log.Printf("Field Desk API (Go) listening on :%s", cfg.Port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %v", err)
		}
	}()

	stop, cancel := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer cancel()
	<-stop.Done()

	log.Println("shutting down…")
	shutCtx, shutCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutCancel()
	if err := srv.Shutdown(shutCtx); err != nil {
		log.Printf("shutdown: %v", err)
	}
}
