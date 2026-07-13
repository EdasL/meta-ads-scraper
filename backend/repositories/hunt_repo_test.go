// Package repositories_test exercises hunt_repo.go against a real Postgres
// database. Skipped unless AGF_IT_DB_URL is set. ratingsDB and must come from
// ratings_repo_test.go (the package's one TestMain); migration 155 (the hunt
// tables) is applied by the Python suite against the same test DB.
package repositories_test

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"
	"gorm.io/gorm"

	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

const huntTestEmail = "hunt-repo-test@t.local"

// cleanHunts removes every hunt owned by the test user plus any hunt-kind
// pipeline jobs those rounds enqueued (pipeline_jobs has no FK to cascade on).
func cleanHunts(g *gorm.DB) {
	must(g.Exec(`DELETE FROM public.pipeline_jobs
		WHERE kind = 'hunt' AND advertiser_id IN (
			SELECT r.id FROM frontend.hunt_rounds r
			JOIN frontend.hunts h ON h.id = r.hunt_id
			WHERE lower(h.user_email) = lower(?))`, huntTestEmail).Error)
	must(g.Exec(`DELETE FROM frontend.hunts WHERE lower(user_email) = lower(?)`, huntTestEmail).Error)
}

func TestCreateHuntAndListHunts(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	id1, err := repositories.CreateHunt(ctx, g, huntTestEmail, "sleep stories")
	require.NoError(t, err)
	id2, err := repositories.CreateHunt(ctx, g, huntTestEmail, "budgeting apps")
	require.NoError(t, err)
	require.Greater(t, id2, id1)

	hunts, err := repositories.ListHunts(ctx, g, huntTestEmail)
	require.NoError(t, err)
	require.Len(t, hunts, 2)
	// Ordered by id DESC (newest first).
	require.Equal(t, id2, hunts[0].ID)
	require.Equal(t, "budgeting apps", hunts[0].Title)
	require.Equal(t, id1, hunts[1].ID)
	// No rounds yet → LastRoundStatus is null.
	require.Nil(t, hunts[0].LastRoundStatus)

	// A round on id1 populates its LastRoundStatus.
	terms, _ := json.Marshal([]string{"sleep stories app"})
	comps, _ := json.Marshal([]string{})
	_, err = repositories.InsertHuntRound(ctx, g, id1, nil, terms, comps)
	require.NoError(t, err)
	hunts, err = repositories.ListHunts(ctx, g, huntTestEmail)
	require.NoError(t, err)
	for _, h := range hunts {
		if h.ID == id1 {
			require.NotNil(t, h.LastRoundStatus)
			require.Equal(t, "queued", *h.LastRoundStatus)
		}
	}
}

func TestHuntOwner(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	id, err := repositories.CreateHunt(ctx, g, huntTestEmail, "owner check")
	require.NoError(t, err)

	owner, err := repositories.HuntOwner(ctx, g, id)
	require.NoError(t, err)
	require.Equal(t, huntTestEmail, owner)

	missing, err := repositories.HuntOwner(ctx, g, 2000000000)
	require.NoError(t, err)
	require.Equal(t, "", missing)
}

func TestHuntMessagesRoundtrip(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	id, err := repositories.CreateHunt(ctx, g, huntTestEmail, "msg roundtrip")
	require.NoError(t, err)

	uMsg, err := repositories.InsertHuntMessage(ctx, g, id, "user", "find me sleep apps", nil)
	require.NoError(t, err)
	proposal := json.RawMessage(`{"reply":"here are some angles","terms":["sleep stories app"],"competitor_apps":["Calm"]}`)
	aMsg, err := repositories.InsertHuntMessage(ctx, g, id, "assistant", "", proposal)
	require.NoError(t, err)
	require.Greater(t, aMsg, uMsg)

	msgs, err := repositories.ListHuntMessages(ctx, g, id)
	require.NoError(t, err)
	require.Len(t, msgs, 2)
	require.Equal(t, "user", msgs[0].Role)
	require.Equal(t, "find me sleep apps", msgs[0].Content)
	require.Nil(t, msgs[0].Proposal) // user message has null proposal
	require.Equal(t, "assistant", msgs[1].Role)
	require.NotNil(t, msgs[1].Proposal)
	// The jsonb survived the roundtrip.
	var got struct {
		Reply string   `json:"reply"`
		Terms []string `json:"terms"`
	}
	require.NoError(t, json.Unmarshal(msgs[1].Proposal, &got))
	require.Equal(t, "here are some angles", got.Reply)
	require.Equal(t, []string{"sleep stories app"}, got.Terms)
}

