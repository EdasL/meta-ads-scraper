package helpers

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
	"google.golang.org/genai"
)

type adRoundTrip func(*http.Request) (*http.Response, error)

func (f adRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestAdJudgeContractAndFailureModes(t *testing.T) {
	// Short but meaningful copy must reach Jev rather than a word-count cutoff.
	ad := AdResearchEvidence{ID: "1:42", Text: "Gentle guided fitness for beginners."}
	for _, tc := range []struct {
		name    string
		prob    float64
		status  int
		invalid bool
		want    string
	}{
		{"match", .96, 200, false, "match"}, {"mismatch", .03, 200, false, "mismatch"},
		{"ambiguous", .5, 200, false, "uncertain"}, {"provider down", .96, 503, false, "uncertain"},
		{"malformed", .96, 200, true, "uncertain"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			client := &http.Client{Transport: adRoundTrip(func(r *http.Request) (*http.Response, error) {
				require.Equal(t, "https://api.typesafe.ai/v1/systemone", r.URL.String())
				require.Equal(t, "Bearer test-key", r.Header.Get("Authorization"))
				var request map[string]any
				require.NoError(t, json.NewDecoder(r.Body).Decode(&request))
				require.Equal(t, JevAdModel, request["model"])
				state := request["state"].(map[string]any)
				require.Len(t, state, 2)
				require.Equal(t, ad.Text, state["ad_copy"])
				require.NotEmpty(t, state["target"])
				require.Len(t, request["questions"], 3)
				answers := map[string]any{}
				for _, key := range []string{"clear_offer", "offers_need", "audience_conflict"} {
					p := tc.prob
					if key == "audience_conflict" {
						p = 1 - tc.prob
					}
					if key == "clear_offer" {
						p = .99
					}
					answers[key] = map[string]any{"type": "noul", "noul": p}
				}
				if tc.invalid {
					answers["offers_need"] = map[string]any{"type": "noul", "noul": 2}
				}
				body, _ := json.Marshal(map[string]any{"model": JevAdModel, "answers": answers})
				return &http.Response{StatusCode: tc.status, Body: io.NopCloser(strings.NewReader(string(body))), Header: make(http.Header)}, nil
			})}
			got, err := (JevAdJudge{APIKey: "test-key", Client: client}).JudgeAds(context.Background(), AdJudgeTarget{Need: "accessible fitness", Audience: "beginners"}, []AdResearchEvidence{ad})
			require.NoError(t, err)
			require.Len(t, got, 1)
			require.Equal(t, tc.want, got[0].Verdict)
			require.Equal(t, ad.ID, got[0].ID)
			require.True(t, got[0].TextOnly)
		})
	}
	got, err := (JevAdJudge{APIKey: "key"}).JudgeAds(context.Background(), AdJudgeTarget{Need: "fitness", Audience: "beginners"}, []AdResearchEvidence{{ID: "1:2", Text: ""}})
	require.NoError(t, err)
	require.Equal(t, "insufficient_text", got[0].Reason)
	_, err = (JevAdJudge{}).JudgeAds(context.Background(), AdJudgeTarget{Need: "fitness", Audience: "beginners"}, []AdResearchEvidence{ad})
	require.Error(t, err)
}

func TestSimilarAdBrandsProviderContract(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		require.NoError(t, json.NewDecoder(r.Body).Decode(&body))
		b, _ := json.Marshal(body)
		require.Contains(t, string(b), "unverified research leads")
		require.Contains(t, string(b), "source evidence")
		text := `{"brands":[{"name":"Source","reason":"exclude"},{"name":"Peer","reason":"shared need","verified":true},{"name":"peer","reason":"duplicate"},{"name":"Excluded","reason":"exclude"}],"search_terms":["gentle movement","gentle movement"]}`
		require.NoError(t, json.NewEncoder(w).Encode(map[string]any{"candidates": []any{map[string]any{"content": map[string]any{"parts": []any{map[string]any{"text": text}}}}}}))
	}))
	defer srv.Close()
	c, err := genai.NewClient(context.Background(), &genai.ClientConfig{APIKey: "test", Backend: genai.BackendGeminiAPI, HTTPOptions: genai.HTTPOptions{BaseURL: srv.URL}, HTTPClient: srv.Client()})
	require.NoError(t, err)
	h := &GeminiHelper{client: c, model: "gemini-2.5-flash"}
	out, err := h.FindSimilarAdBrands(context.Background(), AdResearchBrief{References: []AdResearchEvidence{{ID: "1:2", Text: "source evidence"}}}, "Source", []string{"Excluded"}, 5)
	require.NoError(t, err)
	require.Len(t, out.Brands, 1)
	require.Equal(t, "Peer", out.Brands[0].Name)
	require.False(t, out.Brands[0].Verified)
	require.Equal(t, []string{"gentle movement"}, out.SearchTerms)
	require.Equal(t, "selected_ads", out.EvidenceSource)
}

func TestAdEvidenceIncludesAdvertorialOffer(t *testing.T) {
	text, truncated := AdEvidenceText("opening " + strings.Repeat("story ", 2000) + " Actual supplement offer at the end")
	require.True(t, truncated)
	require.Contains(t, text, "opening")
	require.Contains(t, text, "Actual supplement offer at the end")
}

func TestAdJudgeRejectsIncompatibleAudience(t *testing.T) {
	client := &http.Client{Transport: adRoundTrip(func(*http.Request) (*http.Response, error) {
		answers := map[string]any{}
		for key, p := range map[string]float64{"clear_offer": .97, "offers_need": .95, "audience_conflict": .93} {
			answers[key] = map[string]any{"type": "noul", "noul": p}
		}
		body, _ := json.Marshal(map[string]any{"model": JevAdModel, "answers": answers})
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(string(body))), Header: make(http.Header)}, nil
	})}
	ads := []AdResearchEvidence{{ID: "2:3", Text: "Professional bilateral stimulation equipment for therapists managing their clinical practice and client sessions."}}
	out, err := (JevAdJudge{APIKey: "test", Client: client}).JudgeAds(context.Background(), AdJudgeTarget{Need: "Consumer relaxation", Audience: "Consumers"}, ads)
	require.NoError(t, err)
	require.Equal(t, "mismatch", out[0].Verdict)
	require.Equal(t, "audience_conflict", out[0].Reason)
}

func TestAdJudgeTargetAndReviewPolicy(t *testing.T) {
	for _, target := range []AdJudgeTarget{{}, {Need: "fitness"}, {Need: "fitness", Audience: " "}, {Need: strings.Repeat("é", 801), Audience: "consumers"}} {
		require.Error(t, target.Validate())
	}
	for _, tc := range []struct {
		need, conflict, offer float64
		want                  string
	}{
		{.9, .1, .9, "match"}, {.8, .8, .9, "mismatch"},
		{.1, .1, .9, "mismatch"}, {.5, .1, .9, "uncertain"},
		{.9, .5, .9, "uncertain"}, {.9, .1, .1, "uncertain"},
	} {
		verdict, _ := adJudgeVerdict(map[string]float64{"offers_need": tc.need, "audience_conflict": tc.conflict, "clear_offer": tc.offer})
		require.Equal(t, tc.want, verdict)
	}
}
