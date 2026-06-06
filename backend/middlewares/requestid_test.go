package middlewares

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestRequestID_GeneratesWhenAbsent(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(RequestID())
	r.GET("/", func(c *gin.Context) {
		if c.GetString(ContextRequestID) == "" {
			t.Error("request_id not set on context")
		}
		c.Status(http.StatusOK)
	})
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/", nil))
	if w.Header().Get(RequestIDHeader) == "" {
		t.Error("X-Request-ID not echoed on response")
	}
}

func TestRequestID_HonorsInbound(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(RequestID())
	r.GET("/", func(c *gin.Context) { c.Status(http.StatusOK) })
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set(RequestIDHeader, "abc-123")
	r.ServeHTTP(w, req)
	if got := w.Header().Get(RequestIDHeader); got != "abc-123" {
		t.Errorf("X-Request-ID = %q, want abc-123 (inbound honored)", got)
	}
}
