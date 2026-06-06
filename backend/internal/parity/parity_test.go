// Package parity is the cutover GO/NO-GO gate: it hits the same request matrix
// against the Python FastAPI and the Go API (both pointed at the SAME seeded
// Postgres) and asserts the responses are semantically equal — equal status
// codes and JSON that compares equal by value (numbers numerically, datetimes by
// instant, object keys order-independent). Byte-for-byte differences that don't
// change meaning (74 vs 74.0, key order, +00:00 vs Z) are intentionally ignored;
// see backend/DEVIATIONS.md.
//
// Run it during cutover, e.g.:
//
//	PARITY_PY_URL=http://localhost:8001 PARITY_GO_URL=http://localhost:8000 \
//	  go test ./internal/parity/ -v
//
// Skipped unless both URLs are set.
package parity

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"testing"
	"time"
)

func urls(t *testing.T) (string, string) {
	py, go_ := os.Getenv("PARITY_PY_URL"), os.Getenv("PARITY_GO_URL")
	if py == "" || go_ == "" {
		t.Skip("set PARITY_PY_URL and PARITY_GO_URL to run the parity gate")
	}
	return py, go_
}

func fetch(t *testing.T, base, path string) (int, any) {
	t.Helper()
	resp, err := http.Get(base + path)
	if err != nil {
		t.Fatalf("GET %s%s: %v", base, path, err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	var v any
	if len(b) > 0 {
		_ = json.Unmarshal(b, &v)
	}
	return resp.StatusCode, v
}

func TestParity(t *testing.T) {
	py, goURL := urls(t)

	// Discover a real advertiser id from the Python shortlist.
	_, sl := fetch(t, py, "/api/shortlist")
	advID := firstAdvertiserID(sl)

	paths := []string{
		"/healthz",
		"/api/shortlist",
		"/api/shortlist?cohort=ios",
		"/api/shortlist?min_score=80",
		"/api/review/queue",
		"/api/health/metrics",
		"/api/media/etc/passwd", // 403 both sides
		"/api/gems/999999999",   // 404 both sides
	}
	if advID >= 0 {
		paths = append(paths,
			fmt.Sprintf("/api/gems/%d", advID),
			fmt.Sprintf("/api/gems/%d/scaling", advID),
		)
	}

	for _, p := range paths {
		t.Run(p, func(t *testing.T) {
			pyCode, pyBody := fetch(t, py, p)
			goCode, goBody := fetch(t, goURL, p)
			if pyCode != goCode {
				t.Fatalf("status mismatch: python=%d go=%d", pyCode, goCode)
			}
			if err := semEqual(pyBody, goBody, "$"); err != nil {
				t.Fatalf("body mismatch: %v", err)
			}
		})
	}
}

func firstAdvertiserID(sl any) int {
	arr, ok := sl.([]any)
	if !ok || len(arr) == 0 {
		return -1
	}
	row, ok := arr[0].(map[string]any)
	if !ok {
		return -1
	}
	if f, ok := row["advertiser_id"].(float64); ok {
		return int(f)
	}
	return -1
}

// semEqual compares two decoded JSON values for semantic equality.
func semEqual(a, b any, path string) error {
	switch av := a.(type) {
	case nil:
		if b != nil {
			return fmt.Errorf("%s: python=null go=%v", path, b)
		}
	case float64:
		bv, ok := b.(float64)
		if !ok {
			return fmt.Errorf("%s: python number, go=%T", path, b)
		}
		if diff := av - bv; diff > 1e-6 || diff < -1e-6 {
			return fmt.Errorf("%s: python=%v go=%v", path, av, bv)
		}
	case bool:
		if bv, ok := b.(bool); !ok || bv != av {
			return fmt.Errorf("%s: python=%v go=%v", path, av, b)
		}
	case string:
		bv, ok := b.(string)
		if !ok {
			return fmt.Errorf("%s: python string, go=%T", path, b)
		}
		if av != bv && !sameInstant(av, bv) {
			return fmt.Errorf("%s: python=%q go=%q", path, av, bv)
		}
	case []any:
		bv, ok := b.([]any)
		if !ok {
			return fmt.Errorf("%s: python array, go=%T", path, b)
		}
		if len(av) != len(bv) {
			return fmt.Errorf("%s: array len python=%d go=%d", path, len(av), len(bv))
		}
		for i := range av {
			if err := semEqual(av[i], bv[i], fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
	case map[string]any:
		bv, ok := b.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: python object, go=%T", path, b)
		}
		if len(av) != len(bv) {
			return fmt.Errorf("%s: object key count python=%d go=%d", path, len(av), len(bv))
		}
		for k, vv := range av {
			ov, ok := bv[k]
			if !ok {
				return fmt.Errorf("%s.%s: missing in go", path, k)
			}
			if err := semEqual(vv, ov, path+"."+k); err != nil {
				return err
			}
		}
	default:
		return fmt.Errorf("%s: unhandled type %T", path, a)
	}
	return nil
}

// sameInstant treats two RFC3339 timestamps as equal if they denote the same
// instant (so +00:00 vs Z, or microsecond vs nanosecond, do not matter).
func sameInstant(a, b string) bool {
	ta, ea := time.Parse(time.RFC3339, a)
	tb, eb := time.Parse(time.RFC3339, b)
	return ea == nil && eb == nil && ta.Equal(tb)
}
