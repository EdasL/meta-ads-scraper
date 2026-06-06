package middlewares

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

// A panicking handler must yield a 500 with the JSON {"detail":...} error shape,
// not gin's default empty body.
func TestRecovery_RendersJSON(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(Recovery())
	r.GET("/boom", func(c *gin.Context) { panic("kaboom") })

	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/boom", nil))

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", w.Code)
	}
	if ct := w.Header().Get("Content-Type"); ct[:16] != "application/json" {
		t.Fatalf("content-type = %q, want application/json", ct)
	}
	if got := w.Body.String(); got != `{"detail":"grug fall down. try again."}` {
		t.Fatalf("body = %s", got)
	}
}
