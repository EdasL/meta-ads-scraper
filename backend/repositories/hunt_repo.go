package repositories

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

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

// DeleteHunt removes a hunt thread and all of its messages, rounds, and results
// (ON DELETE CASCADE). The caller must have already verified ownership.
func DeleteHunt(ctx context.Context, g *gorm.DB, huntID int) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(`DELETE FROM frontend.hunts WHERE id = ?`, huntID).Error
	})
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

// ListHunts returns the owner's hunts most-recently-active first, each carrying
// the status of its most recent round (null when it has none yet) and its
// last-activity time (the newest of the thread, its messages, and its rounds —
// so a hunt jumps to the top when Peon replies or a round runs, not just when it
// was created).
func ListHunts(ctx context.Context, g *gorm.DB, email string) ([]models.HuntRow, error) {
	var rows []models.HuntRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT h.id, h.title, h.folder_id, h.product_id, h.created_at,
			        GREATEST(
			          h.created_at,
			          COALESCE((SELECT max(m.created_at) FROM frontend.hunt_messages m
			                     WHERE m.hunt_id = h.id), h.created_at),
			          COALESCE((SELECT max(r.created_at) FROM frontend.hunt_rounds r
			                     WHERE r.hunt_id = h.id), h.created_at)
			        ) AS last_activity,
			        (SELECT r.status FROM frontend.hunt_rounds r
			          WHERE r.hunt_id = h.id ORDER BY r.id DESC LIMIT 1) AS last_round_status
			 FROM frontend.hunts h
			 WHERE lower(h.user_email) = lower(?)
			 ORDER BY last_activity DESC, h.id DESC`,
			email,
		).Scan(&rows).Error
	})
	return rows, err
}

// InsertHuntMessage appends a transcript entry and returns its id. proposal and
// toolLog are stored as jsonb; pass nil for either (persisted as SQL NULL —
// user messages carry no proposal, non-tool turns carry no tool_log).
func InsertHuntMessage(ctx context.Context, g *gorm.DB, huntID int, role, content string, proposal, toolLog json.RawMessage) (int, error) {
	var id int
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		var e error
		id, e = InsertHuntMessageTx(tx, huntID, role, content, proposal, toolLog)
		return e
	})
	return id, err
}

// InsertHuntMessageTx is InsertHuntMessage's body against a caller-supplied
// transaction handle, so an assistant message and its round can commit atomically
// in one outer WithWriteTx. tx already carries the request context.
func InsertHuntMessageTx(tx *gorm.DB, huntID int, role, content string, proposal, toolLog json.RawMessage) (int, error) {
	var id int
	err := tx.Raw(
		`INSERT INTO frontend.hunt_messages (hunt_id, role, content, proposal, tool_log)
		 VALUES (?, ?, ?, CAST(? AS jsonb), CAST(? AS jsonb)) RETURNING id`,
		huntID, role, content, jsonbArg(proposal), jsonbArg(toolLog),
	).Scan(&id).Error
	return id, err
}

// ListHuntMessages returns a hunt's transcript oldest-first.
func ListHuntMessages(ctx context.Context, g *gorm.DB, huntID int) ([]models.HuntMessageRow, error) {
	var rows []models.HuntMessageRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT id, role, content, proposal, tool_log, created_at
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
			        settings, error_text, created_at, finished_at
			 FROM frontend.hunt_rounds
			 WHERE hunt_id = ?
			 ORDER BY id`,
			huntID,
		).Scan(&rows).Error
	})
	return rows, err
}

