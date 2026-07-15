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
	_, err = repositories.InsertHuntRound(ctx, g, id1, nil, terms, comps, nil, nil)
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

	uMsg, err := repositories.InsertHuntMessage(ctx, g, id, "user", "find me sleep apps", nil, nil)
	require.NoError(t, err)
	proposal := json.RawMessage(`{"reply":"here are some angles","terms":["sleep stories app"],"competitor_apps":["Calm"]}`)
	aMsg, err := repositories.InsertHuntMessage(ctx, g, id, "assistant", "", proposal, nil)
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
	msgID, err := repositories.InsertHuntMessage(ctx, g, huntID, "assistant", "", json.RawMessage(`{}`), nil)
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app"})
	comps, _ := json.Marshal([]string{"Calm"})
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, &msgID, terms, comps, nil, nil)
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

func TestInsertHuntRoundIntentAndRelevanceColumns(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "intent hunt")
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app"})
	intent := "sleep apps aimed at new parents"
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, nil, nil, &intent)
	require.NoError(t, err)

	// The intent sentence persists on the round.
	var got *string
	require.NoError(t, g.Raw(
		`SELECT intent FROM frontend.hunt_rounds WHERE id = ?`, roundID).Scan(&got).Error)
	require.NotNil(t, got)
	require.Equal(t, intent, *got)

	// A relevance verdict + note round-trips through the result readers.
	must(g.Exec(`INSERT INTO frontend.hunt_results
		(round_id, page_id, store, store_id, source_term, was_new, triage, relevance, relevance_note)
		VALUES (?, 'R1', 'play', 'com.r1', 'sleep stories app', true, 'kept', 'relevant', 'matches new-parent sleep angle'),
		       (?, 'R2', 'play', 'com.r2', 'sleep stories app', true, 'kept', 'off_target', 'general meditation, not sleep')`,
		roundID, roundID).Error)

	results, err := repositories.RoundResults(ctx, g, huntID, roundID, "", 100)
	require.NoError(t, err)
	require.Len(t, results, 2)
	byPage := map[string]int{}
	for i, r := range results {
		byPage[r.PageID] = i
	}
	rel := results[byPage["R1"]]
	require.NotNil(t, rel.Relevance)
	require.Equal(t, "relevant", *rel.Relevance)
	require.NotNil(t, rel.RelevanceNote)
	require.Equal(t, "matches new-parent sleep angle", *rel.RelevanceNote)
	off := results[byPage["R2"]]
	require.NotNil(t, off.Relevance)
	require.Equal(t, "off_target", *off.Relevance)

	// RoundReport counts the off_target verdict.
	rep, err := repositories.RoundReport(ctx, g, huntID, roundID)
	require.NoError(t, err)
	require.NotNil(t, rep)
	require.Equal(t, 1, rep.OffTargetCount)
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
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, nil, nil, nil)
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
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, comps, nil, nil)
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

	results, err := repositories.ListHuntResults(ctx, g, huntID, huntTestEmail)
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
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, comps, nil, nil)
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

func TestInsertHuntRoundSettingsRoundtrip(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "settings roundtrip")
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app"})

	// A round WITH settings reads back verbatim via ListHuntRounds.
	settings := json.RawMessage(`{"page_cap":30}`)
	withID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, nil, settings, nil)
	require.NoError(t, err)
	// A round with nil settings must default to '{}' (NOT NULL column).
	nilID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, nil, nil, nil)
	require.NoError(t, err)

	rounds, err := repositories.ListHuntRounds(ctx, g, huntID)
	require.NoError(t, err)
	byID := map[int]int{}
	for i, r := range rounds {
		byID[r.ID] = i
	}
	withRow := rounds[byID[withID]]
	require.NotNil(t, withRow.Settings)
	require.JSONEq(t, `{"page_cap":30}`, string(withRow.Settings))
	var got struct {
		PageCap int `json:"page_cap"`
	}
	require.NoError(t, json.Unmarshal(withRow.Settings, &got))
	require.Equal(t, 30, got.PageCap)

	nilRow := rounds[byID[nilID]]
	require.JSONEq(t, `{}`, string(nilRow.Settings))
}

func TestInsertHuntMessageToolLogRoundtrip(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "tool_log roundtrip")
	require.NoError(t, err)

	// User message carries no tool_log → SQL NULL.
	_, err = repositories.InsertHuntMessage(ctx, g, huntID, "user", "find me sleep apps", nil, nil)
	require.NoError(t, err)
	// Assistant message with a tool trace.
	toolLog := json.RawMessage(`[{"tool":"round_report","round_id":7}]`)
	_, err = repositories.InsertHuntMessage(ctx, g, huntID, "assistant", "checking", nil, toolLog)
	require.NoError(t, err)

	msgs, err := repositories.ListHuntMessages(ctx, g, huntID)
	require.NoError(t, err)
	require.Len(t, msgs, 2)
	require.Equal(t, "user", msgs[0].Role)
	require.Nil(t, msgs[0].ToolLog) // user message has null tool_log
	require.Equal(t, "assistant", msgs[1].Role)
	require.NotNil(t, msgs[1].ToolLog)
	require.JSONEq(t, `[{"tool":"round_report","round_id":7}]`, string(msgs[1].ToolLog))
}

