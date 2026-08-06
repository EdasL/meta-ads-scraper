package controllers

import (
	"archive/zip"
	"bytes"
	"errors"
	"io"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

func strp(s string) *string { return &s }

func fetcher(bodies map[string]string) func(string) (io.ReadCloser, error) {
	return func(key string) (io.ReadCloser, error) {
		b, ok := bodies[key]
		if !ok {
			return nil, errors.New("missing")
		}
		return io.NopCloser(strings.NewReader(b)), nil
	}
}

func readZip(t *testing.T, buf *bytes.Buffer) map[string]string {
	t.Helper()
	zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	require.NoError(t, err)
	out := map[string]string{}
	for _, f := range zr.File {
		rc, err := f.Open()
		require.NoError(t, err)
		b, err := io.ReadAll(rc)
		require.NoError(t, err)
		rc.Close()
		out[f.Name] = string(b)
	}
	return out
}

func TestWriteSavedAdsZip_NamesEntriesAfterTheAd(t *testing.T) {
	rows := []models.SavedAdMedia{
		{ExternalAdID: "1271851644912798", PageName: strp("BePresent App"), MediaType: strp("image"), MediaS3Key: "creatives/aaa.jpg"},
		{ExternalAdID: "9988776655", PageName: strp("Femrun"), MediaType: strp("video"), MediaS3Key: "creatives/bbb.mp4"},
	}
	var buf bytes.Buffer
	n, err := writeSavedAdsZip(&buf, rows, fetcher(map[string]string{
		"creatives/aaa.jpg": "JPEGBYTES",
		"creatives/bbb.mp4": "MP4BYTES",
	}))
	require.NoError(t, err)
	require.Equal(t, 2, n)

	files := readZip(t, &buf)
	require.Equal(t, "JPEGBYTES", files["bepresent-app-1271851644912798.jpg"])
	require.Equal(t, "MP4BYTES", files["femrun-9988776655.mp4"])
}

// One missing object must not cost the caller the whole archive.
func TestWriteSavedAdsZip_SkipsUnfetchableObjects(t *testing.T) {
	rows := []models.SavedAdMedia{
		{ExternalAdID: "1", PageName: strp("Alpha"), MediaS3Key: "creatives/ok.jpg"},
		{ExternalAdID: "2", PageName: strp("Beta"), MediaS3Key: "creatives/gone.jpg"},
	}
	var buf bytes.Buffer
	n, err := writeSavedAdsZip(&buf, rows, fetcher(map[string]string{"creatives/ok.jpg": "OK"}))
	require.NoError(t, err)
	require.Equal(t, 1, n)

	files := readZip(t, &buf)
	require.Len(t, files, 1)
	require.Equal(t, "OK", files["alpha-1.jpg"])
}

func TestWriteSavedAdsZip_FallsBackWhenPageNameIsMissingAndDedupesNames(t *testing.T) {
	rows := []models.SavedAdMedia{
		{ExternalAdID: "7", AppName: strp("Just The App"), MediaS3Key: "creatives/a.jpg"},
		{ExternalAdID: "8", MediaS3Key: "creatives/b.jpg"},
		// Same page + same key shape as the first: names must not collide.
		{ExternalAdID: "7", AppName: strp("Just The App"), MediaS3Key: "creatives/c.jpg"},
	}
	var buf bytes.Buffer
	n, err := writeSavedAdsZip(&buf, rows, fetcher(map[string]string{
		"creatives/a.jpg": "A", "creatives/b.jpg": "B", "creatives/c.jpg": "C",
	}))
	require.NoError(t, err)
	require.Equal(t, 3, n)

	files := readZip(t, &buf)
	require.Equal(t, "A", files["just-the-app-7.jpg"])
	require.Equal(t, "B", files["ad-8.jpg"])
	require.Equal(t, "C", files["just-the-app-7-2.jpg"])
}

func TestSavedAdFileName_MatchesTheClientRule(t *testing.T) {
	// Mirrors web/src/lib/adMedia.ts adDownloadName.
	require.Equal(t, "bepresent-app-123.jpg",
		savedAdFileName(models.SavedAdMedia{ExternalAdID: "123", PageName: strp("BePresent App!"), MediaS3Key: "creatives/x.jpg"}))
	require.Equal(t, "femrun-9.mp4",
		savedAdFileName(models.SavedAdMedia{ExternalAdID: "9", PageName: strp("Femrun"), MediaS3Key: "creatives/y.mp4"}))
	// Unusable extension falls back to the media type.
	require.Equal(t, "acme-5.mp4",
		savedAdFileName(models.SavedAdMedia{ExternalAdID: "5", PageName: strp("Acme"), MediaType: strp("video"), MediaS3Key: "creatives/novideoext"}))
}
