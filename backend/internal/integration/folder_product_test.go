package integration

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"github.com/stretchr/testify/require"
)

func TestFolderProductAndHuntContext(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	t.Setenv("GEMINI_API_KEY", "folder-test-unused-key")
	ctx, g := context.Background(), db.DB()
	me := fmt.Sprintf("folder-product-%d@test.local", time.Now().UnixNano())
	other := "other-" + me
	require.NoError(t, repositories.UpsertUser(ctx, g, me, "unused", "Folder test", "admin"))
	t.Cleanup(func() {
		g.Exec(`DELETE FROM frontend.hunts WHERE user_email IN (?,?)`, me, other)
		g.Exec(`DELETE FROM frontend.saved_groups WHERE user_email IN (?,?)`, me, other)
		g.Exec(`DELETE FROM frontend.saved_gems WHERE user_email IN (?,?)`, me, other)
	})
	folder, err := repositories.CreateSavedGroup(ctx, g, me, "Ad references")
	require.NoError(t, err)
	foreign, err := repositories.CreateSavedGroup(ctx, g, other, "Private references")
	require.NoError(t, err)
	r := router()
	post := func(path, body string) *httptest.ResponseRecorder {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+writeToken)
		req.Header.Set("X-User-Email", me)
		r.ServeHTTP(w, req)
		return w
	}
	path := fmt.Sprintf("/api/saved-groups/%d/update", folder.ID)
	require.Equal(t, 200, post(path, `{"product":"  Quizlock — manage distracting phone use  "}`).Code)
	rows, err := repositories.ListSavedGroups(ctx, g, me)
	require.NoError(t, err)
	require.Equal(t, "Quizlock — manage distracting phone use", rows[0].Product)
	require.Equal(t, 200, post(path, `{"name":"Selected brands"}`).Code)
	require.Equal(t, 422, post(path, `{"product":"`+strings.Repeat("a", 801)+`"}`).Code)
	require.Equal(t, 404, post(fmt.Sprintf("/api/saved-groups/%d/update", foreign.ID), `{"product":"stolen"}`).Code)
	require.Equal(t, 404, post("/api/hunts", fmt.Sprintf(`{"message":"Find ads","folder_id":%d}`, foreign.ID)).Code)
	_, err = repositories.CreateHuntInFolder(ctx, g, me, "Forbidden", &foreign.ID)
	require.ErrorIs(t, err, repositories.ErrFolderNotFound)
	require.NoError(t, g.Exec(`INSERT INTO frontend.saved_gems(user_email,advertiser_id,page_name,gem_score)
		VALUES (?,?,'Selected reference',1),(?,?,'Private reference',1)`, me, advID, other, adv2ID).Error)
	_, err = repositories.AddSavedGroupMember(ctx, g, me, folder.ID, int64(advID))
	require.NoError(t, err)
	// Even malformed cross-owner membership cannot enter the evidence view.
	require.NoError(t, g.Exec(`INSERT INTO frontend.saved_group_members(user_email,group_id,advertiser_id)
		VALUES (?,?,?)`, other, folder.ID, adv2ID).Error)
	huntID, err := repositories.CreateHuntInFolder(ctx, g, me, "Find ads", &folder.ID)
	require.NoError(t, err)
	got, err := repositories.HuntFolderContext(ctx, g, huntID)
	require.NoError(t, err)
	require.Equal(t, "Quizlock — manage distracting phone use", got.Product)
	require.Equal(t, "Selected brands", got.Name)
	require.Len(t, got.Brands, 1)
	require.Equal(t, int64(advID), got.Brands[0].AdvertiserID)
	private, err := repositories.SavedFolderContext(ctx, g, me, foreign.ID)
	require.NoError(t, err)
	require.Nil(t, private)
	saved, err := repositories.MySavedGems(ctx, g, me)
	require.NoError(t, err)
	require.Len(t, saved, 1)
	require.Equal(t, []int64{folder.ID}, []int64(saved[0].GroupIDs))
	hunts, err := repositories.ListHunts(ctx, g, me)
	require.NoError(t, err)
	require.Len(t, hunts, 1)
	require.Equal(t, folder.ID, *hunts[0].FolderID)
	require.Equal(t, 200, post(path, `{"product":"Another product"}`).Code)
	got, err = repositories.HuntFolderContext(ctx, g, huntID)
	require.NoError(t, err)
	require.Equal(t, "Another product", got.Product)
	require.Equal(t, 200, post(path, `{"product":""}`).Code)
	got, err = repositories.HuntFolderContext(ctx, g, huntID)
	require.NoError(t, err)
	require.Empty(t, got.Product)
	_, err = repositories.DeleteSavedGroup(ctx, g, me, folder.ID)
	require.NoError(t, err)
	got, err = repositories.HuntFolderContext(ctx, g, huntID)
	require.NoError(t, err)
	require.Nil(t, got)
	hunts, err = repositories.ListHunts(ctx, g, me)
	require.NoError(t, err)
	require.Len(t, hunts, 1)
	require.Nil(t, hunts[0].FolderID)
}