func TestRoundReportForeignHuntReturnsNil(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntA, err := repositories.CreateHunt(ctx, g, huntTestEmail, "hunt A")
	require.NoError(t, err)
	huntB, err := repositories.CreateHunt(ctx, g, huntTestEmail, "hunt B")
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app"})
	roundA, err := repositories.InsertHuntRound(ctx, g, huntA, nil, terms, nil, nil, nil)
	require.NoError(t, err)

	// The round exists under huntA.
	rep, err := repositories.RoundReport(ctx, g, huntA, roundA)
	require.NoError(t, err)
	require.NotNil(t, rep)
	require.Equal(t, "queued", rep.Status)

	// Asking for roundA scoped to huntB returns nil (not owned by that hunt).
	rep, err = repositories.RoundReport(ctx, g, huntB, roundA)
	require.NoError(t, err)
	require.Nil(t, rep)

	// A wholly missing round also returns nil.
	rep, err = repositories.RoundReport(ctx, g, huntA, 2000000000)
	require.NoError(t, err)
	require.Nil(t, rep)
}

func TestRoundReportTriageCounts(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "triage counts")
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app"})
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, nil, nil, nil)
	require.NoError(t, err)

	// 2 kept, 1 vetoed, 3 pending.
	must(g.Exec(`INSERT INTO frontend.hunt_results
		(round_id, page_id, store, store_id, source_term, was_new, triage)
		VALUES (?, 'K1', 'play', 'com.k1', 'sleep stories app', true, 'kept'),
		       (?, 'K2', 'play', 'com.k2', 'sleep stories app', true, 'kept'),
		       (?, 'V1', 'play', 'com.v1', 'sleep stories app', true, 'vetoed'),
		       (?, 'P1', 'play', 'com.p1', 'sleep stories app', true, 'pending'),
		       (?, 'P2', 'play', 'com.p2', 'sleep stories app', true, 'pending'),
		       (?, 'P3', 'play', 'com.p3', 'sleep stories app', true, 'pending')`,
		roundID, roundID, roundID, roundID, roundID, roundID).Error)

	// Seed a realistic progress blob: the compact digest (terms + counters) plus
	// the verbose 'events' log and 'lanes' map the report must strip (I1).
	must(g.Exec(`UPDATE frontend.hunt_rounds SET progress = CAST(? AS jsonb) WHERE id = ?`,
		`{"terms":[{"term":"sleep stories app","found":5}],"pages":10,`+
			`"events":[{"t":"start"},{"t":"page"}],"lanes":{"a":1,"b":2}}`,
		roundID).Error)

	rep, err := repositories.RoundReport(ctx, g, huntID, roundID)
	require.NoError(t, err)
	require.NotNil(t, rep)
	require.Equal(t, "queued", rep.Status)
	require.JSONEq(t, `{}`, string(rep.Settings))
	require.Equal(t, 2, rep.KeptCount)
	require.Equal(t, 1, rep.VetoedCount)
	require.Equal(t, 3, rep.PendingCount)

	// The digest keeps terms + counters but drops the events log and lanes map.
	require.Contains(t, string(rep.Progress), "terms")
	require.Contains(t, string(rep.Progress), "pages")
	require.NotContains(t, string(rep.Progress), "events")
	require.NotContains(t, string(rep.Progress), "lanes")
}

func TestRoundResultsTriageFilterAndLimit(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "round results")
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"sleep stories app"})
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, nil, nil, nil)
	require.NoError(t, err)

	// 3 kept, 2 pending. One kept is wired to the seeded scored advertiser.
	must(g.Exec(`INSERT INTO frontend.hunt_results
		(round_id, page_id, store, store_id, source_term, was_new, triage, kept_advertiser_id)
		VALUES (?, 'K1', 'play', 'com.k1', 'sleep stories app', true, 'kept', ?)`,
		roundID, ratingsAdvID).Error)
	must(g.Exec(`INSERT INTO frontend.hunt_results
		(round_id, page_id, store, store_id, source_term, was_new, triage)
		VALUES (?, 'K2', 'play', 'com.k2', 'sleep stories app', true, 'kept'),
		       (?, 'K3', 'play', 'com.k3', 'sleep stories app', true, 'kept'),
		       (?, 'P1', 'play', 'com.p1', 'sleep stories app', true, 'pending'),
		       (?, 'P2', 'play', 'com.p2', 'sleep stories app', true, 'pending')`,
		roundID, roundID, roundID, roundID).Error)

	// Empty triage → all results (5), no limit clipping at limit=100.
	all, err := repositories.RoundResults(ctx, g, huntID, roundID, "", 100)
	require.NoError(t, err)
	require.Len(t, all, 5)

	// Triage filter to 'kept' → 3.
	kept, err := repositories.RoundResults(ctx, g, huntID, roundID, "kept", 100)
	require.NoError(t, err)
	require.Len(t, kept, 3)
	for _, r := range kept {
		require.Equal(t, "kept", r.Triage)
	}
	// The GemScore join survived (K1 is wired to the scored advertiser).
	var scored int
	for _, r := range kept {
		if r.GemScore != nil {
			scored++
			require.InDelta(t, 77.5, *r.GemScore, 0.001)
		}
	}
	require.Equal(t, 1, scored)

	// Limit caps the row count.
	limited, err := repositories.RoundResults(ctx, g, huntID, roundID, "kept", 2)
	require.NoError(t, err)
	require.Len(t, limited, 2)

	// A round under a different hunt is not reachable (JOIN enforces ownership).
	otherHunt, err := repositories.CreateHunt(ctx, g, huntTestEmail, "other hunt")
	require.NoError(t, err)
	none, err := repositories.RoundResults(ctx, g, otherHunt, roundID, "", 100)
	require.NoError(t, err)
	require.Empty(t, none)
}
