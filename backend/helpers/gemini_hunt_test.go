package helpers

import (
	"reflect"
	"strings"
	"testing"

	"google.golang.org/genai"
)

func intp(v int) *int { return &v }

func TestClampHuntSettings(t *testing.T) {
	cases := []struct {
		name string
		in   HuntSettings
		want map[string]int
	}{
		{"empty", HuntSettings{}, map[string]int{}},
		{
			"in-bounds",
			HuntSettings{PageCap: intp(40), Patience: intp(6), ListingPages: intp(12)},
			map[string]int{"page_cap": 40, "patience": 6, "listing_pages": 12},
		},
		{
			"below-bounds",
			HuntSettings{PageCap: intp(0), Patience: intp(0), ListingPages: intp(0)},
			map[string]int{"page_cap": 1, "patience": 1, "listing_pages": 1},
		},
		{
			"above-bounds",
			HuntSettings{PageCap: intp(500), Patience: intp(99), ListingPages: intp(99)},
			map[string]int{"page_cap": 100, "patience": 10, "listing_pages": 30},
		},
		{
			"only-page-cap",
			HuntSettings{PageCap: intp(30)},
			map[string]int{"page_cap": 30},
		},
		{
			"only-patience",
			HuntSettings{Patience: intp(4)},
			map[string]int{"patience": 4},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := ClampHuntSettings(c.in)
			if !reflect.DeepEqual(got, c.want) {
				t.Errorf("ClampHuntSettings(%+v) = %v, want %v", c.in, got, c.want)
			}
		})
	}
}

func TestPlanFromArgs(t *testing.T) {
	args := map[string]any{
		"terms": []any{
			"Sleep Stories App", // will lowercase
			"ai bedtime audio",
			"  ", // junk / empty
			"sleep stories app", // dup after normalize
			"calm competitor",   // tried
		},
		"competitor_apps": []any{"Calm", "Headspace", "BetterSleep", "Slumber"}, // cap 3
		"settings": map[string]any{
			"page_cap":      float64(40), // JSON number
			"patience":      float64(20), // above bound -> 10
			"listing_pages": float64(12),
		},
	}
	plan := planFromArgs(args, []string{"calm competitor"})

	wantTerms := []string{"sleep stories app", "ai bedtime audio"}
	if !reflect.DeepEqual(plan.Terms, wantTerms) {
		t.Errorf("terms = %v, want %v", plan.Terms, wantTerms)
	}
	if len(plan.CompetitorApps) != 3 {
		t.Errorf("competitor_apps = %v, want 3", plan.CompetitorApps)
	}
	wantSettings := map[string]int{"page_cap": 40, "patience": 10, "listing_pages": 12}
	if !reflect.DeepEqual(plan.Settings, wantSettings) {
		t.Errorf("settings = %v, want %v", plan.Settings, wantSettings)
	}
}

// TestPlanFromArgsProposeVsStartStrip pins the C1 fix: propose_round strips
// already-tried terms (nil tried is the start path, which keeps them so a
// "run those again" command isn't stripped to zero).
func TestPlanFromArgsProposeVsStartStrip(t *testing.T) {
	args := map[string]any{"terms": []any{"sleep stories app", "calm competitor"}}

	// propose_round path: tried terms are stripped.
	propose := planFromArgs(args, []string{"calm competitor"})
	if !reflect.DeepEqual(propose.Terms, []string{"sleep stories app"}) {
		t.Errorf("propose terms = %v, want tried stripped", propose.Terms)
	}

	// start_round path (nil tried): re-run terms are kept.
	start := planFromArgs(args, nil)
	if !reflect.DeepEqual(start.Terms, []string{"sleep stories app", "calm competitor"}) {
		t.Errorf("start terms = %v, want tried kept", start.Terms)
	}
}

// TestBuildStartTurnEmptyPlanNoLog pins the C1 tool_log guard: an empty plan
// must not append a "started a round" entry; a runnable one must.
func TestBuildStartTurnEmptyPlanNoLog(t *testing.T) {
	empty := buildStartTurn("", []string{"checked round 1's report"}, HuntPlan{})
	for _, e := range empty.ToolLog {
		if strings.Contains(e, "started a round") {
			t.Errorf("empty plan logged a started round: %v", empty.ToolLog)
		}
	}
	if empty.Start == nil {
		t.Errorf("start turn must still carry the (empty) plan for the controller guard")
	}

	runnable := buildStartTurn("", nil, HuntPlan{Terms: []string{"budget app"}})
	found := false
	for _, e := range runnable.ToolLog {
		if strings.Contains(e, "started a round") {
			found = true
		}
	}
	if !found {
		t.Errorf("runnable plan did not log a started round: %v", runnable.ToolLog)
	}
}

