package helpers

import (
	"context"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// A real-provider quality gate. Positive abstentions count as missed matches,
// so returning uncertain for everything cannot pass. Labels precede predictions.
func TestAdJudgeQualityLive(t *testing.T) {
	if os.Getenv("AGF_JEV_QUALITY_LIVE") != "1" {
		t.Skip("set AGF_JEV_QUALITY_LIVE=1 and TYPESAFE_API_KEY")
	}
	var fixture struct {
		Cases []struct {
			Split, Domain, Expected string
			Target                  AdJudgeTarget
			Ad                      struct {
				ID   string `json:"ad_archive_id"`
				Name string `json:"page_name"`
				Copy string `json:"body_text"`
			}
		}
	}
	data, err := os.ReadFile("testdata/ad_judge_quality.json")
	require.NoError(t, err)
	require.NoError(t, json.Unmarshal(data, &fixture))
	require.Len(t, fixture.Cases, 25)
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	reports := []map[string]any{}
	for _, split := range []string{"development", "validation"} {
		positive, found, correct, total := 0, 0, 0, 0
		start := time.Now()
		for _, domain := range []string{"fitness", "calm"} {
			var target AdJudgeTarget
			var ads []AdResearchEvidence
			var expected, names []string
			for _, c := range fixture.Cases {
				if c.Split != split || c.Domain != domain {
					continue
				}
				target = c.Target
				copy, truncated := AdEvidenceText(c.Ad.Copy)
				ads = append(ads, AdResearchEvidence{ID: c.Ad.ID, Text: copy, Truncated: truncated})
				expected = append(expected, c.Expected)
				names = append(names, c.Ad.Name)
			}
			out, err := (JevAdJudge{APIKey: os.Getenv("TYPESAFE_API_KEY")}).JudgeAds(ctx, target, ads)
			require.NoError(t, err)
			require.Len(t, out, len(ads))
			for i, v := range out {
				require.Equal(t, ads[i].ID, v.ID)
				require.Len(t, v.Signals, 3, "provider failures must not count as abstentions")
				total++
				if v.Verdict == expected[i] {
					correct++
				}
				if expected[i] == "match" {
					positive++
					if v.Verdict == "match" {
						found++
					}
					assert.NotEqual(t, "mismatch", v.Verdict, names[i])
				} else if expected[i] == "mismatch" {
					// A negative abstention is still a miss in exact accuracy above.
					// It must never be promoted to a positive recommendation.
					assert.NotEqual(t, "match", v.Verdict, names[i])
				} else {
					assert.Equal(t, expected[i], v.Verdict, names[i])
				}
				t.Logf("%s/%s %s: expected=%s actual=%s signals=%v", split, domain, names[i], expected[i], v.Verdict, v.Signals)
				reports = append(reports, map[string]any{"split": split, "domain": domain, "target": target, "expected": expected[i], "judgement": v})
			}
		}
		// Preserve useful recall as well as rejection of the known hard negatives.
		minimumRecall := .5
		if split == "validation" {
			minimumRecall = .7
		}
		require.Positive(t, positive)
		assert.GreaterOrEqual(t, float64(found)/float64(positive), minimumRecall)
		assert.GreaterOrEqual(t, float64(correct)/float64(total), .8)
		t.Logf("%s: %d/%d exact, %d/%d positive recall, %s", split, correct, total, found, positive, time.Since(start))
	}
	if path := os.Getenv("AGF_JEV_QUALITY_REPORT"); path != "" {
		data, err := json.Marshal(reports)
		require.NoError(t, err)
		require.NoError(t, os.WriteFile(path, data, 0600))
	}
}