// ListHuntResults returns every result across a hunt's rounds, joined at read
// time with the kept advertiser's GemScore, its Discover-feed app data
// (icon/tagline/cohort), the SERP page depth, and the CALLER's saved/rating
// state (email-scoped — another user's saves never leak). Icon and tagline
// fall back from the matview to public.apps (matched on the result's own
// store/store_id) so keepers show media as soon as triage promotes them,
// without waiting for the next matview refresh.
func ListHuntResults(ctx context.Context, g *gorm.DB, huntID int, email string) ([]models.HuntResultRow, error) {
	var rows []models.HuntResultRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT hr.id, hr.round_id, hr.page_id, hr.page_name, hr.store, hr.store_id,
			        hr.link_url, hr.source_term, hr.was_new, hr.triage,
			        eff.adv_id AS kept_advertiser_id,
			        hr.relevance, hr.relevance_note, hr.ad_evidence, hr.page,
			        s.gem_score,
			        esa.app_name,
			        COALESCE(esa.icon_url, papp.icon_url) AS icon_url,
			        COALESCE(esa.tagline, nullif(left(regexp_replace(
			            split_part(papp.description, E'\n', 1), '\s+', ' ', 'g'
			        ), 200), '')) AS tagline,
			        esa.cohort,
			        (sg.advertiser_id IS NOT NULL) AS saved,
			        ar.rating
			 FROM frontend.hunt_results hr
			 JOIN frontend.hunt_rounds r ON r.id = hr.round_id
			 LEFT JOIN LATERAL (
			     SELECT COALESCE(
			                hr.kept_advertiser_id,
			                (SELECT a.id FROM public.advertisers a
			                  WHERE a.page_id = hr.page_id LIMIT 1)
			            ) AS adv_id
			 ) eff ON true
			 LEFT JOIN adtech.advertiser_scores s ON s.advertiser_id = eff.adv_id
			 LEFT JOIN frontend.emerging_scaling_apps esa ON esa.advertiser_id = eff.adv_id
			 LEFT JOIN public.apps papp ON papp.store = hr.store AND papp.store_id = hr.store_id
			 LEFT JOIN frontend.saved_gems sg
			        ON sg.advertiser_id = eff.adv_id AND lower(sg.user_email) = lower(?)
			 LEFT JOIN frontend.app_ratings ar
			        ON ar.advertiser_id = eff.adv_id AND lower(ar.user_email) = lower(?)
			 WHERE r.hunt_id = ?
			 ORDER BY hr.id`,
			email, email, huntID,
		).Scan(&rows).Error
	})
	return rows, err
}

// RoundReport returns the tool-facing status digest for one round, scoped to
// its hunt (r.hunt_id = ?). It returns (nil, nil) when the round does not exist
// under that hunt — the tool executor turns that into an error string. Triage
// counts are aggregated from hunt_results with FILTER.
func RoundReport(ctx context.Context, g *gorm.DB, huntID, roundID int) (*models.HuntRoundReport, error) {
	var rows []models.HuntRoundReport
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			// progress ships to the model as a compact digest: strip the verbose
			// 'events' log and 'lanes' map (I1), keeping 'terms' and any counters.
			`SELECT r.status, r.error_text, r.started_at, r.finished_at,
			        r.settings, (r.progress - 'events' - 'lanes') AS progress,
			        count(hr.id) FILTER (WHERE hr.triage = 'kept')    AS kept_count,
			        count(hr.id) FILTER (WHERE hr.triage = 'vetoed')  AS vetoed_count,
			        count(hr.id) FILTER (WHERE hr.triage = 'pending') AS pending_count,
			        count(hr.id) FILTER (WHERE hr.relevance = 'off_target') AS off_target_count
			 FROM frontend.hunt_rounds r
			 LEFT JOIN frontend.hunt_results hr ON hr.round_id = r.id
			 WHERE r.id = ? AND r.hunt_id = ?
			 GROUP BY r.id, r.status, r.error_text, r.started_at, r.finished_at,
			          r.settings, r.progress`,
			roundID, huntID,
		).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

// RoundResults returns a single round's results (scoped to its hunt via the
// hunt_rounds JOIN), optionally filtered to one triage state, capped at limit
// and starting at offset. It mirrors ListHuntResults' GemScore join. Ordering is
// by hr.id, which is stable across calls, so paging cannot skip or repeat a row.
func RoundResults(ctx context.Context, g *gorm.DB, huntID, roundID int, triage string, limit, offset int) ([]models.HuntResultRow, error) {
	var rows []models.HuntResultRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT hr.id, hr.round_id, hr.page_id, hr.page_name, hr.store, hr.store_id,
			        hr.link_url, hr.source_term, hr.was_new, hr.triage, hr.kept_advertiser_id,
			        hr.relevance, hr.relevance_note, hr.ad_evidence,
			        s.gem_score
			 FROM frontend.hunt_results hr
			 JOIN frontend.hunt_rounds r ON r.id = hr.round_id
			 LEFT JOIN adtech.advertiser_scores s ON s.advertiser_id = hr.kept_advertiser_id
			 WHERE r.hunt_id = ? AND hr.round_id = ?
			   AND (? = '' OR hr.triage = ?)
			 ORDER BY hr.id
			 LIMIT ? OFFSET ?`,
			huntID, roundID, triage, triage, limit, offset,
		).Scan(&rows).Error
	})
	return rows, err
}

