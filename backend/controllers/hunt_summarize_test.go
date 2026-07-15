package controllers

import (
	"strings"
	"testing"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

func TestSummarizeRoundsIncludesRelevance(t *testing.T) {
	_, lines := summarizeRounds([]models.HuntRoundSummary{
		{RoundID: 7, TermCount: 12, FoundCount: 40, KeptCount: 5,
			RelevantCount: 2, OffTargetCount: 3},
		{RoundID: 8, TermCount: 4, FoundCount: 0, KeptCount: 0},
	})
	if len(lines) != 2 {
		t.Fatalf("lines = %d, want 2", len(lines))
	}
	if !strings.Contains(lines[0], "5 kept (2 relevant, 3 off-target)") {
		t.Fatalf("line 0 missing relevance: %q", lines[0])
	}
	if strings.Contains(lines[1], "relevant") {
		t.Fatalf("line 1 should omit zero relevance: %q", lines[1])
	}
}
