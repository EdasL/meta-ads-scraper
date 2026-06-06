// Package monitoring wires optional error tracking. It mirrors femrun's
// monitoring.InitSentryClient + bootstrap-api's helpers.InitSentry: Sentry is
// enabled only when SENTRY_DSN is set, so local/dev runs need no account.
package monitoring

import (
	"os"
	"strconv"
	"time"

	"github.com/getsentry/sentry-go"
)

// Enabled reports whether Sentry is configured.
func Enabled() bool { return os.Getenv("SENTRY_DSN") != "" }

// InitSentry initializes Sentry when SENTRY_DSN is set; otherwise it is a no-op.
func InitSentry() error {
	if !Enabled() {
		return nil
	}
	rate := 0.1
	if v := os.Getenv("SENTRY_TRACES_SAMPLE_RATE"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			rate = f
		}
	}
	return sentry.Init(sentry.ClientOptions{
		Dsn:              os.Getenv("SENTRY_DSN"),
		Environment:      os.Getenv("SENTRY_ENVIRONMENT"),
		EnableTracing:    true,
		TracesSampleRate: rate,
	})
}

// Flush drains buffered events on shutdown.
func Flush() {
	if Enabled() {
		sentry.Flush(2 * time.Second)
	}
}
