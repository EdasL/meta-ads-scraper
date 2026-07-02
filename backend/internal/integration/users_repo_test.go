package integration

import (
	"context"
	"testing"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
	"golang.org/x/crypto/bcrypt"
)

func TestAPIKeyLifecycle(t *testing.T) {
	ctx := context.Background()
	g := db.DB()
	hash, _ := bcrypt.GenerateFromPassword([]byte("pw-test"), bcrypt.DefaultCost)
	if err := repositories.UpsertUser(ctx, g, "it-user@test.local", string(hash), "IT User", "member"); err != nil {
		t.Fatal(err)
	}
	// Keys accumulate per run (each CreateAPIKey mints a new row); start from a
	// clean slate so the count/label assertions are deterministic across re-runs.
	if err := g.Exec(`DELETE FROM frontend.api_keys WHERE user_email = ?`, "it-user@test.local").Error; err != nil {
		t.Fatal(err)
	}

	plain, err := repositories.CreateAPIKey(ctx, g, "it-user@test.local", "laptop")
	if err != nil || len(plain) < 20 || plain[:3] != "fd_" {
		t.Fatalf("bad key: %q err=%v", plain, err)
	}

	ku, err := repositories.LookupAPIKey(ctx, g, repositories.HashAPIKey(plain))
	if err != nil || ku == nil || ku.Email != "it-user@test.local" || ku.Role != "member" {
		t.Fatalf("lookup: %+v err=%v", ku, err)
	}
	if ku, _ := repositories.LookupAPIKey(ctx, g, repositories.HashAPIKey("fd_wrong")); ku != nil {
		t.Fatal("unknown key must resolve to nil")
	}

	keys, err := repositories.ListAPIKeys(ctx, g, "it-user@test.local")
	if err != nil || len(keys) != 1 || keys[0].Label != "laptop" {
		t.Fatalf("list: %+v err=%v", keys, err)
	}

	if err := repositories.RevokeAPIKey(ctx, g, "it-user@test.local", keys[0].KeyHash); err != nil {
		t.Fatal(err)
	}
	if ku, _ := repositories.LookupAPIKey(ctx, g, repositories.HashAPIKey(plain)); ku != nil {
		t.Fatal("revoked key must resolve to nil")
	}
}

func TestVerifyLogin(t *testing.T) {
	ctx := context.Background()
	g := db.DB()
	hash, _ := bcrypt.GenerateFromPassword([]byte("right-pw"), bcrypt.DefaultCost)
	if err := repositories.UpsertUser(ctx, g, "login@test.local", string(hash), "Login", "admin"); err != nil {
		t.Fatal(err)
	}
	ku, name, err := repositories.VerifyLogin(ctx, g, "login@test.local", "right-pw")
	if err != nil || ku == nil || ku.Role != "admin" || name != "Login" {
		t.Fatalf("good login failed: %+v %q %v", ku, name, err)
	}
	if ku, _, _ := repositories.VerifyLogin(ctx, g, "login@test.local", "wrong"); ku != nil {
		t.Fatal("wrong password must fail")
	}
	if ku, _, _ := repositories.VerifyLogin(ctx, g, "ghost@test.local", "x"); ku != nil {
		t.Fatal("unknown user must fail")
	}
}
