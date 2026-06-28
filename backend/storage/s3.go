// Package storage wraps the MinIO/S3 client used by the media proxy. It mirrors
// src/agf/storage/s3.py: path-style addressing, a custom endpoint, and static
// MinIO credentials — and reads the same env vars so both runtimes share config.
package storage

import (
	"context"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

var (
	client *s3.Client
	bucket string
)

// Init builds the singleton S3 client. Unlike the Python helper it does NOT
// create the bucket — bucket provisioning stays with the compose `minio-init`
// job (the API is a read-only consumer of objects).
func Init(ctx context.Context, endpoint, accessKey, secretKey, region, bkt string) error {
	// when_required mirrors the Python client: GCS's S3-compatible endpoint
	// rejects the default request checksum the SDK now adds, and may omit
	// response checksums; harmless against MinIO. region must match the GCS
	// bucket location (e.g. europe-west1) for v4 signing.
	cfg, err := awsconfig.LoadDefaultConfig(ctx,
		awsconfig.WithRegion(region),
		awsconfig.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(accessKey, secretKey, "")),
		awsconfig.WithRequestChecksumCalculation(aws.RequestChecksumCalculationWhenRequired),
		awsconfig.WithResponseChecksumValidation(aws.ResponseChecksumValidationWhenRequired),
	)
	if err != nil {
		return err
	}
	client = s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
		}
		o.UsePathStyle = true // == boto3 addressing_style "path"
	})
	bucket = bkt
	return nil
}

// Client returns the singleton S3 client.
func Client() *s3.Client { return client }

// Bucket returns the configured bucket name.
func Bucket() string { return bucket }