func TestPlanFromArgsNoSettings(t *testing.T) {
	plan := planFromArgs(map[string]any{"terms": []any{"budget app"}}, nil)
	if len(plan.Settings) != 0 {
		t.Errorf("settings = %v, want empty", plan.Settings)
	}
	if !reflect.DeepEqual(plan.Terms, []string{"budget app"}) {
		t.Errorf("terms = %v", plan.Terms)
	}
}

func TestDescribePlan(t *testing.T) {
	withSettings := HuntPlan{
		Terms:    []string{"a", "b", "c"},
		Settings: map[string]int{"page_cap": 30, "patience": 6},
	}
	if got := describePlan("started a round", withSettings); got != "started a round — 3 terms · depth 30 · patience 6" {
		t.Errorf("with settings = %q", got)
	}

	noSettings := HuntPlan{Terms: []string{"a", "b"}}
	if got := describePlan("proposed a round", noSettings); got != "proposed a round — 2 terms" {
		t.Errorf("no settings = %q", got)
	}
}

func TestBuildHuntTurnContents(t *testing.T) {
	in := HuntTurnInput{
		Message: "run it 40 deep",
		History: []HuntHistoryTurn{
			{Role: "user", Content: "find sleep apps"},
			{Role: "assistant", Content: "here are terms"},
		},
		TriedTerms:     []string{"sleep stories app"},
		RoundSummaries: []string{"round 1: 12 terms, 40 found, 5 kept"},
	}
	contents := buildHuntTurnContents(in)
	if len(contents) != 3 {
		t.Fatalf("got %d contents, want 3", len(contents))
	}
	if contents[0].Role != genai.RoleUser {
		t.Errorf("content[0] role = %q, want user", contents[0].Role)
	}
	if contents[1].Role != genai.RoleModel {
		t.Errorf("content[1] role = %q, want model", contents[1].Role)
	}
	if contents[2].Role != genai.RoleUser {
		t.Errorf("content[2] role = %q, want user", contents[2].Role)
	}
	last := contents[2].Parts[0].Text
	if !strings.Contains(last, "run it 40 deep") {
		t.Errorf("final turn missing message: %q", last)
	}
	if !strings.Contains(last, "sleep stories app") {
		t.Errorf("final turn missing tried terms: %q", last)
	}
	if !strings.Contains(last, "round 1: 12 terms") {
		t.Errorf("final turn missing round summary: %q", last)
	}
}

func TestNewGeminiHelperHuntModelDefaults(t *testing.T) {
	t.Setenv("GEMINI_API_KEY", "test-key")
	t.Setenv("GEMINI_MODEL", "")
	t.Setenv("GEMINI_HUNT_MODEL", "")
	t.Setenv("AGF_HUNT_WEB_TOOLS", "")
	h := NewGeminiHelper()
	if h == nil {
		t.Fatal("helper nil with API key set")
	}
	if h.huntModel != "gemini-3.5-flash" {
		t.Fatalf("huntModel = %q, want gemini-3.5-flash", h.huntModel)
	}
	if !h.huntWebTools {
		t.Fatal("huntWebTools should default on")
	}
	if h.model != "gemini-2.5-flash" {
		t.Fatalf("model = %q, want gemini-2.5-flash (unchanged)", h.model)
	}
}

func TestNewGeminiHelperHuntOverrides(t *testing.T) {
	t.Setenv("GEMINI_API_KEY", "test-key")
	t.Setenv("GEMINI_HUNT_MODEL", "gemini-9-test")
	t.Setenv("AGF_HUNT_WEB_TOOLS", "0")
	h := NewGeminiHelper()
	if h.huntModel != "gemini-9-test" {
		t.Fatalf("huntModel = %q", h.huntModel)
	}
	if h.huntWebTools {
		t.Fatal("AGF_HUNT_WEB_TOOLS=0 must disable web tools")
	}
}
