package integration

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/models"
	"github.com/edaslakavicius/emerging-app-finder/backend/repositories"
)

// TestSavedGroupsRepo covers the board-column repo: default seeding, append,
// rename/reorder with ownership, delete-with-reassign, the last-group guard,
// and the group_id round-trip through the saved-gems upsert. The shared test DB
// is reused by other tests, so every assertion is scoped to per-run-unique
// emails and the test deletes its own rows on the way out.
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

	// --- (a) fresh email seeds exactly the four stage-mirror defaults ---
	got, err := repositories.ListSavedGroups(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	wantNames := []string{"Just saw", "Watching", "Looks real", "Building"}
	if len(got) != 4 {
		t.Fatalf("ListSavedGroups seeded %d groups, want 4: %+v", len(got), got)
	}
	for i, w := range wantNames {
		if got[i].Name != w || got[i].Position != i {
			t.Fatalf("group[%d] = %+v, want name %q position %d", i, got[i], w, i)
		}
		if got[i].ID == 0 {
			t.Fatalf("group[%d] has zero id: %+v", i, got[i])
		}
	}

	// Seeding is once-only: a second list returns the same four rows.
	again, err := repositories.ListSavedGroups(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(again) != 4 {
		t.Fatalf("second ListSavedGroups returned %d groups, want 4 (no duplicate seeding)", len(again))
	}
	for i := range again {
		if again[i].ID != got[i].ID {
			t.Fatalf("group[%d] id changed across calls: %d → %d", i, got[i].ID, again[i].ID)
		}
	}

	// --- (b) create appends at max(position)+1 ---
	created, err := repositories.CreateSavedGroup(ctx, g, me, "Later")
	if err != nil {
		t.Fatal(err)
	}
	if created.Name != "Later" || created.Position != 4 || created.ID == 0 {
		t.Fatalf("CreateSavedGroup = %+v, want name Later position 4 non-zero id", created)
	}
	if list, err := repositories.ListSavedGroups(ctx, g, me); err != nil {
		t.Fatal(err)
	} else if len(list) != 5 || list[4].ID != created.ID {
		t.Fatalf("after create, list = %+v, want 5 groups ending in the new one", list)
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

	// --- (f) the saved-gems upsert round-trips group_id ---
	watching := got[1] // "Watching"
	gemIn := models.SavedGemIn{
		UserEmail: me,
		Cohort:    "ios",
		GemScore:  71,
		Stage:     "watching",
		GroupID:   &watching.ID,
	}
	if err := repositories.UpsertSavedGem(ctx, g, gemIn, advID); err != nil {
		t.Fatal(err)
	}
	gemIn2 := gemIn
	if err := repositories.UpsertSavedGem(ctx, g, gemIn2, adv2ID); err != nil {
		t.Fatal(err)
	}
	gems, err := repositories.ListSavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(gems) != 2 {
		t.Fatalf("ListSavedGems returned %d rows, want 2", len(gems))
	}
	for _, gem := range gems {
		if gem.GroupID == nil || *gem.GroupID != watching.ID {
			t.Fatalf("saved gem %d group_id = %v, want %d", gem.AdvertiserID, gem.GroupID, watching.ID)
		}
	}

	// A re-save that carries no group (the Discover save button) keeps the card
	// in the column it was filed under.
	noGroup := gemIn
	noGroup.GroupID = nil
	noGroup.Note = "resaved from discover"
	if err := repositories.UpsertSavedGem(ctx, g, noGroup, advID); err != nil {
		t.Fatal(err)
	}
	gems, err = repositories.ListSavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	for _, gem := range gems {
		if gem.GroupID == nil || *gem.GroupID != watching.ID {
			t.Fatalf("group-less re-save cleared gem %d group_id: %v", gem.AdvertiserID, gem.GroupID)
		}
	}

	// A group owned by somebody else is rejected with the sentinel.
	theirs, err := repositories.CreateSavedGroup(ctx, g, other, "Theirs")
	if err != nil {
		t.Fatal(err)
	}
	bad := gemIn
	bad.GroupID = &theirs.ID
	if err := repositories.UpsertSavedGem(ctx, g, bad, advID); !errors.Is(err, repositories.ErrGroupNotOwned) {
		t.Fatalf("upsert with a foreign group_id err = %v, want ErrGroupNotOwned", err)
	}

	// --- (d) delete reassigns that group's gems to the first remaining group ---
	deleted, lastGroup, err := repositories.DeleteSavedGroup(ctx, g, me, watching.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !deleted || lastGroup {
		t.Fatalf("DeleteSavedGroup = (deleted %v, last %v), want (true, false)", deleted, lastGroup)
	}
	first := got[0] // "Just saw", position 0
	gems, err = repositories.ListSavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(gems) != 2 {
		t.Fatalf("after group delete ListSavedGems returned %d rows, want 2 (gems survive)", len(gems))
	}
	for _, gem := range gems {
		if gem.GroupID == nil || *gem.GroupID != first.ID {
			t.Fatalf("orphaned gem %d group_id = %v, want reassignment to %d", gem.AdvertiserID, gem.GroupID, first.ID)
		}
	}
	// A foreign delete is a miss, not a last-group answer.
	if d, l, err := repositories.DeleteSavedGroup(ctx, g, other, first.ID); err != nil {
		t.Fatal(err)
	} else if d || l {
		t.Fatalf("foreign DeleteSavedGroup = (%v, %v), want (false, false)", d, l)
	}

	// --- (e) the last group cannot be deleted ---
	remaining, err := repositories.ListSavedGroups(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(remaining) != 4 {
		t.Fatalf("remaining groups = %d, want 4", len(remaining))
	}
	for _, grp := range remaining[:len(remaining)-1] {
		if d, l, err := repositories.DeleteSavedGroup(ctx, g, me, grp.ID); err != nil {
			t.Fatal(err)
		} else if !d || l {
			t.Fatalf("DeleteSavedGroup(%d) = (%v, %v), want (true, false)", grp.ID, d, l)
		}
	}
	survivor := remaining[len(remaining)-1]
	d, l, err := repositories.DeleteSavedGroup(ctx, g, me, survivor.ID)
	if err != nil {
		t.Fatal(err)
	}
	if d || !l {
		t.Fatalf("deleting the last group = (deleted %v, last %v), want (false, true)", d, l)
	}
	if final, err := repositories.ListSavedGroups(ctx, g, me); err != nil {
		t.Fatal(err)
	} else if len(final) != 1 || final[0].ID != survivor.ID {
		t.Fatalf("final groups = %+v, want just the survivor %d", final, survivor.ID)
	}
}

// TestSavedGroupsWireKeys pins the JSON keys the web board reads. Field renames
// in Go are invisible to TypeScript, so the literal keys are asserted here.
func TestSavedGroupsWireKeys(t *testing.T) {
	var gid int64 = 42
	for _, tc := range []struct {
		name string
		v    any
		keys []string
	}{
		{"SavedGroupRow", models.SavedGroupRow{ID: 1, Name: "Watching", Position: 2},
			[]string{"id", "name", "position"}},
		{"SavedGemRow", models.SavedGemRow{AdvertiserID: 7, GroupID: &gid},
			[]string{"group_id"}},
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
	// A nil group renders as an explicit null, never an omitted key.
	b, err := json.Marshal(models.SavedGemRow{AdvertiserID: 7})
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(b, &raw); err != nil {
		t.Fatal(err)
	}
	if v, ok := raw["group_id"]; !ok || string(v) != "null" {
		t.Errorf("nil GroupID = %s (present %v), want null", v, ok)
	}
}
