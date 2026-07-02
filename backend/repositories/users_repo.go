// Package repositories: accounts + personal API keys (frontend.users/api_keys).
package repositories

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"math/big"
	"time"

	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
)

type KeyUser struct {
	Email string
	Role  string
}

type APIKeyInfo struct {
	KeyHash    string     `json:"key_hash"`
	Label      string     `json:"label"`
	CreatedAt  time.Time  `json:"created_at"`
	LastUsedAt *time.Time `json:"last_used_at"`
	RevokedAt  *time.Time `json:"revoked_at"`
}

const base62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

func HashAPIKey(plaintext string) string {
	sum := sha256.Sum256([]byte(plaintext))
	return hex.EncodeToString(sum[:])
}

func NewAPIKeyPlaintext() (string, error) {
	out := make([]byte, 43)
	for i := range out {
		n, err := rand.Int(rand.Reader, big.NewInt(62))
		if err != nil {
			return "", err
		}
		out[i] = base62[n.Int64()]
	}
	return "fd_" + string(out), nil
}

func LookupAPIKey(ctx context.Context, g *gorm.DB, keyHash string) (*KeyUser, error) {
	var rows []KeyUser
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT u.email, u.role
		               FROM frontend.api_keys k
		               JOIN frontend.users u ON u.email = k.user_email
		               WHERE k.key_hash = ? AND k.revoked_at IS NULL`, keyHash).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

// TouchAPIKey updates last_used_at in the background; failures are ignored on
// purpose (liveness metadata must never fail a request).
func TouchAPIKey(g *gorm.DB, keyHash string) {
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
			return tx.Exec(`UPDATE frontend.api_keys SET last_used_at = now() WHERE key_hash = ?`, keyHash).Error
		})
	}()
}

func CreateAPIKey(ctx context.Context, g *gorm.DB, email, label string) (string, error) {
	plain, err := NewAPIKeyPlaintext()
	if err != nil {
		return "", err
	}
	err = db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(`INSERT INTO frontend.api_keys (key_hash, user_email, label) VALUES (?, ?, ?)`,
			HashAPIKey(plain), email, label).Error
	})
	if err != nil {
		return "", err
	}
	return plain, nil
}

func ListAPIKeys(ctx context.Context, g *gorm.DB, email string) ([]APIKeyInfo, error) {
	var rows []APIKeyInfo
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT key_hash, label, created_at, last_used_at, revoked_at
		               FROM frontend.api_keys WHERE user_email = ? ORDER BY created_at DESC`, email).Scan(&rows).Error
	})
	return rows, err
}

func RevokeAPIKey(ctx context.Context, g *gorm.DB, email, keyHash string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(`UPDATE frontend.api_keys SET revoked_at = now()
		                WHERE key_hash = ? AND user_email = ? AND revoked_at IS NULL`, keyHash, email).Error
	})
}

// dummyHash keeps VerifyLogin's timing flat when the user does not exist.
var dummyHash, _ = bcrypt.GenerateFromPassword([]byte("fielddesk-dummy"), bcrypt.DefaultCost)

func VerifyLogin(ctx context.Context, g *gorm.DB, email, password string) (*KeyUser, string, error) {
	var rows []struct {
		Email        string
		Role         string
		Name         *string
		PasswordHash string
	}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT email, role, name, password_hash FROM frontend.users
		               WHERE lower(email) = lower(?)`, email).Scan(&rows).Error
	})
	if err != nil {
		return nil, "", err
	}
	if len(rows) == 0 {
		_ = bcrypt.CompareHashAndPassword(dummyHash, []byte(password))
		return nil, "", nil
	}
	if bcrypt.CompareHashAndPassword([]byte(rows[0].PasswordHash), []byte(password)) != nil {
		return nil, "", nil
	}
	name := rows[0].Email
	if rows[0].Name != nil && *rows[0].Name != "" {
		name = *rows[0].Name
	}
	return &KeyUser{Email: rows[0].Email, Role: rows[0].Role}, name, nil
}

func UpsertUser(ctx context.Context, g *gorm.DB, email, passwordHash, name, role string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(`INSERT INTO frontend.users (email, password_hash, name, role)
		                VALUES (?, ?, ?, ?)
		                ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash,
		                  name = EXCLUDED.name, role = EXCLUDED.role`,
			email, passwordHash, name, role).Error
	})
}
