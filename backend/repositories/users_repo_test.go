// Package repositories_test exercises users_repo.go OAuth/profile functions
// against a real Postgres database. Skipped unless AGF_IT_DB_URL is set.
package repositories_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// ratingsDB and must come from ratings_repo_test.go (the package's one TestMain).

func seedUsers(t *testing.T) {
	t.Helper()
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	must(ratingsDB.Exec(`DELETE FROM frontend.api_keys WHERE user_email LIKE 'oauth-test-%@t.local'`).Error)
	must(ratingsDB.Exec(`DELETE FROM frontend.users WHERE email LIKE 'oauth-test-%@t.local'`).Error)
}

func TestOAuthUpsertUser_InsertsNewMember(t *testing.T) {
	seedUsers(t)
	ctx := context.Background()

	p, err := repositories.OAuthUpsertUser(ctx, ratingsDB, "OAuth-Test-New@t.local", "New User", "google", "https://a/img.png")
	require.NoError(t, err)
	require.Equal(t, "oauth-test-new@t.local", p.Email) // normalized lowercase
	require.Equal(t, "member", p.Role)
	require.Equal(t, "google", p.AuthProvider)
	require.NotNil(t, p.AvatarURL)
	require.Nil(t, p.OnboardedAt) // new users must go through onboarding
}

func TestOAuthUpsertUser_ConflictKeepsProviderAndRole(t *testing.T) {
	seedUsers(t)
	ctx := context.Background()
	// Existing password-provisioned admin.
	must(ratingsDB.Exec(`INSERT INTO frontend.users (email, password_hash, name, role, onboarded_at)
		VALUES ('oauth-test-admin@t.local', 'x', 'Old Name', 'admin', now())`).Error)

	p, err := repositories.OAuthUpsertUser(ctx, ratingsDB, "oauth-test-admin@t.local", "Fresh Name", "github", "")
	require.NoError(t, err)
	require.Equal(t, "admin", p.Role)               // role never clobbered
	require.Equal(t, "password", p.AuthProvider)     // provider set once at insert
	require.Equal(t, "Fresh Name", p.Name)           // name refreshed
	require.NotNil(t, p.OnboardedAt)                 // existing user stays onboarded
}

func TestMarkOnboarded_IdempotentAndProfileRoundTrip(t *testing.T) {
	seedUsers(t)
	ctx := context.Background()
	_, err := repositories.OAuthUpsertUser(ctx, ratingsDB, "oauth-test-ob@t.local", "Ob", "github", "")
	require.NoError(t, err)

	require.NoError(t, repositories.MarkOnboarded(ctx, ratingsDB, "oauth-test-ob@t.local"))
	p1, err := repositories.GetUserProfile(ctx, ratingsDB, "oauth-test-ob@t.local")
	require.NoError(t, err)
	require.NotNil(t, p1.OnboardedAt)

	// Second call must not error or move the timestamp.
	require.NoError(t, repositories.MarkOnboarded(ctx, ratingsDB, "oauth-test-ob@t.local"))
	p2, err := repositories.GetUserProfile(ctx, ratingsDB, "oauth-test-ob@t.local")
	require.NoError(t, err)
	require.Equal(t, p1.OnboardedAt.UTC(), p2.OnboardedAt.UTC())
}

func TestGetUserProfile_NotFound(t *testing.T) {
	seedUsers(t)
	p, err := repositories.GetUserProfile(context.Background(), ratingsDB, "oauth-test-missing@t.local")
	require.NoError(t, err)
	require.Nil(t, p)
}

func TestListAPIKeys_KeylessUserIsEmptyNotNil(t *testing.T) {
	seedUsers(t)
	keys, err := repositories.ListAPIKeys(context.Background(), ratingsDB, "oauth-test-nokeys@t.local")
	require.NoError(t, err)
	require.NotNil(t, keys) // nil serializes as JSON null and crashes /settings
	require.Empty(t, keys)
}