func TestInsertHuntRoundEnqueuesJob(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "sleep stories")
	require.NoError(t, err)
	msgID, err := repositories.InsertHuntMessage(ctx, g, huntID, "assistant", "", json.RawMessage(`{}`))
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app"})
	comps, _ := json.Marshal([]string{"Calm"})
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, &msgID, terms, comps)
	require.NoError(t, err)

	// The round exists and is queued.
	rounds, err := repositories.ListHuntRounds(ctx, g, huntID)
	require.NoError(t, err)
	require.Len(t, rounds, 1)
	require.Equal(t, roundID, rounds[0].ID)
	require.Equal(t, "queued", rounds[0].Status)
	require.NotNil(t, rounds[0].MessageID)
	require.Equal(t, msgID, *rounds[0].MessageID)

	// Exactly one pipeline_jobs row: kind=hunt, advertiser_id=roundID, queued.
	var kind, status string
	require.NoError(t, g.Raw(
		`SELECT kind, status FROM public.pipeline_jobs
		 WHERE kind = 'hunt' AND advertiser_id = ?`, roundID).
		Row().Scan(&kind, &status))
	require.Equal(t, "hunt", kind)
	require.Equal(t, "queued", status)

	var jobCount int64
	require.NoError(t, g.Raw(
		`SELECT count(*) FROM public.pipeline_jobs
		 WHERE kind = 'hunt' AND advertiser_id = ?`, roundID).Row().Scan(&jobCount))
	require.EqualValues(t, 1, jobCount)
}

func TestInsertHuntRoundNilCompetitorsStoresEmptyArray(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "nil competitors")
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app"})
	// nil competitors must NOT violate the NOT NULL column; it defaults to '[]'.
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, nil)
	require.NoError(t, err)

	var termsText, compsText string
	require.NoError(t, g.Raw(
		`SELECT terms::text, competitor_apps::text FROM frontend.hunt_rounds WHERE id = ?`,
		roundID).Row().Scan(&termsText, &compsText))
	require.Equal(t, "[]", compsText)
	require.JSONEq(t, `["sleep stories app"]`, termsText)
}

func TestListHuntResultsJoinsGemScore(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "results")
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app"})
	comps, _ := json.Marshal([]string{})
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, comps)
	require.NoError(t, err)

	// One kept result wired to the seeded scored advertiser; one pending result
	// with no advertiser (GemScore must be null-safe).
	must(g.Exec(`INSERT INTO frontend.hunt_results
		(round_id, page_id, page_name, store, store_id, link_url, source_term, was_new, triage, kept_advertiser_id)
		VALUES (?, 'PG1', 'Kept Co', 'play', 'com.kept', 'https://x', 'sleep stories app', true, 'kept', ?)`,
		roundID, ratingsAdvID).Error)
	must(g.Exec(`INSERT INTO frontend.hunt_results
		(round_id, page_id, page_name, store, store_id, source_term, was_new, triage)
		VALUES (?, 'PG2', 'Pending Co', 'appstore', '999', 'sleep stories app', false, 'pending')`,
		roundID).Error)

	results, err := repositories.ListHuntResults(ctx, g, huntID)
	require.NoError(t, err)
	require.Len(t, results, 2)

	byPage := map[string]int{}
	for i, r := range results {
		byPage[r.PageID] = i
	}
	kept := results[byPage["PG1"]]
	require.Equal(t, "kept", kept.Triage)
	require.NotNil(t, kept.KeptAdvertiserID)
	require.NotNil(t, kept.GemScore)
	require.InDelta(t, 77.5, *kept.GemScore, 0.001)

	pending := results[byPage["PG2"]]
	require.Equal(t, "pending", pending.Triage)
	require.Nil(t, pending.KeptAdvertiserID)
	require.Nil(t, pending.GemScore)
}

func TestHuntRoundSummaries(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "summaries")
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app", "ai bedtime audio"})
	comps, _ := json.Marshal([]string{})
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, comps)
	require.NoError(t, err)

	// Two results, one kept.
	must(g.Exec(`INSERT INTO frontend.hunt_results
		(round_id, page_id, store, store_id, source_term, was_new, triage)
		VALUES (?, 'S1', 'play', 'com.s1', 'sleep stories app', true, 'kept'),
		       (?, 'S2', 'play', 'com.s2', 'ai bedtime audio', true, 'pending')`,
		roundID, roundID).Error)

	sums, err := repositories.HuntRoundSummaries(ctx, g, huntID)
	require.NoError(t, err)
	require.Len(t, sums, 1)
	require.Equal(t, roundID, sums[0].RoundID)
	require.EqualValues(t, 2, sums[0].TermCount)
	require.EqualValues(t, 2, sums[0].FoundCount)
	require.EqualValues(t, 1, sums[0].KeptCount)
}
