package repositories

import (
	"context"
	"encoding/json"

	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// CreateHunt inserts a new hunt thread for the owner and returns its id.
func CreateHunt(ctx context.Context, g *gorm.DB, email, title string) (int, error) {
	var id int
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`INSERT INTO frontend.hunts (user_email, title)
			 VALUES (?, ?) RETURNING id`,
			email, title,
		).Scan(&id).Error
	})
	return id, err
}

// HuntOwner returns the owner email for a hunt, or "" when it does not exist.
func HuntOwner(ctx context.Context, g *gorm.DB, huntID int) (string, error) {
	var emails []string
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT user_email FROM frontend.hunts WHERE id = ?`,
			huntID,
		).Scan(&emails).Error
	})
	if err != nil || len(emails) == 0 {
		return "", err
	}
	return emails[0], nil
}

// ListHunts returns the owner's hunts newest-first, each carrying the status of
// its most recent round (null when it has none yet).
func ListHunts(ctx context.Context, g *gorm.DB, email string) ([]models.HuntRow, error) {
	var rows []models.HuntRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT h.id, h.title, h.created_at,
			        (SELECT r.status FROM frontend.hunt_rounds r
			          WHERE r.hunt_id = h.id ORDER BY r.id DESC LIMIT 1) AS last_round_status
			 FROM frontend.hunts h
			 WHERE lower(h.user_email) = lower(?)
			 ORDER BY h.id DESC`,
			email,
		).Scan(&rows).Error
	})
	return rows, err
}

// InsertHuntMessage appends a transcript entry and returns its id. proposal is
// stored as jsonb; pass nil for user messages (persisted as SQL NULL).
func InsertHuntMessage(ctx context.Context, g *gorm.DB, huntID int, role, content string, proposal json.RawMessage) (int, error) {
	var id int
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`INSERT INTO frontend.hunt_messages (hunt_id, role, content, proposal)
			 VALUES (?, ?, ?, CAST(? AS jsonb)) RETURNING id`,
			huntID, role, content, jsonbArg(proposal),
		).Scan(&id).Error
	})
	return id, err
}

// ListHuntMessages returns a hunt's transcript oldest-first.
func ListHuntMessages(ctx context.Context, g *gorm.DB, huntID int) ([]models.HuntMessageRow, error) {
	var rows []models.HuntMessageRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT id, role, content, proposal, created_at
			 FROM frontend.hunt_messages
			 WHERE hunt_id = ?
			 ORDER BY id`,
			huntID,
		).Scan(&rows).Error
	})
	return rows, err
}

// ListHuntRounds returns a hunt's rounds oldest-first.
func ListHuntRounds(ctx context.Context, g *gorm.DB, huntID int) ([]models.HuntRoundRow, error) {
	var rows []models.HuntRoundRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT id, message_id, terms, competitor_apps, status, progress,
			        error_text, created_at, finished_at
			 FROM frontend.hunt_rounds
			 WHERE hunt_id = ?
			 ORDER BY id`,
			huntID,
		).Scan(&rows).Error
	})
	return rows, err
}

// ListHuntResults returns every result across a hunt's rounds, joining the kept
// advertiser's GemScore (null-safe) at read time.
func ListHuntResults(ctx context.Context, g *gorm.DB, huntID int) ([]models.HuntResultRow, error) {
	var rows []models.HuntResultRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT hr.id, hr.round_id, hr.page_id, hr.page_name, hr.store, hr.store_id,
			        hr.link_url, hr.source_term, hr.was_new, hr.triage, hr.kept_advertiser_id,
			        s.gem_score
			 FROM frontend.hunt_results hr
			 JOIN frontend.hunt_rounds r ON r.id = hr.round_id
			 LEFT JOIN adtech.advertiser_scores s ON s.advertiser_id = hr.kept_advertiser_id
			 WHERE r.hunt_id = ?
			 ORDER BY hr.id`,
			huntID,
		).Scan(&rows).Error
	})
	return rows, err
}

// InsertHuntRound inserts a queued round and enqueues its pipeline job in a
// single write transaction: the job's advertiser_id carries the ROUND id (the
// hunt worker daemon drains kind='hunt' jobs). The partial unique index on
// (kind, advertiser_id) WHERE status IN ('queued','running') makes the enqueue
// idempotent. messageID may be nil (SQL NULL).
func InsertHuntRound(ctx context.Context, g *gorm.DB, huntID int, messageID *int, terms, competitors json.RawMessage) (int, error) {
	var roundID int
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		if err := tx.Raw(
			`INSERT INTO frontend.hunt_rounds (hunt_id, message_id, terms, competitor_apps)
			 VALUES (?, ?, CAST(? AS jsonb), CAST(? AS jsonb)) RETURNING id`,
			huntID, messageID, jsonbArg(terms), jsonbArg(competitors),
		).Scan(&roundID).Error; err != nil {
			return err
		}
		return tx.Exec(
			`INSERT INTO public.pipeline_jobs (kind, advertiser_id)
			 VALUES ('hunt', ?)
			 ON CONFLICT (kind, advertiser_id) WHERE status IN ('queued','running')
			 DO NOTHING`,
			roundID,
		).Error
	})
	return roundID, err
}

// HuntRoundSummaries returns per-round term/found/kept counts (oldest-first) for
// the Gemini proposal context so the model can avoid repeating angles.
func HuntRoundSummaries(ctx context.Context, g *gorm.DB, huntID int) ([]models.HuntRoundSummary, error) {
	var rows []models.HuntRoundSummary
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT r.id AS round_id,
			        r.terms AS terms,
			        COALESCE(jsonb_array_length(r.terms), 0) AS term_count,
			        (SELECT count(*) FROM frontend.hunt_results hr
			          WHERE hr.round_id = r.id) AS found_count,
			        (SELECT count(*) FROM frontend.hunt_results hr
			          WHERE hr.round_id = r.id AND hr.triage = 'kept') AS kept_count
			 FROM frontend.hunt_rounds r
			 WHERE r.hunt_id = ?
			 ORDER BY r.id`,
			huntID,
		).Scan(&rows).Error
	})
	return rows, err
}

// jsonbArg maps a nil json.RawMessage to a typed nil so CAST(? AS jsonb)
// receives SQL NULL rather than an empty string (which is not valid jsonb).
func jsonbArg(raw json.RawMessage) any {
	if len(raw) == 0 {
		return nil
	}
	return string(raw)
}
