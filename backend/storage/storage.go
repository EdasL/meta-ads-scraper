// Package storage serves creative/funnel bytes for the media proxy. It picks a
// backend at Init time by endpoint: GCS (storage.googleapis.com) uses the
// native Google client (ADC) — aws-sdk-go-v2's SigV4 is not compatible with
// GCS's HMAC interop endpoint (SignatureDoesNotMatch on every op), and the Mac
// API already depends on ADC for the Cloud SQL proxy, so this adds no new
// failure mode. Any other endpoint (MinIO in dev/test) uses the S3 client.
package storage

import (
	"context"
	"io"
	"strings"
)

// Object is a backend-agnostic read result for one stored object.
type Object struct {
	Body        io.ReadCloser
	ContentType string
	Size        int64 // -1 if unknown
}

type backend interface {
	get(ctx context.Context, key string) (*Object, error)
}

var (
	active backend
	bucket string
)

// Init selects and builds the storage backend. Signature is unchanged so
// callers (main.go) need no edits.
func Init(ctx context.Context, endpoint, accessKey, secretKey, region, bkt string) error {
	bucket = bkt
	if strings.Contains(endpoint, "storage.googleapis.com") {
		b, err := newGCSBackend(ctx, bkt)
		if err != nil {
			return err
		}
		active = b
		return nil
	}
	b, err := newS3Backend(ctx, endpoint, accessKey, secretKey, region, bkt)
	if err != nil {
		return err
	}
	active = b
	return nil
}

// Get streams one object by key from the active backend.
func Get(ctx context.Context, key string) (*Object, error) {
	return active.get(ctx, key)
}

// Bucket returns the configured bucket name.
func Bucket() string { return bucket }
