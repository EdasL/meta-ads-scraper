package controllers

import (
	"errors"
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/gin-gonic/gin"
	"net/http"
)

type ProductsController struct{}

func NewProductsController() *ProductsController { return &ProductsController{} }

type productIn struct {
	URL         string `json:"url"`
	Name        string `json:"name"`
	Description string `json:"description"`
	PageID      string `json:"page_id"`
}

func productError(c *gin.Context, err error) {
	var input *helpers.ProductInputError
	if errors.As(err, &input) {
		helpers.Detail(c, http.StatusUnprocessableEntity, input.Error())
		return
	}
	helpers.Detail(c, http.StatusInternalServerError, "could not load product data.")
}
func (*ProductsController) List(c *gin.Context) {
	u, ok := middlewares.UserFrom(c.Request.Context())
	if !ok {
		helpers.Detail(c, 401, "not signed in.")
		return
	}
	ctx, cancel := reqCtx(c)
	defer cancel()
	rows, err := repositories.ListResearchProducts(ctx, db.DB(), u.Email)
	if err != nil {
		productError(c, err)
		return
	}
	c.JSON(200, rows)
}
func (*ProductsController) Resolve(c *gin.Context) {
	u, ok := middlewares.UserFrom(c.Request.Context())
	if !ok {
		helpers.Detail(c, 401, "not signed in.")
		return
	}
	var in productIn
	if c.ShouldBindJSON(&in) != nil {
		helpers.Detail(c, 422, "enter a product link.")
		return
	}
	ctx, cancel := reqCtx(c)
	defer cancel()
	result, err := repositories.ResolveProductLink(ctx, db.DB(), u.Email, in.URL)
	if err != nil {
		productError(c, err)
		return
	}
	c.JSON(200, result)
}
func (*ProductsController) Create(c *gin.Context) {
	u, ok := middlewares.UserFrom(c.Request.Context())
	if !ok {
		helpers.Detail(c, 401, "not signed in.")
		return
	}
	var in productIn
	if c.ShouldBindJSON(&in) != nil {
		helpers.Detail(c, 422, "enter a product link and name.")
		return
	}
	ctx, cancel := reqCtx(c)
	defer cancel()
	result, err := repositories.CreateResearchProduct(ctx, db.DB(), u.Email, in.URL, in.Name, in.Description, in.PageID)
	if err != nil {
		productError(c, err)
		return
	}
	c.JSON(200, result)
}
