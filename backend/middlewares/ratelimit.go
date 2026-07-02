package middlewares

import (
	"net/http"
	"sync"

	"github.com/gin-gonic/gin"
	"golang.org/x/time/rate"

	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
)

// PerKeyRateLimit is a per-bearer-token token bucket (single-pod deployment,
// in-memory is sufficient; see spec "Out of scope" for multi-pod).
func PerKeyRateLimit(perMin int) gin.HandlerFunc {
	var mu sync.Mutex
	buckets := map[string]*rate.Limiter{}
	return func(c *gin.Context) {
		key := c.GetHeader("Authorization")
		mu.Lock()
		lim, ok := buckets[key]
		if !ok {
			lim = rate.NewLimiter(rate.Limit(float64(perMin)/60.0), perMin)
			buckets[key] = lim
		}
		mu.Unlock()
		if !lim.Allow() {
			helpers.Detail(c, http.StatusTooManyRequests, "rate limit exceeded. retry in a minute.")
			c.Abort()
			return
		}
		c.Next()
	}
}
