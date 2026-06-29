// Native GCS backend for the media proxy. Uses Application Default Credentials
// (the same ADC the Cloud SQL proxy relies on), avoiding the aws-sdk-go-v2 ↔
// GCS HMAC SigV4 incompatibility.
package storage

import (
	"context"
	"errors"
	"io"

	gcs "cloud.google.com/go/storage"
)

type gcsBackend struct {
	client *gcs.Client
	bucket string
}

func newGCSBackend(ctx context.Context, bkt string) (*gcsBackend, error) {
	client, err := gcs.NewClient(ctx)
	if err != nil {
		return nil, err
	}
	return &gcsBackend{client: client, bucket: bkt}, nil
}

func (b *gcsBackend) get(ctx context.Context, key string) (*Object, error) {
	obj := b.client.Bucket(b.bucket).Object(key)
	r, err := obj.NewReader(ctx)
	if err != nil {
		// Surface a clean miss for not-found; other errors propagate.
		if errors.Is(err, gcs.ErrObjectNotExist) {
			return nil, err
		}
		return nil, err
	}
	return &Object{
		Body:        r,
		ContentType: r.Attrs.ContentType,
		Size:        r.Attrs.Size,
	}, nil
}

var _ io.ReadCloser = (*gcs.Reader)(nil)
