package repositories

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"gorm.io/gorm"
)

var ErrAdSearchConflict = errors.New("idempotency key already used for a different request")
var ErrAdSearchCapacity = errors.New("at most two active ad searches are allowed")

func CreateAdSearch(ctx context.Context, g *gorm.DB, owner string, request models.AdSearchRequest, key string, refresh bool) (*models.AdSearch, error) {
	owner = strings.ToLower(strings.TrimSpace(owner))
	payload, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	var result models.AdSearch
	err = db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		if err := tx.Exec(`SELECT pg_advisory_xact_lock(hashtextextended(?, 0))`, owner).Error; err != nil {
			return err
		}
		if key != "" {
			var rows []struct {
				ID   int64
				Same bool
			}
			if err := tx.Raw(`SELECT id, request = CAST(? AS jsonb) AS same FROM frontend.ad_searches WHERE created_by = ? AND idempotency_key = ?`, string(payload), owner, key).Scan(&rows).Error; err != nil {
				return err
			}
			if len(rows) > 0 {
				if !rows[0].Same {
					return ErrAdSearchConflict
				}
				return tx.Raw(`SELECT id, request, status, progress, error_text, created_at, started_at, finished_at FROM frontend.ad_searches WHERE id = ? AND created_by = ?`, rows[0].ID, owner).Scan(&result).Error
			}
		}
		if !refresh && key == "" {
			var cached []models.AdSearch
			if err := tx.Raw(`SELECT id,request,status,progress,error_text,created_at,started_at,finished_at
                FROM frontend.ad_searches WHERE created_by=? AND request=CAST(? AS jsonb)
                AND status='done' AND created_at>now()-interval '6 hours'
                AND COALESCE((progress->>'partial')::boolean,false)=false
                ORDER BY id DESC LIMIT 1`, owner, string(payload)).Scan(&cached).Error; err != nil {
				return err
			}
			if len(cached) > 0 {
				result = cached[0]
				return nil
			}
		}
		var count int64
		if err := tx.Raw(`SELECT count(*) FROM frontend.ad_searches WHERE created_by = ? AND status IN ('queued','running')`, owner).Scan(&count).Error; err != nil {
			return err
		}
		if count >= 2 {
			return ErrAdSearchCapacity
		}
		var keyValue any
		if key != "" {
			keyValue = key
		}
		if err := tx.Raw(`INSERT INTO frontend.ad_searches (created_by, request, idempotency_key) VALUES (?, CAST(? AS jsonb), ?) RETURNING id, request, status, progress, error_text, created_at, started_at, finished_at`, owner, string(payload), keyValue).Scan(&result).Error; err != nil {
			return err
		}
		return tx.Exec(`INSERT INTO public.pipeline_jobs (kind, advertiser_id) VALUES ('ad_search', ?)`, result.ID).Error
	})
	if err != nil {
		return nil, err
	}
	return &result, nil
}
func GetAdSearch(ctx context.Context, g *gorm.DB, owner string, id int64) (*models.AdSearch, error) {
	var rows []models.AdSearch
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT id, request, status, progress, error_text, created_at, started_at, finished_at FROM frontend.ad_searches WHERE id = ? AND created_by = ?`, id, strings.ToLower(strings.TrimSpace(owner))).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

func AdScrapeResults(ctx context.Context, g *gorm.DB, owner string, id int64, limit, offset int) ([]models.AdSearchResult, error) {
	rows := []models.AdSearchResult{}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT r.* FROM frontend.ad_search_results r JOIN frontend.ad_searches s ON s.id=r.search_id
    WHERE s.id=? AND s.created_by=? ORDER BY r.id LIMIT ? OFFSET ?`, id, strings.ToLower(strings.TrimSpace(owner)), limit, offset).Scan(&rows).Error
	})
	return rows, err
}

func AdScrapeCopy(ctx context.Context, g *gorm.DB, owner string, id int64, adID string) (*string, error) {
	var rows []struct{ Body string }
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT COALESCE(r.body_text,'') AS body FROM frontend.ad_search_results r
		JOIN frontend.ad_searches s ON s.id=r.search_id WHERE s.id=? AND s.created_by=? AND r.ad_archive_id=?`,
			id, strings.ToLower(strings.TrimSpace(owner)), adID).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0].Body, nil
}

func AdScrapeAd(ctx context.Context, g *gorm.DB, owner string, id int64, adID string) (*models.AdSearchResult, error) {
	var rows []models.AdSearchResult
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(`SELECT r.* FROM frontend.ad_search_results r JOIN frontend.ad_searches s ON s.id=r.search_id
			WHERE s.id=? AND s.created_by=? AND r.ad_archive_id=?`, id,
			strings.ToLower(strings.TrimSpace(owner)), adID).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}