// InsertHuntRound inserts a queued round and enqueues its pipeline job in a
// single write transaction: the job's advertiser_id carries the ROUND id (the
// hunt worker daemon drains kind='hunt' jobs). The partial unique index on
// (kind, advertiser_id) WHERE status IN ('queued','running') makes the enqueue
// idempotent. messageID may be nil (SQL NULL).
func InsertHuntRound(ctx context.Context, g *gorm.DB, huntID int, messageID *int, terms, competitors, settings json.RawMessage, intent *string) (int, error) {
	var roundID int
	err := db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		var e error
		roundID, e = InsertHuntRoundTx(tx, huntID, messageID, terms, competitors, settings, intent)
		return e
	})
	return roundID, err
}

// InsertHuntRoundTx is InsertHuntRound's body against a caller-supplied
// transaction handle, so the round insert + pipeline-job enqueue share the outer
// txn that also wrote the assistant message. tx already carries the request
// context.
func InsertHuntRoundTx(tx *gorm.DB, huntID int, messageID *int, terms, competitors, settings json.RawMessage, intent *string) (int, error) {
	var roundID int
	if err := tx.Raw(
		`INSERT INTO frontend.hunt_rounds (hunt_id, message_id, terms, competitor_apps, settings, intent)
		 VALUES (?, ?, CAST(? AS jsonb), CAST(? AS jsonb), CAST(? AS jsonb), ?) RETURNING id`,
		// terms and competitor_apps are NOT NULL: default nil/empty to an
		// empty JSON array. settings is NOT NULL DEFAULT '{}': default
		// nil/empty to an empty JSON object. intent is nullable (nil → SQL NULL).
		huntID, messageID, jsonbArgDefault(terms), jsonbArgDefault(competitors), jsonbArgDefaultObj(settings), intent,
	).Scan(&roundID).Error; err != nil {
		return 0, err
	}
	if err := tx.Exec(
		`INSERT INTO public.pipeline_jobs (kind, advertiser_id)
		 VALUES ('hunt', ?)
		 ON CONFLICT (kind, advertiser_id) WHERE status IN ('queued','running')
		 DO NOTHING`,
		roundID,
	).Error; err != nil {
		return 0, err
	}
	return roundID, nil
}

// HuntRoundSummaries returns per-round term/found/kept/relevance counts
// (oldest-first) for the Gemini proposal context so the model can avoid
// repeating angles and learn which rounds produced on-target results.
func HuntRoundSummaries(ctx context.Context, g *gorm.DB, huntID int) ([]models.HuntRoundSummary, error) {
	var rows []models.HuntRoundSummary
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT r.id AS round_id,
			        r.terms AS terms,
			        r.settings AS settings,
			        r.progress AS progress,
			        COALESCE(jsonb_array_length(r.terms), 0) AS term_count,
			        (SELECT count(*) FROM frontend.hunt_results hr
			          WHERE hr.round_id = r.id) AS found_count,
			        (SELECT count(*) FROM frontend.hunt_results hr
			          WHERE hr.round_id = r.id AND hr.triage = 'kept') AS kept_count,
			        (SELECT count(*) FROM frontend.hunt_results hr
			          WHERE hr.round_id = r.id AND hr.relevance = 'relevant') AS relevant_count,
			        (SELECT count(*) FROM frontend.hunt_results hr
			          WHERE hr.round_id = r.id AND hr.relevance = 'off_target') AS off_target_count
			 FROM frontend.hunt_rounds r
			 WHERE r.hunt_id = ?
			 ORDER BY r.id`,
			huntID,
		).Scan(&rows).Error
	})
	return rows, err
}

// PriorHuntLines summarizes the owner's OTHER hunts for Peon's context: newest
// rounds first, one compact line each, capped so the prompt stays small. It is
// scoped to h.user_email (case-insensitive, matching the rest of the hunt reads)
// and excludes the current hunt so the model does not echo its own thread back.
func PriorHuntLines(ctx context.Context, g *gorm.DB, email string, excludeHuntID int) ([]string, error) {
	var rows []struct {
		HuntID  int             `gorm:"column:hunt_id"`
		Title   string          `gorm:"column:title"`
		RoundID int             `gorm:"column:round_id"`
		Terms   json.RawMessage `gorm:"column:terms"`
		Status  string          `gorm:"column:status"`
		Found   int             `gorm:"column:found"`
		Kept    int             `gorm:"column:kept"`
	}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT h.id AS hunt_id, h.title, r.id AS round_id, r.terms, r.status,
			        (SELECT count(*) FROM frontend.hunt_results hr WHERE hr.round_id = r.id) AS found,
			        (SELECT count(*) FROM frontend.hunt_results hr
			          WHERE hr.round_id = r.id AND hr.triage = 'kept') AS kept
			 FROM frontend.hunt_rounds r
			 JOIN frontend.hunts h ON h.id = r.hunt_id
			 WHERE lower(h.user_email) = lower(?) AND h.id <> ?
			 ORDER BY r.id DESC
			 LIMIT 50`,
			email, excludeHuntID,
		).Scan(&rows).Error
	})
	if err != nil {
		return nil, err
	}
	var out []string
	for _, r := range rows {
		var terms []string
		if len(r.Terms) > 0 {
			_ = json.Unmarshal(r.Terms, &terms)
		}
		out = append(out, fmt.Sprintf(
			"hunt %d (%s) round %d [%s]: terms: %s — %d found, %d kept",
			r.HuntID, r.Title, r.RoundID, r.Status, strings.Join(terms, ", "), r.Found, r.Kept))
	}
	return out, nil
}

