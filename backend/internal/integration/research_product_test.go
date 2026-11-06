package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/stretchr/testify/require"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestResearchProductsAndChatContext(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	ctx, g := context.Background(), db.DB()
	tag := fmt.Sprintf("%d", time.Now().UnixNano())
	me := "product-" + tag + "@test.local"
	other := "private-" + me
	require.NoError(t, repositories.UpsertUser(ctx, g, me, "unused", "Product test", "admin"))
	t.Cleanup(func() {
		g.Exec(`DELETE FROM frontend.hunts WHERE user_email IN (?,?)`, me, other)
		g.Exec(`DELETE FROM frontend.saved_groups WHERE user_email IN (?,?)`, me, other)
		g.Exec(`DELETE FROM frontend.saved_gems WHERE user_email IN (?,?)`, me, other)
		g.Exec(`DELETE FROM frontend.research_products WHERE user_email IN (?,?)`, me, other)
		g.Exec(`DELETE FROM frontend.ad_searches WHERE created_by IN (?,?)`, me, other)
	})
	page := "990" + tag[:12]
	link := "https://www.facebook.com/ads/library/?view_all_page_id=" + page
	var searchID int64
	require.NoError(t, g.Raw(`INSERT INTO frontend.ad_searches(created_by,status) VALUES (?,'done') RETURNING id`, me).Scan(&searchID).Error)
	require.NoError(t, g.Exec(`INSERT INTO frontend.ad_search_results(search_id,ad_archive_id,page_id,page_name,source_term,country,is_active,body_text,creative_hash,link_url)
 VALUES (?,? ,?,'Owned product','seed','US',true,'Reduce distracting phone use','test',?)`, searchID, tag[:12], page, "https://product-"+tag+".example/offer").Error)
	result, err := repositories.ResolveProductLink(ctx, g, me, link)
	require.NoError(t, err)
	require.Equal(t, "Owned product", result.Name)
	require.Len(t, result.Matches, 1)
	hidden, err := repositories.ResolveProductLink(ctx, g, other, link)
	require.NoError(t, err)
	require.Empty(t, hidden.Matches)
	fromAd, err := repositories.ResolveProductLink(ctx, g, me, "https://facebook.com/ads/library/?id="+tag[:12])
	require.NoError(t, err)
	require.Equal(t, page, fromAd.PageID)
	_, err = repositories.ResolveProductLink(ctx, g, other, "https://facebook.com/ads/library/?id="+tag[:12])
	require.Error(t, err)
	product, err := repositories.CreateResearchProduct(ctx, g, me, link, "Quizlock", "Earn screen time by studying", "")
	require.NoError(t, err)
	require.Contains(t, string(product.AdCopy), "Reduce distracting phone use")
	duplicate, err := repositories.CreateResearchProduct(ctx, g, me, "https://product-"+tag+".example/offer", "Second name", "", page)
	require.NoError(t, err)
	require.Equal(t, product.ID, duplicate.ID)
	require.Equal(t, "Quizlock", duplicate.Name)
	_, err = repositories.ResearchProductByID(ctx, g, other, product.ID)
	require.ErrorIs(t, err, repositories.ErrProductNotFound)
	unknown, err := repositories.CreateResearchProduct(ctx, g, me, "https://new-"+tag+".example", "No-ad product", "Helps people focus", "")
	require.NoError(t, err)
	require.JSONEq(t, "[]", string(unknown.AdCopy))
	folder, err := repositories.CreateSavedGroup(ctx, g, me, "Quizlock references")
	require.NoError(t, err)
	ok, err := repositories.AttachFolderProduct(ctx, g, me, folder.ID, product.ID)
	require.NoError(t, err)
	require.True(t, ok)
	ok, err = repositories.AttachFolderProduct(ctx, g, other, folder.ID, product.ID)
	require.NoError(t, err)
	require.False(t, ok)
	require.NoError(t, g.Exec(`INSERT INTO frontend.saved_gems(user_email,advertiser_id,page_name,gem_score) VALUES (?,?,'one sec',1)`, me, advID).Error)
	_, err = repositories.AddSavedGroupMember(ctx, g, me, folder.ID, int64(advID))
	require.NoError(t, err)
	hid, err := repositories.CreateHuntWithProduct(ctx, g, me, "Find ads", nil, &product.ID)
	require.NoError(t, err)
	got, err := repositories.HuntFolderContext(ctx, g, hid)
	require.NoError(t, err)
	require.Equal(t, product.ID, got.ReferenceProduct.ID)
	require.Len(t, got.Brands, 1)
	require.Len(t, got.Folders, 1)
	_, err = repositories.CreateHuntWithProduct(ctx, g, other, "No access", nil, &product.ID)
	require.ErrorIs(t, err, repositories.ErrProductNotFound)
	_, err = repositories.CreateHuntWithProduct(ctx, g, me, "Wrong folder", &folder.ID, &unknown.ID)
	require.ErrorIs(t, err, repositories.ErrProductFolderMismatch)
	inherited, err := repositories.CreateHuntWithProduct(ctx, g, me, "From folder", &folder.ID, nil)
	require.NoError(t, err)
	_, err = repositories.AttachFolderProduct(ctx, g, me, folder.ID, unknown.ID)
	require.NoError(t, err)
	got, err = repositories.HuntFolderContext(ctx, g, inherited)
	require.NoError(t, err)
	require.Equal(t, product.ID, got.ReferenceProduct.ID)
	require.Empty(t, got.Brands)

	// Store links reuse the existing listing, including a source description.
	storeProduct, err := repositories.CreateResearchProduct(ctx, g, me, "https://apps.apple.com/app/id111", "", "", "")
	require.NoError(t, err)
	require.NotEmpty(t, storeProduct.Name)
	require.Equal(t, "111", storeProduct.StoreID)
	// Shared-domain pages remain ambiguous until the caller chooses one.
	require.NoError(t, g.Exec(`INSERT INTO frontend.ad_search_results(search_id,ad_archive_id,page_id,page_name,source_term,country,is_active,creative_hash,link_url)
      VALUES (?,? ,?,'Other brand','seed','US',true,'other',?)`, searchID, "other-"+tag, "991"+tag[:12], "https://product-"+tag+".example/another").Error)
	ambiguous, err := repositories.ResolveProductLink(ctx, g, me, "https://product-"+tag+".example")
	require.NoError(t, err)
	require.Len(t, ambiguous.Matches, 2)
	_, err = repositories.CreateResearchProduct(ctx, g, me, "https://product-"+tag+".example", "Guess", "", "")
	require.Error(t, err)

	// API routes validate links and keep product selection owner-scoped.
	r := router()
	request := func(method, path, body string) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+writeToken)
		req.Header.Set("X-User-Email", me)
		r.ServeHTTP(w, req)
		return w
	}
	require.Equal(t, 422, request("POST", "/api/products/resolve", `{"url":"javascript:bad"}`).Code)
	w := request("GET", "/api/products", "")
	require.Equal(t, http.StatusOK, w.Code)
	var list []models.ResearchProduct
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &list))
	require.Len(t, list, 3)
	require.Equal(t, 404, request("POST", fmt.Sprintf("/api/saved-groups/%d/update", folder.ID), `{"product_id":999999999}`).Code)
	require.Equal(t, 200, request("POST", fmt.Sprintf("/api/saved-groups/%d/update", folder.ID), `{"product_id":0}`).Code)
	var invalid *helpers.ProductInputError
	_, err = repositories.CreateResearchProduct(ctx, g, me, link, "", "", "123456789")
	require.ErrorAs(t, err, &invalid)
}

