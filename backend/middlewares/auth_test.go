package middlewares

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

func newAuthRouter() *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.POST("/w", RequireToken(), func(c *gin.Context) { c.Status(http.StatusOK) })
	return r
}

func post(r *gin.Engine, bearer string) int {
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/w", nil)
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	r.ServeHTTP(w, req)
	return w.Code
}

func TestRequireToken_NoopWhenUnset(t *testing.T) {
	t.Setenv(APITokenEnv, "")
	if code := post(newAuthRouter(), ""); code != http.StatusOK {
		t.Fatalf("unset token should allow writes; got %d", code)
	}
}

func TestRequireToken_EnforcesWhenSet(t *testing.T) {
	t.Setenv(APITokenEnv, "s3cret")
	r := newAuthRouter()
	if code := post(r, ""); code != http.StatusUnauthorized {
		t.Errorf("missing token → want 401, got %d", code)
	}
	if code := post(r, "nope"); code != http.StatusUnauthorized {
		t.Errorf("wrong token → want 401, got %d", code)
	}
	if code := post(r, "s3cret"); code != http.StatusOK {
		t.Errorf("correct token → want 200, got %d", code)
	}
}
