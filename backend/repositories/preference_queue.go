package repositories

import (
	"math"
	"math/rand"
	"sort"
	"strconv"

	"github.com/edaslakavicius/emerging-app-finder/backend/models"
)

// Serve-time queue blend constants. The review queue mixes the user's taste
// (exploit) with the model's most uncertain apps and brand-new arrivals so the
// deck stays diverse and never re-serves one cluster. See
// docs/superpowers/specs/2026-06-26-review-queue-diversity-design.md.
const (
	defaultPerNicheCap = 3    // max cards per niche_keyword in one batch
	exploitFrac        = 0.60 // share ranked by p_like
	uncertainFrac      = 0.25 // share ranked by uncertainty (p_like ≈ 0.5)
	// freshFrac is the remainder (~0.15): fresh-unscored + leftover scored.
	// exploitSampleWindow is how many top-p_like apps the taste bucket samples
	// from each request. Without this the top exploit cards are fully
	// deterministic and recur on every refresh until rated; sampling the top
	// window (weighted by p_like) keeps cards on-taste while varying the deck.
	exploitSampleWindow = 40
)

// weightedSampleByRank returns in reordered by weighted random sampling without
// replacement (Efraimidis–Spirakis), weight = RankScore (p_like after crowding
// demotion). Higher rank tends toward the front but the order varies per rng.
// The returned slice is a permutation of in (same elements), never drops or
// duplicates.
func weightedSampleByRank(in []prefCandidate, rng *rand.Rand) []prefCandidate {
	type keyed struct {
		c prefCandidate
		k float64
	}
	ks := make([]keyed, len(in))
	for i, c := range in {
		w := c.RankScore
		if w < 1e-6 {
			w = 1e-6 // floor so a 0 p_like still gets a (tiny) chance, no div-by-zero
		}
		u := rng.Float64()
		if u <= 0 {
			u = 1e-12
		}
		ks[i] = keyed{c, math.Pow(u, 1.0/w)}
	}
	sort.SliceStable(ks, func(i, j int) bool { return ks[i].k > ks[j].k })
	out := make([]prefCandidate, len(in))
	for i := range ks {
		out[i] = ks[i].c
	}
	return out
}

// prefCandidate is one app considered for the review queue: a renderable card
// plus the selection inputs (rank score, uncertainty, source). RankScore is the
// exploit-bucket ordering key: p_like after crowding demotion (equal to PLike
// unless the app sits in a clone cluster). Source is "scored" (has a content
// embedding, scored at serve time) or "fresh" (no embedding yet).
type prefCandidate struct {
	models.PreferenceCardRow
	RankScore   float64
	Uncertainty float64
	Source      string
}

// queueOpts parameterises assembleQueue. Rng makes selection deterministic in
// tests and varied per request in production (a fresh source each call).
type queueOpts struct {
	Limit         int
	PerNicheCap   int
	ExploitFrac   float64
	UncertainFrac float64
	// SeedNicheCounts pre-fills the per-niche counter (decayed cap: the user's
	// recent ratings count against each niche's budget, so a topic that already
	// filled its cap in the window stops appearing until the window rolls off).
	SeedNicheCounts map[string]int
	Rng             *rand.Rand
}

// nicheKeyFrom is the diversity-cap key for raw fields: embedding topic, else
// niche_keyword, else cohort, else exempt. Topic first: keyword "niches"
// scatter clone clusters across dozens of keys (spec 2026-08-03). Shared by
// candidates (nicheKey) and the recent-ratings seed (preference_repo).
func nicheKeyFrom(topicID *int, nicheKeyword *string, cohort string) string {
	if topicID != nil {
		return "t:" + strconv.Itoa(*topicID)
	}
	if nicheKeyword != nil && *nicheKeyword != "" {
		return "n:" + *nicheKeyword
	}
	if cohort != "" {
		return "c:" + cohort
	}
	return "" // empty key is exempt from the cap (unknown niche)
}

func nicheKey(c prefCandidate) string {
	return nicheKeyFrom(c.TopicID, c.NicheKeyword, c.Cohort)
}