// HuntForRound resolves a round to its hunt id and owner email, for the
// internal round-completion hook. Returns (0, "", nil) when the round does not
// exist — the caller 404s without leaking existence semantics.
func HuntForRound(ctx context.Context, g *gorm.DB, roundID int) (int, string, error) {
	var rows []struct {
		HuntID int    `gorm:"column:hunt_id"`
		Email  string `gorm:"column:user_email"`
	}
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT r.hunt_id, h.user_email
			 FROM frontend.hunt_rounds r
			 JOIN frontend.hunts h ON h.id = r.hunt_id
			 WHERE r.id = ?`,
			roundID,
		).Scan(&rows).Error
	})
	if err != nil || len(rows) == 0 {
		return 0, "", err
	}
	return rows[0].HuntID, rows[0].Email, nil
}

// jsonbArg maps a nil json.RawMessage to a typed nil so CAST(? AS jsonb)
// receives SQL NULL rather than an empty string (which is not valid jsonb).
func jsonbArg(raw json.RawMessage) any {
	if len(raw) == 0 {
		return nil
	}
	return string(raw)
}

// jsonbArgDefault is jsonbArg for NOT NULL jsonb columns: a nil/empty
// json.RawMessage becomes an empty JSON array ("[]") rather than SQL NULL.
func jsonbArgDefault(raw json.RawMessage) any {
	if len(raw) == 0 {
		return "[]"
	}
	return string(raw)
}

// jsonbArgDefaultObj is jsonbArgDefault for NOT NULL jsonb columns whose default
// is an empty JSON object ("{}") — e.g. hunt_rounds.settings.
func jsonbArgDefaultObj(raw json.RawMessage) any {
	if len(raw) == 0 {
		return "{}"
	}
	return string(raw)
}

// HuntTermYield tallies hunt outcomes per search term. With roundID nil it
// aggregates every round, which is the "which terms have ever worked" read;
// with a round id it scopes to that run. Ordered by keepers so the useful
// terms sort to the top.
func HuntTermYield(ctx context.Context, g *gorm.DB, roundID *int, limit, offset int) ([]models.HuntTermYieldRow, error) {
	var rows []models.HuntTermYieldRow
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT source_term,
			        count(DISTINCT round_id)                          AS rounds,
			        count(*)                                          AS found,
			        count(*) FILTER (WHERE triage = 'kept')           AS kept,
			        count(*) FILTER (WHERE relevance = 'relevant')    AS relevant,
			        count(*) FILTER (WHERE relevance = 'off_target')  AS off_target
			   FROM frontend.hunt_results
			  -- The casts are load-bearing, not decoration: gorm sends a nil
			  -- *int as an untyped NULL and Postgres rejects a bare "$1 IS NULL"
			  -- with 42P18. round_id is bigint, so int4 would be the wrong width.
			  WHERE (CAST(? AS bigint) IS NULL OR round_id = CAST(? AS bigint))
			  GROUP BY source_term
			  ORDER BY kept DESC, found DESC, source_term
			  LIMIT ? OFFSET ?`,
			roundID, roundID, limit, offset,
		).Scan(&rows).Error
	})
	return rows, err
}
