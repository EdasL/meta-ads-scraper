package repositories

import (
	"math/rand"
	"sort"

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
	poolMultiplier = 4 // fresh candidates fetched = poolMultiplier * limit
)

// prefCandidate is one app considered for the review queue: a renderable card
// plus the selection inputs (uncertainty, source). Source is "scored" (has a
// preference_scores row) or "fresh" (in the matview, not yet scored for user).
type prefCandidate struct {
	models.PreferenceCardRow
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
	Rng           *rand.Rand
}

// nicheKey is the diversity-cap key: niche_keyword, else cohort, else exempt.
func nicheKey(c prefCandidate) string {
	if c.NicheKeyword != nil && *c.NicheKeyword != "" {
		return "n:" + *c.NicheKeyword
	}
	if c.Cohort != "" {
		return "c:" + c.Cohort
	}
	return "" // empty key is exempt from the cap (unknown niche)
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
	sort.SliceStable(exploit, func(i, j int) bool { return exploit[i].PLike > exploit[j].PLike })

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
	nicheCount := map[string]int{}
	cursors := []int{0, 0, 0}
	got := []int{0, 0, 0}

	tryTake := func(b int) bool {
		for cursors[b] < len(buckets[b]) {
			c := buckets[b][cursors[b]]
			cursors[b]++
			if seenAdv[c.AdvertiserID] {
				continue
			}
			k := nicheKey(c)
			if k != "" && nicheCount[k] >= cap {
				continue
			}
			seenAdv[c.AdvertiserID] = true
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
			if tryTake(b) {
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
			if tryTake(b) {
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

	return picked
}