func TestMCPProductWorkflow(t *testing.T) {
	ctx := context.Background()
	email, key := seedKeyedUser(t, ctx, "admin")
	server := httptest.NewServer(router())
	defer server.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "product-integration", Version: "test"}, nil)
	sess, err := client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: server.URL + "/mcp", HTTPClient: mcpClientFor(key)}, nil)
	require.NoError(t, err)
	defer sess.Close()
	t.Cleanup(func() {
		db.DB().Exec(`DELETE FROM public.pipeline_jobs WHERE kind='hunt' AND advertiser_id IN (SELECT r.id FROM frontend.hunt_rounds r JOIN frontend.hunts h ON h.id=r.hunt_id WHERE h.user_email=?)`, email)
		db.DB().Exec(`DELETE FROM frontend.hunts WHERE user_email=?`, email)
		db.DB().Exec(`DELETE FROM frontend.research_products WHERE user_email=?`, email)
	})
	call := func(name string, args map[string]any) *mcp.CallToolResult {
		res, e := sess.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: args})
		require.NoError(t, e)
		require.False(t, res.IsError, "%s", firstText(res))
		return res
	}
	resolution := call("resolve_product_link", map[string]any{"url": "https://product-workflow.example"})
	require.Contains(t, firstText(resolution), "matches")
	created := call("add_product", map[string]any{"url": "https://product-workflow.example", "name": "Focus product", "description": "Reduce distracting phone use"})
	var p models.ResearchProduct
	require.NoError(t, json.Unmarshal([]byte(firstText(created)), &p))
	require.Positive(t, p.ID)
	saved := call("my_saved_gems", map[string]any{})
	require.Contains(t, firstText(saved), "Focus product")
	queued := call("run_hunt", map[string]any{"product_id": p.ID, "terms": []string{"screen time"}})
	var round struct {
		HuntID  int `json:"hunt_id"`
		RoundID int `json:"round_id"`
	}
	require.NoError(t, json.Unmarshal([]byte(firstText(queued)), &round))
	var stored struct {
		ProductID int64
		Intent    string
	}
	require.NoError(t, db.DB().Raw(`SELECT h.product_id,r.intent FROM frontend.hunts h JOIN frontend.hunt_rounds r ON r.hunt_id=h.id WHERE r.id=?`, round.RoundID).Scan(&stored).Error)
	require.Equal(t, p.ID, stored.ProductID)
	require.NotEmpty(t, stored.Intent)
}
