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

// TestSavedGroupsRepo covers the folder repo: no seeding for a new user, append,
// rename/reorder with ownership, delete-unfiles-members down to zero folders,
// and the group_id round-trip (including the explicit unfile) through the
// saved-gems upsert. The shared test DB is reused by other tests, so every
// assertion is scoped to per-run-unique emails and the test deletes its own rows
// on the way out.
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

	// --- (f) the saved-gems upsert round-trips group_id ---
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

	// --- (g) group_id 0 is an explicit unfile; it needs no ownership ---
	var zero int64
	unfile := gemIn
	unfile.GroupID = &zero
	if err := repositories.UpsertSavedGem(ctx, g, unfile, advID); err != nil {
		t.Fatalf("upsert with group_id 0: %v", err)
	}
	gems, err = repositories.ListSavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	for _, gem := range gems {
		switch gem.AdvertiserID {
		case advID:
			if gem.GroupID != nil {
				t.Fatalf("group_id 0 left gem %d filed under %d, want NULL", gem.AdvertiserID, *gem.GroupID)
			}
		case adv2ID:
			if gem.GroupID == nil || *gem.GroupID != watching.ID {
				t.Fatalf("unfiling one gem moved gem %d: group_id = %v, want %d", gem.AdvertiserID, gem.GroupID, watching.ID)
			}
		}
	}
	// Re-file it, then confirm a nil group_id still means "keep", not "clear".
	if err := repositories.UpsertSavedGem(ctx, g, gemIn, advID); err != nil {
		t.Fatal(err)
	}
	keep := gemIn
	keep.GroupID = nil
	if err := repositories.UpsertSavedGem(ctx, g, keep, advID); err != nil {
		t.Fatal(err)
	}
	gems, err = repositories.ListSavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	for _, gem := range gems {
		if gem.GroupID == nil || *gem.GroupID != watching.ID {
			t.Fatalf("after re-file + nil upsert gem %d group_id = %v, want %d", gem.AdvertiserID, gem.GroupID, watching.ID)
		}
	}

	// --- (d) delete unfiles the folder's gems and drops the folder row ---
	deleted, err := repositories.DeleteSavedGroup(ctx, g, me, watching.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !deleted {
		t.Fatalf("DeleteSavedGroup = deleted %v, want true", deleted)
	}
	gems, err = repositories.ListSavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(gems) != 2 {
		t.Fatalf("after folder delete ListSavedGems returned %d rows, want 2 (gems survive)", len(gems))
	}
	for _, gem := range gems {
		if gem.GroupID != nil {
			t.Fatalf("gem %d group_id = %d after its folder was deleted, want NULL (unfiled)", gem.AdvertiserID, *gem.GroupID)
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
	// The gems outlive every folder, unfiled.
	gems, err = repositories.ListSavedGems(ctx, g, me)
	if err != nil {
		t.Fatal(err)
	}
	if len(gems) != 2 {
		t.Fatalf("after deleting every folder ListSavedGems returned %d rows, want 2", len(gems))
	}
	for _, gem := range gems {
		if gem.GroupID != nil {
			t.Fatalf("gem %d group_id = %d with no folders left, want NULL", gem.AdvertiserID, *gem.GroupID)
		}
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
