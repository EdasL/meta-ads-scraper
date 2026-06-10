package middlewares

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

// Recovery renders a JSON {"detail": ...} body on panic instead of gin's default
// empty 500, keeping the error contract consistent with every other handler.
func Recovery() gin.HandlerFunc {
	return gin.CustomRecovery(func(c *gin.Context, _ any) {
		c.AbortWithStatusJSON(http.StatusInternalServerError, gin.H{"detail": "something went wrong. try again."})
	})
}
