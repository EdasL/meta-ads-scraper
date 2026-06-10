// Package config loads the Go API's runtime configuration from the environment,
// mirroring the env vars the Python service already uses so both runtimes share
// one Postgres and one MinIO/S3 endpoint.
package config

import (
	"log"
	"os"
	"strings"
)

// Config holds the resolved runtime settings.
type Config struct {
	DatabaseURL  string
	CorsOrigins  []string
	Port         string
	S3Endpoint   string
	S3Bucket     string
	S3Region     string
	S3AccessKey  string
	S3SecretKey  string
	GeminiAPIKey string
}

const defaultOrigins = "http://localhost:3000,http://127.0.0.1:3000"

// Load reads configuration from the environment and fails fast when the database
// URL is missing — the API cannot serve anything without it.
func Load() *Config {
	dbURL := normalizeDSN(os.Getenv("AGF_DATABASE_URL"))
	if dbURL == "" {
		dbURL = normalizeDSN(os.Getenv("AGF_TEST_DB_URL"))
	}
	if dbURL == "" {
		log.Fatal("AGF_DATABASE_URL is required")
	}
	return &Config{
		DatabaseURL:  dbURL,
		CorsOrigins:  splitOrigins(getenv("AGF_CORS_ORIGINS", defaultOrigins)),
		Port:         getenv("PORT", "8000"),
		S3Endpoint:   os.Getenv("S3_ENDPOINT"),
		S3Bucket:     getenv("S3_BUCKET", "agf"),
		S3Region:     getenv("S3_REGION", "us-east-1"),
		S3AccessKey:  os.Getenv("MINIO_ACCESS_KEY"),
		S3SecretKey:  os.Getenv("MINIO_SECRET_KEY"),
		GeminiAPIKey: os.Getenv("GEMINI_API_KEY"),
	}
}

// normalizeDSN converts a SQLAlchemy-style URL (e.g. postgresql+psycopg://…) into
// the plain libpq/pgx form (postgres://…) that GORM's postgres driver expects.
// The Python side sets AGF_DATABASE_URL with the +psycopg driver suffix, so the
// Go side has to strip it — otherwise pgx rejects the unknown scheme.
func normalizeDSN(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	i := strings.Index(raw, "://")
	if i < 0 {
		return raw // already a key=value DSN
	}
	scheme := raw[:i]
	if plus := strings.Index(scheme, "+"); plus >= 0 {
		scheme = scheme[:plus] // postgresql+psycopg -> postgresql
	}
	if scheme == "postgresql" {
		scheme = "postgres"
	}
	return scheme + raw[i:]
}

func splitOrigins(raw string) []string {
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if s := strings.TrimSpace(p); s != "" {
			out = append(out, s)
		}
	}
	return out
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
