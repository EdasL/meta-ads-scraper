package integration

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/middlewares"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// TestSavedGroupsRepo covers the folder repo: no seeding for a new user, append,
// rename/reorder with ownership, multi-folder membership (a gem in many folders
// at once), idempotent add/remove, the foreign-folder miss, and delete cascading
// memberships without touching the gems. The shared test DB is reused by other
// tests, so every assertion is scoped to per-run-unique emails and the test
// deletes its own rows on the way out.
func TestSavedGroupsRepo(t *testing.T) {
	ctx := context.Background()
	g := db.DB()

	uniq := time.Now().UnixNano()
	me := fmt.Sprintf("groups-%d@test.local", uniq)
	other := fmt.Sprintf("groups-other-%d@test.local", uniq)

	t.Cleanup(func() {
		// saved_gems first: FK → saved_groups.
		if err := g.Exec(`DELETE FROM frontend.saved_gems WHERE user_email IN (?, ?)`, me, other).Error; err != nil {
			t.Errorf("cleanup saved_gems: %v", err)
		}
		if err := g.Exec(`DELETE FROM frontend.saved_groups WHERE user_email IN (?, ?)`, me, other).Error; err != nil {
			t.Errorf("cleanup saved_groups: %v", err)
		}
	})

	// --- (a) a user who has made no folders gets an empty list, not defaults ---
	fresh, err := repositories.ListSavedGroups(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(fresh) != 0 {
		t.Fatalf("ListSavedGroups on a fresh email returned %d groups, want 0 (no seeding): %+v", len(fresh), fresh)
	}
	if fresh == nil {
		t.Fatalf("ListSavedGroups returned a nil slice, want an empty one (JSON [] not null)")
	}
	// Listing does not create anything as a side effect.
	var seeded int64
	if err := g.Raw(`SELECT count(*) FROM frontend.saved_groups WHERE user_email = ?`, me).
		Scan(&seeded).Error; err != nil {
		t.Fatal(err)
	}
	if seeded != 0 {
		t.Fatalf("after two lists the fresh user has %d group rows, want 0", seeded)
	}

	// --- (b) create appends at max(position)+1, starting at 0 ---
	first, err := repositories.CreateSavedGroup(ctx, g, me, "Fitness")
	if err != nil {
		t.Fatal(err)
	}
	if first.Name != "Fitness" || first.Position != 0 || first.ID == 0 {
		t.Fatalf("first CreateSavedGroup = %+v, want name Fitness position 0 non-zero id", first)
	}
	watching, err := repositories.CreateSavedGroup(ctx, g, me, "Watching")
	if err != nil {
		t.Fatal(err)
	}
	if watching.Position != 1 {
		t.Fatalf("second CreateSavedGroup = %+v, want position 1", watching)
	}
	created, err := repositories.CreateSavedGroup(ctx, g, me, "Later")
	if err != nil {
		t.Fatal(err)
	}
	if created.Name != "Later" || created.Position != 2 || created.ID == 0 {
		t.Fatalf("CreateSavedGroup = %+v, want name Later position 2 non-zero id", created)
	}
	if list, err := repositories.ListSavedGroups(ctx, g, me); err != nil {
		t.Fatal(err)
	} else if len(list) != 3 || list[2].ID != created.ID {
		t.Fatalf("after create, list = %+v, want 3 groups ending in the new one", list)
	}

	// --- (c) update: foreign owner is a miss, owner sticks ---
	newName := "Later maybe"
	newPos := 9
	ok, err := repositories.UpdateSavedGroup(ctx, g, other, created.ID, &newName, &newPos)
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatalf("UpdateSavedGroup with a foreign email returned true, want false")
	}
	ok, err = repositories.UpdateSavedGroup(ctx, g, me, created.ID, &newName, &newPos)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatalf("UpdateSavedGroup by the owner returned false, want true")
	}
	// A missing id is also a miss.
	if ok, err := repositories.UpdateSavedGroup(ctx, g, me, -1, &newName, nil); err != nil {
		t.Fatal(err)
	} else if ok {
		t.Fatalf("UpdateSavedGroup on an unknown id returned true, want false")
	}
	// Partial patch: name only, position untouched.
	onlyName := "Later"
	if ok, err := repositories.UpdateSavedGroup(ctx, g, me, created.ID, &onlyName, nil); err != nil {
		t.Fatal(err)
	} else if !ok {
		t.Fatalf("name-only UpdateSavedGroup returned false, want true")
	}
	list, err := repositories.ListSavedGroups(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	var updated *models.SavedGroupRow
	for i := range list {
		if list[i].ID == created.ID {
			updated = &list[i]
		}
	}
	if updated == nil {
		t.Fatalf("created group vanished from list: %+v", list)
	}
	if updated.Name != "Later" || updated.Position != 9 {
		t.Fatalf("after patches group = %+v, want name Later position 9", *updated)
	}

	// --- (f) membership: a gem lives in many folders at once ---
	gemIn := models.SavedGemIn{
		UserEmail: me,
		Cohort:    "ios",
		GemScore:  71,
		Stage:     "watching",
	}
	if err := repositories.UpsertSavedGem(ctx, g, gemIn, advID); err != nil {
		t.Fatal(err)
	}
	if err := repositories.UpsertSavedGem(ctx, g, gemIn, adv2ID); err != nil {
		t.Fatal(err)
	}

	// membersOf answers the folder ids ListSavedGems reports for one gem.
	membersOf := func(advertiserID int) []int64 {
		t.Helper()
		rows, err := repositories.ListSavedGems(ctx, g, me)
		if err != nil {
			t.Fatal(err)
		}
		for _, gem := range rows {
			if gem.AdvertiserID == advertiserID {
				if gem.GroupIDs == nil {
					t.Fatalf("gem %d group_ids is nil, want an empty list", advertiserID)
				}
				return gem.GroupIDs
			}
		}
		t.Fatalf("gem %d missing from ListSavedGems", advertiserID)
		return nil
	}
	// countMemberships reads the join table directly, so an assertion cannot be
	// satisfied by an aggregation bug in ListSavedGems.
	countMemberships := func(groupID int64) int64 {
		t.Helper()
		var n int64
		if err := g.Raw(
			`SELECT count(*) FROM frontend.saved_group_members WHERE group_id = ?`, groupID,
		).Scan(&n).Error; err != nil {
			t.Fatal(err)
		}
		return n
	}

	// A saved gem starts in no folder: [] on the wire, never null.
	if ids := membersOf(advID); len(ids) != 0 {
		t.Fatalf("a freshly saved gem has group_ids %v, want empty", ids)
	}

	// Two folders, one gem — the playlist case.
	for _, grp := range []int64{watching.ID, first.ID} {
		if found, err := repositories.AddSavedGroupMember(ctx, g, me, grp, int64(advID)); err != nil {
			t.Fatal(err)
		} else if !found {
			t.Fatalf("AddSavedGroupMember(%d) = found false, want true", grp)
		}
	}
	// group_ids is ordered by group_id, so the expectation is the sorted pair.
	wantIDs := []int64{first.ID, watching.ID}
	if first.ID > watching.ID {
		wantIDs = []int64{watching.ID, first.ID}
	}
	if got := membersOf(advID); !reflect.DeepEqual(got, []int64(models.IDList(wantIDs))) {
		t.Fatalf("gem in two folders reports group_ids %v, want %v (ascending group_id)", got, wantIDs)
	}
	// Adding one gem to a folder does not drag its neighbours in.
	if ids := membersOf(adv2ID); len(ids) != 0 {
		t.Fatalf("untouched gem has group_ids %v, want empty", ids)
	}

	// Adding again is a no-op, not a duplicate or an error.
	if found, err := repositories.AddSavedGroupMember(ctx, g, me, watching.ID, int64(advID)); err != nil {
		t.Fatal(err)
	} else if !found {
		t.Fatalf("re-adding an existing member returned found false, want true")
	}
	if n := countMemberships(watching.ID); n != 1 {
		t.Fatalf("after a duplicate add folder %d has %d membership rows, want 1", watching.ID, n)
	}
	if got := membersOf(advID); len(got) != 2 {
		t.Fatalf("after a duplicate add group_ids = %v, want 2 entries", got)
	}

	// --- (g) a foreign folder is a miss and writes nothing ---
	theirs, err := repositories.CreateSavedGroup(ctx, g, other, "Theirs")
	if err != nil {
		t.Fatal(err)
	}
	if found, err := repositories.AddSavedGroupMember(ctx, g, me, theirs.ID, int64(advID)); err != nil {
		t.Fatal(err)
	} else if found {
		t.Fatalf("AddSavedGroupMember into a foreign folder = found true, want false")
	}
	if n := countMemberships(theirs.ID); n != 0 {
		t.Fatalf("a foreign add wrote %d rows into folder %d, want 0", n, theirs.ID)
	}
	if found, err := repositories.AddSavedGroupMember(ctx, g, me, -1, int64(advID)); err != nil {
		t.Fatal(err)
	} else if found {
		t.Fatalf("AddSavedGroupMember into an unknown folder = found true, want false")
	}
	if found, err := repositories.RemoveSavedGroupMember(ctx, g, me, theirs.ID, int64(advID)); err != nil {
		t.Fatal(err)
	} else if found {
		t.Fatalf("RemoveSavedGroupMember on a foreign folder = found true, want false")
	}

	// --- (h) remove is idempotent and leaves other folders alone ---
	if found, err := repositories.RemoveSavedGroupMember(ctx, g, me, first.ID, int64(advID)); err != nil {
		t.Fatal(err)
	} else if !found {
		t.Fatalf("RemoveSavedGroupMember returned found false for an owned folder")
	}
	if got := membersOf(advID); !reflect.DeepEqual(got, []int64{watching.ID}) {
		t.Fatalf("after removing one folder group_ids = %v, want [%d]", got, watching.ID)
	}
	// Removing something that is not a member still succeeds.
	if found, err := repositories.RemoveSavedGroupMember(ctx, g, me, first.ID, int64(advID)); err != nil {
		t.Fatal(err)
	} else if !found {
		t.Fatalf("a repeated remove returned found false, want true (idempotent)")
	}
	if got := membersOf(advID); len(got) != 1 {
		t.Fatalf("a repeated remove changed group_ids to %v, want one entry", got)
	}

	// --- (d) deleting a folder cascades its memberships; the gems survive ---
	if n := countMemberships(watching.ID); n != 1 {
		t.Fatalf("folder %d has %d memberships before delete, want 1", watching.ID, n)
	}
	deleted, err := repositories.DeleteSavedGroup(ctx, g, me, watching.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !deleted {
		t.Fatalf("DeleteSavedGroup = deleted %v, want true", deleted)
	}
	if n := countMemberships(watching.ID); n != 0 {
		t.Fatalf("folder %d still has %d membership rows after delete, want 0 (CASCADE)", watching.ID, n)
	}
	gems, err := repositories.ListSavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(gems) != 2 {
		t.Fatalf("after folder delete ListSavedGems returned %d rows, want 2 (gems survive)", len(gems))
	}
	for _, gem := range gems {
		if len(gem.GroupIDs) != 0 {
			t.Fatalf("gem %d still reports folders %v after its only folder was deleted", gem.AdvertiserID, gem.GroupIDs)
		}
	}
	remaining, err := repositories.ListSavedGroups(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	for _, grp := range remaining {
		if grp.ID == watching.ID {
			t.Fatalf("deleted folder %d is still listed: %+v", watching.ID, remaining)
		}
	}
	// A foreign delete is a miss.
	if d, err := repositories.DeleteSavedGroup(ctx, g, other, first.ID); err != nil {
		t.Fatal(err)
	} else if d {
		t.Fatalf("foreign DeleteSavedGroup = %v, want false", d)
	}
	// So is an unknown id.
	if d, err := repositories.DeleteSavedGroup(ctx, g, me, -1); err != nil {
		t.Fatal(err)
	} else if d {
		t.Fatalf("DeleteSavedGroup on an unknown id = %v, want false", d)
	}

	// --- (e) folders may be deleted down to zero ---
	if len(remaining) != 2 {
		t.Fatalf("remaining folders = %d, want 2", len(remaining))
	}
	for _, grp := range remaining {
		if d, err := repositories.DeleteSavedGroup(ctx, g, me, grp.ID); err != nil {
			t.Fatal(err)
		} else if !d {
			t.Fatalf("DeleteSavedGroup(%d) = %v, want true (deleting to zero is allowed)", grp.ID, d)
		}
	}
	final, err := repositories.ListSavedGroups(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(final) != 0 {
		t.Fatalf("final folders = %+v, want none", final)
	}
	// The gems outlive every folder.
	gems, err = repositories.ListSavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(gems) != 2 {
		t.Fatalf("after deleting every folder ListSavedGems returned %d rows, want 2", len(gems))
	}
	for _, gem := range gems {
		if len(gem.GroupIDs) != 0 {
			t.Fatalf("gem %d reports folders %v with none left", gem.AdvertiserID, gem.GroupIDs)
		}
	}
}

// TestSavedGroupMemberEndpoints drives the two membership routes through the
// real router, which the repo tests cannot do: it pins the URL shapes, the
// identity-from-KeyAuth rule (the body carries no user_email), and the status
// codes — 404 for a folder the caller does not own, 422 for a missing
// advertiser_id.
//
// These are write routes, so RequireToken applies: t.Setenv must precede
// router() because the middleware reads the env at construction.
func TestSavedGroupMemberEndpoints(t *testing.T) {
	t.Setenv(middlewares.APITokenEnv, writeToken)
	r := router()
	ctx := context.Background()
	g := db.DB()

	// authedEmail is the identity the internal-token path resolves to, so the
	// folder must belong to that user for the request to find it.
	other := fmt.Sprintf("groups-http-other-%d@test.local", time.Now().UnixNano())
	t.Cleanup(func() {
		if err := g.Exec(`DELETE FROM frontend.saved_groups WHERE user_email IN (?, ?)`,
			authedEmail, other).Error; err != nil {
			t.Errorf("cleanup saved_groups: %v", err)
		}
	})

	mine, err := repositories.CreateSavedGroup(ctx, g, authedEmail, "HTTP folder")
	if err != nil {
		t.Fatal(err)
	}
	theirs, err := repositories.CreateSavedGroup(ctx, g, other, "Not yours")
	if err != nil {
		t.Fatal(err)
	}

	post := func(path, body string) int {
		w := httptest.NewRecorder()
		req := authedInternal(httptest.NewRequest(http.MethodPost, path, strings.NewReader(body)))
		req.Header.Set("Content-Type", "application/json")
		r.ServeHTTP(w, req)
		return w.Code
	}
	gemsPath := func(id int64, suffix string) string {
		return "/api/saved-groups/" + strconv.FormatInt(id, 10) + "/gems" + suffix
	}
	body := fmt.Sprintf(`{"advertiser_id": %d}`, advID)

	if code := post(gemsPath(mine.ID, ""), body); code != http.StatusOK {
		t.Fatalf("add to an owned folder = %d, want 200", code)
	}
	// Idempotent over the wire too.
	if code := post(gemsPath(mine.ID, ""), body); code != http.StatusOK {
		t.Fatalf("repeated add = %d, want 200", code)
	}
	var n int64
	if err := g.Raw(`SELECT count(*) FROM frontend.saved_group_members WHERE group_id = ?`,
		mine.ID).Scan(&n).Error; err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("two adds produced %d membership rows, want 1", n)
	}

	if code := post(gemsPath(theirs.ID, ""), body); code != http.StatusNotFound {
		t.Fatalf("add into a foreign folder = %d, want 404", code)
	}
	if code := post(gemsPath(theirs.ID, "/remove"), body); code != http.StatusNotFound {
		t.Fatalf("remove from a foreign folder = %d, want 404", code)
	}
	if code := post("/api/saved-groups/not-a-number/gems", body); code != http.StatusNotFound {
		t.Fatalf("add with a non-numeric folder id = %d, want 404", code)
	}
	if code := post(gemsPath(mine.ID, ""), `{}`); code != http.StatusUnprocessableEntity {
		t.Fatalf("add with no advertiser_id = %d, want 422", code)
	}

	if code := post(gemsPath(mine.ID, "/remove"), body); code != http.StatusOK {
		t.Fatalf("remove from an owned folder = %d, want 200", code)
	}
	if code := post(gemsPath(mine.ID, "/remove"), body); code != http.StatusOK {
		t.Fatalf("repeated remove = %d, want 200 (idempotent)", code)
	}
	if err := g.Raw(`SELECT count(*) FROM frontend.saved_group_members WHERE group_id = ?`,
		mine.ID).Scan(&n).Error; err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Fatalf("after remove the folder has %d membership rows, want 0", n)
	}
}

// TestSavedGroupsWireKeys pins the JSON keys the web board reads. Field renames
// in Go are invisible to TypeScript, so the literal keys are asserted here.
func TestSavedGroupsWireKeys(t *testing.T) {
	for _, tc := range []struct {
		name string
		v    any
		keys []string
	}{
		{"SavedGroupRow", models.SavedGroupRow{ID: 1, Name: "Watching", Position: 2},
			[]string{"id", "name", "position"}},
		{"SavedGemRow", models.SavedGemRow{AdvertiserID: 7, GroupIDs: models.IDList{42, 43}},
			[]string{"group_ids"}},
	} {
		b, err := json.Marshal(tc.v)
		if err != nil {
			t.Fatal(err)
		}
		var m map[string]any
		if err := json.Unmarshal(b, &m); err != nil {
			t.Fatal(err)
		}
		for _, k := range tc.keys {
			if _, ok := m[k]; !ok {
				t.Errorf("%s: key %q missing from %s", tc.name, k, b)
			}
		}
	}
	// A gem in no folders renders as [], never null and never an omitted key —
	// the client iterates group_ids without a null guard.
	b, err := json.Marshal(models.SavedGemRow{AdvertiserID: 7})
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(b, &raw); err != nil {
		t.Fatal(err)
	}
	if v, ok := raw["group_ids"]; !ok || string(v) != "[]" {
		t.Errorf("empty GroupIDs = %s (present %v), want []", v, ok)
	}
	// And a populated list keeps its literal ids in order.
	b, err = json.Marshal(models.SavedGemRow{AdvertiserID: 7, GroupIDs: models.IDList{42, 43}})
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &raw); err != nil {
		t.Fatal(err)
	}
	if string(raw["group_ids"]) != "[42,43]" {
		t.Errorf("GroupIDs = %s, want [42,43]", raw["group_ids"])
	}
	// The single-folder key is gone: the web store must not read it any more.
	if _, ok := raw["group_id"]; ok {
		t.Errorf("SavedGemRow still carries the legacy group_id key: %s", b)
	}
}