// assembleQueue blends exploit/uncertain/fresh buckets to opts.Limit, enforces a
// per-niche cap, dedupes advertisers, and randomises within buckets. Pure: no DB,
// deterministic given opts.Rng.
func assembleQueue(cands []prefCandidate, opts queueOpts) []prefCandidate {
	if opts.Limit <= 0 || len(cands) == 0 {
		return nil
	}

	// Shuffle once so equal-key ties resolve randomly (per-request variety),
	// then stable-sort within each bucket by the bucket's priority.
	shuffled := make([]prefCandidate, len(cands))
	copy(shuffled, cands)
	opts.Rng.Shuffle(len(shuffled), func(i, j int) {
		shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
	})

	var scored, fresh []prefCandidate
	for _, c := range shuffled {
		if c.Source == "fresh" {
			fresh = append(fresh, c)
		} else {
			scored = append(scored, c)
		}
	}

	exploit := append([]prefCandidate(nil), scored...)
	sort.SliceStable(exploit, func(i, j int) bool { return exploit[i].RankScore > exploit[j].RankScore })
	// Weighted-sample the top window so the taste cards vary per request instead
	// of recurring deterministically; the tail beyond the window stays in rank
	// order (it only matters when the deck is short on candidates).
	if len(exploit) > 1 {
		w := exploitSampleWindow
		if w > len(exploit) {
			w = len(exploit)
		}
		exploit = append(weightedSampleByRank(exploit[:w], opts.Rng), exploit[w:]...)
	}

	uncertain := append([]prefCandidate(nil), scored...)
	sort.SliceStable(uncertain, func(i, j int) bool { return uncertain[i].Uncertainty > uncertain[j].Uncertainty })

	// Bucket order: each bucket is a queue we draw from in round-robin. Fresh
	// pulls from fresh-unscored first, then leftover scored (already shuffled).
	freshQ := append(append([]prefCandidate(nil), fresh...), scored...)

	buckets := [][]prefCandidate{exploit, uncertain, freshQ}
	// Draw targets per bucket; remainder goes to fresh.
	nExploit := int(float64(opts.Limit)*opts.ExploitFrac + 0.5)
	nUncertain := int(float64(opts.Limit)*opts.UncertainFrac + 0.5)
	nFresh := opts.Limit - nExploit - nUncertain
	if nFresh < 0 {
		nFresh = 0
	}
	targets := []int{nExploit, nUncertain, nFresh}

	cap := opts.PerNicheCap
	if cap <= 0 {
		cap = defaultPerNicheCap
	}

	picked := make([]prefCandidate, 0, opts.Limit)
	seenAdv := map[int]bool{}
	seenApp := map[int]bool{} // collapse multiple Meta pages of the same app
	nicheCount := map[string]int{}
	for k, v := range opts.SeedNicheCounts {
		if k != "" && v > 0 {
			nicheCount[k] = v
		}
	}
	cursors := []int{0, 0, 0}
	got := []int{0, 0, 0}

	tryTake := func(b int, capped bool) bool {
		for cursors[b] < len(buckets[b]) {
			c := buckets[b][cursors[b]]
			cursors[b]++
			if seenAdv[c.AdvertiserID] {
				continue
			}
			if c.AppID != nil && seenApp[*c.AppID] {
				continue
			}
			k := nicheKey(c)
			if capped && k != "" && nicheCount[k] >= cap {
				continue
			}
			seenAdv[c.AdvertiserID] = true
			if c.AppID != nil {
				seenApp[*c.AppID] = true
			}
			if k != "" {
				nicheCount[k]++
			}
			picked = append(picked, c)
			got[b]++
			return true
		}
		return false
	}

	// Round-robin to interleave buckets until each hits its target.
	for len(picked) < opts.Limit {
		progressed := false
		for b := 0; b < 3; b++ {
			if got[b] >= targets[b] {
				continue
			}
			if tryTake(b, true) {
				progressed = true
				if len(picked) >= opts.Limit {
					break
				}
			}
		}
		if !progressed {
			break
		}
	}

	// Underfill pass: ignore targets, fill from any bucket (still capped/deduped).
	for len(picked) < opts.Limit {
		progressed := false
		for b := 0; b < 3; b++ {
			if tryTake(b, true) {
				progressed = true
				if len(picked) >= opts.Limit {
					break
				}
			}
		}
		if !progressed {
			break
		}
	}

	// Cap-free last resort: a heavily seeded cap (or a candidate pool collapsed
	// into few niches) must never underfill the deck. Cursors reset because the
	// capped passes already consumed them past skipped candidates.
	if len(picked) < opts.Limit {
		cursors = []int{0, 0, 0}
		for len(picked) < opts.Limit {
			progressed := false
			for b := 0; b < 3; b++ {
				if tryTake(b, false) {
					progressed = true
					if len(picked) >= opts.Limit {
						break
					}
				}
			}
			if !progressed {
				break
			}
		}
	}

	return picked
}
