package mcpserver

import (
	"context"
	"errors"
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type productLinkIn struct {
	URL string `json:"url" jsonschema:"product website, store, or Meta Ad Library URL"`
}
type addProductIn struct {
	URL         string `json:"url" jsonschema:"the product link returned by resolve_product_link"`
	Name        string `json:"name" jsonschema:"product name confirmed by the user or stored source"`
	Description string `json:"description,omitempty" jsonschema:"product audience and benefit, especially when it has no stored ads"`
	PageID      string `json:"page_id,omitempty" jsonschema:"chosen page from resolve_product_link matches; required when ambiguous"`
}

func productToolError(err error) error {
	var input *helpers.ProductInputError
	if errors.As(err, &input) {
		return input
	}
	return errors.New("could not load product data")
}
func (h *handlers) resolveProductLink(ctx context.Context, _ *mcp.CallToolRequest, in productLinkIn) (*mcp.CallToolResult, any, error) {
	result, err := repositories.ResolveProductLink(ctx, db.DB(), h.u.Email, in.URL)
	if err != nil {
		return nil, nil, productToolError(err)
	}
	return nil, result, nil
}
func (h *handlers) addProduct(ctx context.Context, _ *mcp.CallToolRequest, in addProductIn) (*mcp.CallToolResult, any, error) {
	result, err := repositories.CreateResearchProduct(ctx, db.DB(), h.u.Email, in.URL, in.Name, in.Description, in.PageID)
	if err != nil {
		return nil, nil, productToolError(err)
	}
	return nil, result, nil
}
