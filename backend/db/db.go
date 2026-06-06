// Package db owns the GORM connection pool and the transaction discipline.
//
// IMPORTANT: the Postgres schema is owned by the Python migrations
// (db/schema/*.sql + db/migrate.py). This service NEVER calls AutoMigrate and
// defines no GORM table-mapped models — every read binds to column names via
// raw SQL, so there is nothing here that could create or alter the schema.
package db

import (
	"context"
	"errors"
	"log"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

var pool *gorm.DB

var errNoPool = errors.New("db: pool not initialized")

// Init opens the pool and verifies connectivity.
func Init(dsn string) (*gorm.DB, error) {
	g, err := gorm.Open(postgres.Open(dsn), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Error),
	})
	if err != nil {
		return nil, err
	}
	sqlDB, err := g.DB()
	if err != nil {
		return nil, err
	}
	sqlDB.SetMaxOpenConns(20)
	sqlDB.SetMaxIdleConns(5)
	if err := sqlDB.Ping(); err != nil {
		return nil, err
	}
	pool = g
	return g, nil
}

// DB returns the singleton pool.
func DB() *gorm.DB { return pool }

// Cleanup closes the underlying pool.
func Cleanup() {
	if pool == nil {
		return
	}
	if sqlDB, err := pool.DB(); err == nil {
		_ = sqlDB.Close()
	}
}

// WithReadTx runs fn in a READ ONLY transaction bound to ctx. Postgres rejects
// any write on this connection — the same default-deny guarantee the Python API
// gets from `SET TRANSACTION READ ONLY` (src/agf/api/deps.py:get_conn).
func WithReadTx(ctx context.Context, g *gorm.DB, fn func(tx *gorm.DB) error) error {
	tx := g.WithContext(ctx).Begin()
	if tx.Error != nil {
		return tx.Error
	}
	defer tx.Rollback()
	if err := tx.Exec("SET TRANSACTION READ ONLY").Error; err != nil {
		return err
	}
	return fn(tx)
}

// WithWriteTx runs fn in a ctx-bound read-write transaction, committing on
// success and rolling back on error. This is the single write path: the review
// decision.
func WithWriteTx(ctx context.Context, g *gorm.DB, fn func(tx *gorm.DB) error) error {
	return g.WithContext(ctx).Transaction(fn)
}

// Ready pings the database for readiness probes (distinct from liveness).
func Ready(ctx context.Context) error {
	if pool == nil {
		return errNoPool
	}
	sqlDB, err := pool.DB()
	if err != nil {
		return err
	}
	return sqlDB.PingContext(ctx)
}

// SanityCheck logs a loud warning if the product matview the API serves is
// absent — a fail-soft signal that the DB is mis-pointed or migrations have not
// run. It is a sanity probe, not a drift check (that guard stays in Python:
// tests/db/test_models_match_schema.py).
func SanityCheck(ctx context.Context, g *gorm.DB) {
	var n int64
	err := g.WithContext(ctx).Raw(
		"SELECT count(*) FROM pg_matviews WHERE schemaname = 'frontend' AND matviewname = 'emerging_scaling_apps'",
	).Scan(&n).Error
	if err != nil {
		log.Printf("sanity check failed: %v", err)
		return
	}
	if n == 0 {
		log.Println("WARNING: frontend.emerging_scaling_apps not found — run the Python migrations")
	}
}
