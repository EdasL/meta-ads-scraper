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

	// Two niche budgets, checked by different passes.
	//
	//   seededCount — the decayed cap: pre-filled from the user's recent ratings,
	//     so a topic already rated to its cap in the window stops appearing.
	//   deckCount   — this request only, seeds ignored: the per-deck diversity
	//     floor (≤ cap cards per niche in one batch), which is what the queue
	//     guaranteed before seeding existed.
	//
	// Every admitted card increments BOTH, whichever budget gated it. Keeping
	// them in step is what makes the passes compose: a card let through by the
	// seeded passes still spends its per-deck slot (so the deck floor can't be
	// exceeded by mixing passes), and a card let through by the deck-floor pass
	// still spends its seeded slot (so a later pass can't double-spend a niche
	// the seeded budget had already written off). seededCount is always ≥
	// deckCount for a given niche, hence the pass order: strictest budget first.
	seededCount := map[string]int{}
	for k, v := range opts.SeedNicheCounts {
		if k != "" && v > 0 {
			seededCount[k] = v
		}
	}
	deckCount := map[string]int{}

	cursors := []int{0, 0, 0}
	got := []int{0, 0, 0}

	// tryTake pulls the next admissible candidate out of bucket b. counter picks
	// which niche budget gates the choice; nil means no cap at all. Advertiser
	// and app dedupe are unconditional in every pass.
	tryTake := func(b int, counter map[string]int) bool {
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
			if counter != nil && k != "" && counter[k] >= cap {
				continue
			}
			seenAdv[c.AdvertiserID] = true
			if c.AppID != nil {
				seenApp[*c.AppID] = true
			}
			if k != "" {
				seededCount[k]++
				deckCount[k]++
			}
			picked = append(picked, c)
			got[b]++
			return true
		}
		return false
	}

	// runPass round-robins the three buckets until the deck is full or no bucket
	// can yield another card. useTargets honours the exploit/uncertain/fresh
	// blend quotas; the fallback passes draw from wherever a card is available.
	runPass := func(useTargets bool, counter map[string]int) {
		for len(picked) < opts.Limit {
			progressed := false
			for b := 0; b < 3; b++ {
				if useTargets && got[b] >= targets[b] {
					continue
				}
				if tryTake(b, counter) {
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

	// Pass 1: the blend, under the decayed cap. Pass 2: same cap, targets
	// dropped, so a bucket that ran dry doesn't strand slots.
	runPass(true, seededCount)
	runPass(false, seededCount)

	// Pass 3: seeds exhausted the capped passes, so fall back to the per-deck
	// floor — this niche may be spent for the window, but the batch itself still
	// gets at most cap cards from it. Without this pass a single topic could take
	// the whole shortfall.
	//
	// Later passes reset the cursors: the earlier passes advanced them past
	// candidates they skipped, which are exactly the ones now admissible.
	if len(picked) < opts.Limit {
		cursors = []int{0, 0, 0}
		runPass(false, deckCount)
	}

	// Pass 4, last resort: no cap. Only reachable when the pool holds fewer than
	// limit cards spread over enough niches — e.g. everything collapsed into one
	// niche. An underfilled deck is worse than a repetitive one.
	if len(picked) < opts.Limit {
		cursors = []int{0, 0, 0}
		runPass(false, nil)
	}

	return picked
}
