// S3/MinIO backend for the media proxy. Used for any non-GCS endpoint (local
// MinIO in dev/test). Path-style addressing, a custom endpoint, static creds —
// mirrors src/agf/storage/s3.py.
package storage

import (
	"context"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

type s3Backend struct {
	client *s3.Client
	bucket string
}

func newS3Backend(ctx context.Context, endpoint, accessKey, secretKey, region, bkt string) (*s3Backend, error) {
	cfg, err := awsconfig.LoadDefaultConfig(ctx,
		awsconfig.WithRegion(region),
		awsconfig.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(accessKey, secretKey, "")),
	)
	if err != nil {
		return nil, err
	}
	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
		}
		o.UsePathStyle = true
	})
	return &s3Backend{client: client, bucket: bkt}, nil
}

func (b *s3Backend) get(ctx context.Context, key string) (*Object, error) {
	out, err := b.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(b.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, err
	}
	ctype := ""
	if out.ContentType != nil {
		ctype = *out.ContentType
	}
	var size int64 = -1
	if out.ContentLength != nil {
		size = *out.ContentLength
	}
	return &Object{Body: out.Body, ContentType: ctype, Size: size}, nil
}
