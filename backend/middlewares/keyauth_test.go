package middlewares

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestUserFromRoundTrip(t *testing.T) {
	ctx := WithUser(context.Background(), AuthedUser{Email: "a@b.c", Role: "member"})
	u, ok := UserFrom(ctx)
	if !ok || u.Email != "a@b.c" || u.Role != "member" {
		t.Fatalf("got %+v ok=%v", u, ok)
	}
	if _, ok := UserFrom(context.Background()); ok {
		t.Fatal("empty context must not carry a user")
	}
}

func TestKeyAuthRejectsMissingHeader(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/x", KeyAuth(false), func(c *gin.Context) { c.Status(200) })
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/x", nil)
	r.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("want 401, got %d", w.Code)
	}
}

func TestPerKeyRateLimit(t *testing.T) {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/x", PerKeyRateLimit(2), func(c *gin.Context) { c.Status(200) })
	codes := []int{}
	for i := 0; i < 4; i++ {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/x", nil)
		req.Header.Set("Authorization", "Bearer fd_same")
		r.ServeHTTP(w, req)
		codes = append(codes, w.Code)
	}
	if codes[0] != 200 || codes[1] != 200 || codes[3] != 429 {
		t.Fatalf("codes=%v", codes)
	}
}
