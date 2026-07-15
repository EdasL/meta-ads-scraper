# Hunt Competitor-Finding Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make hunts on niche intents surface real competitor apps: Peon proposes ad-copy-style terms (no "app" token), sees per-term outcomes, and the competitor lane resolves the right page, pulls globally, and captures web-funnel advertisers.

**Architecture:** Go side (backend/helpers/gemini.go, repositories, controllers) fixes the prompt rules, caps, and round-summary feedback. Python side (src/agf/discovery/hunt.py + db/hunts.py + funnel_classify.py) reworks the competitor pull (disambiguated typeahead, real telemetry, global countries) and adds web-funnel capture to hunt result persistence. No DB migration needed (hunt_results.store/store_id are TEXT, candidate_id nullable; migration 158 already applied to prod on 07-15).

**Tech Stack:** Go 1.x + genai + gorm + testify; Python 3 + SQLAlchemy text() + pytest; Postgres test DB.

**Spec:** docs/superpowers/specs/2026-07-15-hunt-competitor-finding-design.md (committed cd71a39). Research grounding: docs/research/2026-07-15-hunt-term-strategy.md.

## Global Constraints

- Product copy voice (CLAUDE.md): plain, clear, professional. Prompt text IS product-adjacent copy — no marketing fluff. Keep product nouns intact: Field Desk, GemScore, cohort.
- Python DB tests need `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it`; Go repo tests need `AGF_IT_DB_URL` (same DB). A skip is NOT a pass — grep the summary.
- ONE test consumer per shared test DB at a time: never run the Go suite and the Python suite concurrently; the final zero-failed gate run happens LAST, SOLO, bare (no pipes).
- A PARALLEL Claude session shares this repo: re-read each file immediately before editing (a stale-read Edit error IS the collision signal), `git add` EXPLICIT paths only, never `git add -A`.
- All commits on main directly (repo convention). Commit after each task.
- Migrations: none in this plan. If one becomes necessary, no bare `%` anywhere in the file, including comments.
- DEV subagents run on Opus.

---

### Task 1: Go — Peon term-style rules, "app"-token ban, competitor cap 10

**Files:**
- Modify: `backend/helpers/gemini.go` (huntSystemInstruction :203-209, schema descriptions :233-242, capStrings call :300, cleanHuntTerms :304-327, huntAgentInstruction :463-504, planSchema :712-713, planFromArgs competitor cap ~:731)
- Test: `backend/helpers/gemini_hunt_test.go`

**Interfaces:**
- Consumes: existing `cleanHuntTerms(terms, tried []string) []string`, `capStrings(in []string, max int) []string`, `planFromArgs(args map[string]any, tried []string) HuntPlan`.
- Produces: new pure helper `hasBannedTermToken(term string) bool` (used only inside gemini.go); competitor cap becomes 10 in BOTH proposal paths; no signature changes.

- [ ] **Step 1: Write the failing tests**

Append to `backend/helpers/gemini_hunt_test.go`:

```go
func TestCleanHuntTermsBansAppTokens(t *testing.T) {
	got := cleanHuntTerms([]string{
		"running app for women", // 'app' token → dropped
		"fitness apps",          // 'apps' token → dropped
		"meditation application", // 'application' token → dropped
		"job applications tracker", // 'applications' token → dropped
		"run walk beginner plan",   // kept
		"happy applesauce recipes", // 'app' only as substring → kept
	}, nil)
	want := []string{"run walk beginner plan", "happy applesauce recipes"}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}

func TestPlanFromArgsCompetitorCapTen(t *testing.T) {
	names := make([]any, 0, 12)
	for i := 0; i < 12; i++ {
		names = append(names, fmt.Sprintf("brand%d", i))
	}
	plan := planFromArgs(map[string]any{
		"terms":           []any{"run walk beginner plan"},
		"intent":          "running apps for women",
		"competitor_apps": names,
	}, nil)
	if len(plan.CompetitorApps) != 10 {
		t.Fatalf("competitor cap = %d, want 10", len(plan.CompetitorApps))
	}
}
```

(`fmt` is already imported in the test file; if not, add it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && go test ./helpers/ -run 'TestCleanHuntTermsBansAppTokens|TestPlanFromArgsCompetitorCapTen' -v`
Expected: FAIL — banned terms survive cleanHuntTerms; competitor cap is 3.

- [ ] **Step 3: Implement**

3a. Add the banned-token helper directly above `cleanHuntTerms` (needs `"unicode"` added to the imports):

```go
// bannedTermTokens are junk magnets in Ad Library keyword search: junk
// advertisers keyword-stuff them into every creative, so any term containing
// them returns drama/e-commerce noise (measured 2026-07-15,
// docs/research/2026-07-15-hunt-term-strategy.md).
var bannedTermTokens = map[string]struct{}{
	"app": {}, "apps": {}, "application": {}, "applications": {},
}

// hasBannedTermToken reports whether any letter/digit token of the (already
// lowercased) term is banned. Substrings do not count: 'applesauce' passes,
// 'fitness apps' does not.
func hasBannedTermToken(term string) bool {
	tokens := strings.FieldsFunc(term, func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsNumber(r)
	})
	for _, tok := range tokens {
		if _, banned := bannedTermTokens[tok]; banned {
			return true
		}
	}
	return false
}
```

3b. In `cleanHuntTerms`, after the empty check (`if norm == "" { continue }`), add:

```go
		if hasBannedTermToken(norm) {
			continue
		}
```

Also update its doc comment first line to: `// cleanHuntTerms lowercases+trims terms, drops empties and banned-token terms, dedups (and drops any term already tried, matched case-insensitively), and caps the list at 15.`

3c. Replace `huntSystemInstruction` (one-shot path) entirely:

```go
const huntSystemInstruction = "You are Peon, Field Desk's discovery agent. " +
	"You brainstorm Meta Ad Library search terms to find emerging mobile apps. " +
	"Ad Library keyword search matches AD COPY text, not app categories: terms " +
	"must read like the benefit phrases an advertiser writes in an ad " +
	"('womens running coach', 'run walk beginner plan') — short, plain, 2-4 " +
	"content words, no brand names. Never use the words 'app', 'apps', or " +
	"'application' in a term: junk advertisers keyword-stuff them, and such " +
	"terms return drama and e-commerce noise. For gendered or narrowly-" +
	"qualified niches, prefer neutral benefit terms — the target apps usually " +
	"advertise neutrally, and results are judged against the founder's intent " +
	"afterwards. Brand or app names the user mentions belong in " +
	"competitor_apps, never in terms. Never repeat a term listed as already " +
	"tried. Reply text is 1-3 plain sentences on the angles you chose. " +
	"Propose 8-15 terms."
```

3d. In `huntAgentInstruction`, replace the second paragraph (currently `"Search terms are generic niche queries a user would type (e.g. 'sleep stories app', " ... "3 competitor apps.\n"`) with:

```go
	"Ad Library keyword search matches AD COPY text, not app categories. Terms must read " +
	"like the benefit phrases an advertiser writes in an ad ('womens running coach', " +
	"'run walk beginner plan', 'couch to 5k plan') — short, plain, 2-4 content words. " +
	"Never use the words 'app', 'apps', or 'application' in a term: junk advertisers " +
	"keyword-stuff them, and such terms return drama and e-commerce noise. For gendered " +
	"or narrowly-qualified niches, prefer neutral benefit terms — the target apps " +
	"usually advertise neutrally, and the relevance judge filters results against the " +
	"intent. Any brand or app the founder names belongs in competitor_apps, never in " +
	"terms. At most 15 terms and at most 10 competitor apps.\n" +
```

3e. In `huntAgentInstruction`, replace the "Prefer a few broad head terms..." paragraph (:497-499) with:

```go
	"Avoid broad '<niche> app' head terms and hyper-specific long-tail alike: broad " +
	"'app' heads are colonized by keyword-stuffing advertisers, and long-tail phrases " +
	"rarely match any ad copy. Specific natural ad-copy phrasing sits between the two. " +
	"When re-proposing after a round, read the per-term stats from get_round_report and " +
	"drop or reword terms with zero kept results or mostly off-target results.\n" +
```

3f. Caps and schema text:
- `GenerateHuntProposal` (:300): `out.CompetitorApps = capStrings(out.CompetitorApps, 10)` and update its doc comment ("caps competitors at 3" → "at 10").
- One-shot schema `terms` description (:235): `"8-15 benefit-phrase search queries. Short, plain, no brand names, never the word 'app'."`
- `planSchema()` (:712-713): terms description → `"Benefit-phrase search queries. Short, plain, no brand names, never the word 'app'. At most 15."`; competitor_apps description → `"Brand or app names the founder named as competitors. At most 10."`
- `planFromArgs` (~:731): `CompetitorApps: capStrings(stringSlice(args["competitor_apps"]), 10),` and update the func doc comment ("capped at 3" → "capped at 10").
- Agent tool schema competitor_apps description (:692): `"Brand or app names the founder named as competitors. At most 10."`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && go test ./helpers/ -v`
Expected: PASS, including all pre-existing gemini_hunt tests. If `TestPlanFromArgs` or `TestBuildHuntTurnContents` assert on old instruction text or a cap of 3, update those assertions to the new copy/cap — that is expected fallout, not a regression.

- [ ] **Step 5: Commit**

```bash
cd /Users/edaslakavicius/Projects/emerging-app-finder
git add backend/helpers/gemini.go backend/helpers/gemini_hunt_test.go
git commit -m "feat(hunt): Peon term-style rules — ban 'app' tokens, ad-copy phrasing, competitor cap 10"
```

---

### Task 2: Go — round summaries carry relevant/off-target counts

**Files:**
- Modify: `backend/repositories/hunt_repo.go` (HuntRoundSummaries :249-269)
- Modify: `backend/models/hunt.go` (HuntRoundSummary :89-97)
- Modify: `backend/controllers/hunt_controller.go` (summarizeRounds :671-694)
- Test: `backend/repositories/hunt_repo_test.go` (extend TestHuntRoundSummaries :296), `backend/controllers/hunt_summarize_test.go` (new)

**Interfaces:**
- Consumes: `models.HuntRoundSummary`, `summarizeRounds(summaries []models.HuntRoundSummary) (tried, lines []string)`.
- Produces: `HuntRoundSummary` gains `RelevantCount int` + `OffTargetCount int` (gorm columns `relevant_count`, `off_target_count`); summary lines gain `" (N relevant, M off-target)"` when either is non-zero.

- [ ] **Step 1: Write the failing tests**

New file `backend/controllers/hunt_summarize_test.go`:

```go
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
```

Extend `TestHuntRoundSummaries` in `backend/repositories/hunt_repo_test.go`: after its existing result-row inserts and assertions, insert two hunt_results rows with relevance set and assert the new counts. Follow the file's existing raw-SQL seeding style, e.g.:

```go
	must(g.Exec(`UPDATE frontend.hunt_results
		SET relevance = 'relevant' WHERE round_id = ? AND store_id = ?`,
		roundID, "id-a").Error)
	must(g.Exec(`UPDATE frontend.hunt_results
		SET relevance = 'off_target' WHERE round_id = ? AND store_id = ?`,
		roundID, "id-b").Error)
	sums, err = repositories.HuntRoundSummaries(ctx, g, huntID)
	require.NoError(t, err)
	require.Equal(t, 1, sums[0].RelevantCount)
	require.Equal(t, 1, sums[0].OffTargetCount)
```

(Adapt variable names + store_ids to the rows that test already seeds — read it first; it may seed via `upsertHuntResult` helpers or raw SQL.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && go test ./controllers/ -run TestSummarizeRoundsIncludesRelevance -v`
Expected: FAIL to compile (`RelevantCount` undefined).

- [ ] **Step 3: Implement**

3a. `backend/models/hunt.go` — add to `HuntRoundSummary`:

```go
	RelevantCount  int `json:"relevant_count" gorm:"column:relevant_count"`
	OffTargetCount int `json:"off_target_count" gorm:"column:off_target_count"`
```

3b. `backend/repositories/hunt_repo.go` — in HuntRoundSummaries' SELECT, after the kept_count subquery add:

```sql
			        (SELECT count(*) FROM frontend.hunt_results hr
			          WHERE hr.round_id = r.id AND hr.relevance = 'relevant') AS relevant_count,
			        (SELECT count(*) FROM frontend.hunt_results hr
			          WHERE hr.round_id = r.id AND hr.relevance = 'off_target') AS off_target_count
```

(and update the func doc comment: "per-round term/found/kept/relevance counts").

3c. `backend/controllers/hunt_controller.go` — in `summarizeRounds`, after the `", %d found, %d kept"` Fprintf add:

```go
		if s.RelevantCount > 0 || s.OffTargetCount > 0 {
			fmt.Fprintf(&b, " (%d relevant, %d off-target)", s.RelevantCount, s.OffTargetCount)
		}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && go test ./controllers/ -run TestSummarizeRoundsIncludesRelevance -v && AGF_IT_DB_URL=postgres://agf:agf@localhost:55433/agf_it go test ./repositories/ -run TestHuntRoundSummaries -v`
Expected: PASS both. (Confirm the repositories test is not skipped — a skip is not a pass.)

- [ ] **Step 5: Commit**

```bash
git add backend/models/hunt.go backend/repositories/hunt_repo.go backend/controllers/hunt_controller.go backend/controllers/hunt_summarize_test.go backend/repositories/hunt_repo_test.go
git commit -m "feat(hunt): round summaries carry relevant/off-target counts for Peon"
```

---

### Task 3: Python — per-term outcome aggregates into progress.terms

**Files:**
- Modify: `src/agf/db/hunts.py` (new helper after `round_summary_counts`)
- Modify: `src/agf/discovery/hunt.py` (`_Progress` gains merge_term_outcomes; phase 5 calls it in BOTH branches)
- Test: `tests/discovery/test_hunt.py`

**Interfaces:**
- Consumes: `frontend.hunt_results` columns source_term/triage/relevance; `_Progress._state["terms"]` entries shaped `{pages, found, new, stop}`.
- Produces: `term_outcome_stats(conn, round_id: int) -> dict[str, dict]` returning `{term: {"kept": int, "relevant": int, "off_target": int}}`; `_Progress.merge_term_outcomes(outcomes: dict) -> None` merging those keys into existing term entries (creating bare entries for unknown terms). RoundReport (Go) already ships `progress.terms` verbatim, so get_round_report picks these up with no Go change.

- [ ] **Step 1: Write the failing tests**

Add to `tests/discovery/test_hunt.py` (mirror the file's existing fixtures for engine/round setup — it already creates rounds and results; reuse its helpers):

```python
def test_term_outcome_stats_groups_by_source_term(hunt_round_with_results):
    # fixture seeds: 2 results for 'run coach' (1 kept+relevant, 1 vetoed),
    # 1 result for 'page:joggo' kept + off_target
    engine, round_id = hunt_round_with_results
    with begin(engine) as conn:
        out = term_outcome_stats(conn, round_id)
    assert out["run coach"] == {"kept": 1, "relevant": 1, "off_target": 0}
    assert out["page:joggo"] == {"kept": 1, "relevant": 0, "off_target": 1}


def test_progress_merge_term_outcomes_preserves_sweep_telemetry(engine, round_id):
    p = _Progress(engine, round_id, terms_total=1)
    p.term_stats("run coach", pages=4, found=2, new=1, stop="dry")
    p.merge_term_outcomes({"run coach": {"kept": 1, "relevant": 1, "off_target": 0},
                           "page:joggo": {"kept": 1, "relevant": 0, "off_target": 1}})
    terms = p._state["terms"]
    assert terms["run coach"] == {"pages": 4, "found": 2, "new": 1, "stop": "dry",
                                  "kept": 1, "relevant": 1, "off_target": 0}
    assert terms["page:joggo"] == {"kept": 1, "relevant": 0, "off_target": 1}
```

(Write the `hunt_round_with_results` seeding inline if no such fixture exists: insert a hunts row, a hunt_rounds row, and three hunt_results rows with the stated source_term/triage/relevance values, following the INSERT patterns already in this test file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt.py -k "term_outcome or merge_term_outcomes" -v`
Expected: FAIL with ImportError / AttributeError (term_outcome_stats, merge_term_outcomes not defined).

- [ ] **Step 3: Implement**

3a. `src/agf/db/hunts.py` — append:

```python
def term_outcome_stats(conn, round_id: int) -> dict:
    """Post-triage outcome tallies per source_term for one round: kept /
    relevant / off_target. Keys match progress.terms entries (competitor pulls
    appear as 'page:<name>'). NULL relevance counts as neither (fail-open
    unjudged, spec 2026-07-15)."""
    out = {}
    for row in conn.execute(text(
            "SELECT source_term, "
            "count(*) FILTER (WHERE triage = 'kept') AS kept, "
            "count(*) FILTER (WHERE relevance = 'relevant') AS relevant, "
            "count(*) FILTER (WHERE relevance = 'off_target') AS off_target "
            "FROM frontend.hunt_results WHERE round_id = :r "
            "GROUP BY source_term"), {"r": round_id}):
        out[row.source_term] = {"kept": row.kept, "relevant": row.relevant,
                                "off_target": row.off_target}
    return out
```

3b. `src/agf/discovery/hunt.py` — add to `_Progress` (after `term_stats`):

```python
    def merge_term_outcomes(self, outcomes: dict) -> None:
        """Merge post-triage tallies (kept/relevant/off_target) into the
        existing terms entries so Peon's get_round_report sees outcome per
        term, not just sweep telemetry."""
        with self._lock:
            terms = self._state.setdefault("terms", {})
            for term, vals in outcomes.items():
                terms.setdefault(term, {}).update(vals)
            self._flush()
```

3c. `src/agf/discovery/hunt.py` — import `term_outcome_stats` in the existing `from agf.db.hunts import (...)` block. In phase 5, in the `lookup_ios is not None` branch, after the enrich-enqueue `with begin(engine)` block and its progress.event, add (NOTE: merge_term_outcomes flushes progress via its own short txn, so the stats read must NOT wrap it — read first, close the txn, then merge):

```python
        with begin(engine) as conn:
            outcomes = term_outcome_stats(conn, round_id)
        progress.merge_term_outcomes(outcomes)
```

And in the `else:` branch, after the `with` block holding `stats.update(apply_triage_to_results(conn, round_id))` closes, add the same two lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt.py -v`
Expected: PASS all (including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/agf/db/hunts.py src/agf/discovery/hunt.py tests/discovery/test_hunt.py
git commit -m "feat(hunt): per-term kept/relevant/off-target outcomes merged into round progress"
```

---

### Task 4: Python — competitor lane rework (cap 10, disambiguation, real telemetry, global pulls)

**Files:**
- Modify: `src/agf/discovery/hunt.py` (constants block; new `_name_similarity` + `_pull_competitor` helpers; phase 0 loop :199-226)
- Test: `tests/discovery/test_hunt.py`

**Interfaces:**
- Consumes: `extractor.typeahead(query=...)` → list of `{page_id, page_name, page_like_count, ig_username}`; `extractor.search_listing(page_id=..., countries=..., max_pages=...)` → `(records, truncated)`; `_persist_page(engine, round_id, term, records, depth, stats, lock)` (Task 5 adds a `funnel_autopass` kwarg — this task calls it WITHOUT that kwarg; Task 5 flips the call site).
- Produces: `_pull_competitor(engine, extractor, round_id, name, progress, stats, lock, listing_pages) -> None`; telemetry `progress.term_stats("page:<name>", attempts, found, new, stop)` with stop ∈ `listing_ok | no_match | no_results | error` (the `pages` slot for competitor entries counts candidate PAGES ATTEMPTED, not SERP pages); constants `HUNT_COMPETITOR_CAP = 10`, `HUNT_COMPETITOR_TYPEAHEAD_TOP = 5`, `HUNT_COMPETITOR_ATTEMPTS = 3`, `HUNT_COMPETITOR_COUNTRIES` (broad list).

- [ ] **Step 1: Write the failing tests**

Add to `tests/discovery/test_hunt.py`, using the file's existing fake-extractor pattern (it fakes `search_page`; extend the fake with `typeahead` and `search_listing` async methods returning canned data):

```python
def test_pull_competitor_prefers_name_match_over_first_hit(hunt_engine_round):
    """Typeahead returns a big wrong page first (the Femmira Fashion failure);
    the fuzzy name match must pick the exact-name page instead."""
    engine, round_id = hunt_engine_round
    fake = FakeExtractor(
        typeahead_results=[
            {"page_id": "111", "page_name": "Femmira Fashion", "page_like_count": 2019},
            {"page_id": "222", "page_name": "Femmi", "page_like_count": 107},
        ],
        listing_by_page={"222": [make_app_ad(page_id="222", page_name="Femmi",
                                             store_id="id.femmi")],
                         "111": [make_app_ad(page_id="111", page_name="Femmira Fashion",
                                             store_id="id.fashion")]})
    stats = fresh_stats()
    progress = _Progress(engine, round_id, terms_total=0)
    _pull_competitor(engine, fake, round_id, "Femmi", progress, stats,
                     threading.Lock(), listing_pages=2)
    entry = progress._state["terms"]["page:Femmi"]
    assert entry["stop"] == "listing_ok"
    assert entry["found"] == 1
    assert fake.listing_calls[0] == "222"          # best name match tried first


def test_pull_competitor_falls_through_empty_pages(hunt_engine_round):
    engine, round_id = hunt_engine_round
    fake = FakeExtractor(
        typeahead_results=[
            {"page_id": "111", "page_name": "Joggo", "page_like_count": 48310},
            {"page_id": "222", "page_name": "Joggo Fan Club", "page_like_count": 10},
        ],
        listing_by_page={"111": [], "222": []})
    stats = fresh_stats()
    progress = _Progress(engine, round_id, terms_total=0)
    _pull_competitor(engine, fake, round_id, "Joggo", progress, stats,
                     threading.Lock(), listing_pages=2)
    entry = progress._state["terms"]["page:Joggo"]
    assert entry["stop"] == "no_results"
    assert entry["pages"] == 2                     # both candidates attempted


def test_pull_competitor_no_typeahead_match(hunt_engine_round):
    engine, round_id = hunt_engine_round
    fake = FakeExtractor(typeahead_results=[], listing_by_page={})
    stats = fresh_stats()
    progress = _Progress(engine, round_id, terms_total=0)
    _pull_competitor(engine, fake, round_id, "Gazella", progress, stats,
                     threading.Lock(), listing_pages=2)
    assert progress._state["terms"]["page:Gazella"]["stop"] == "no_match"


def test_run_hunt_round_pulls_up_to_ten_competitors(hunt_round_factory):
    """12 named competitors → 10 attempted (was 3)."""
    engine, round_id, fake = hunt_round_factory(
        competitors=[f"brand{i}" for i in range(12)])
    run_hunt_round(engine, [fake], round_id)
    assert fake.typeahead_queries == [f"brand{i}" for i in range(10)]


def test_competitor_listing_uses_global_countries(hunt_engine_round):
    engine, round_id = hunt_engine_round
    fake = FakeExtractor(
        typeahead_results=[{"page_id": "1", "page_name": "Movana",
                            "page_like_count": 17}],
        listing_by_page={"1": []})
    _pull_competitor(engine, fake, round_id, "Movana",
                     _Progress(engine, round_id, terms_total=0), fresh_stats(),
                     threading.Lock(), listing_pages=2)
    assert fake.listing_countries[0] == HUNT_COMPETITOR_COUNTRIES
```

(Adapt fixture/helper names to what `tests/discovery/test_hunt.py` already provides — it drives `run_hunt_round` with fakes today for the existing competitor-pull tests; extend those fakes with `typeahead_queries`, `listing_calls`, `listing_countries` recording lists. `make_app_ad` = whatever AdRecord factory the file already uses for store-linked ads.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt.py -k "pull_competitor or ten_competitors or global_countries" -v`
Expected: FAIL (ImportError: `_pull_competitor`, `HUNT_COMPETITOR_COUNTRIES` not defined).

- [ ] **Step 3: Implement**

3a. Constants block in `src/agf/discovery/hunt.py` (after HUNT_LISTING_PAGES), plus `import difflib` at the top:

```python
HUNT_COMPETITOR_CAP = 10            # matches the Go proposal cap (gemini.go)
HUNT_COMPETITOR_TYPEAHEAD_TOP = 5   # typeahead rows considered for disambiguation
HUNT_COMPETITOR_ATTEMPTS = 3        # candidate pages tried before giving up
# Competitor pulls are GLOBAL: founders name small niche apps that often run
# only EU/AU campaigns (design 2026-07-15). Term sweeps stay US-only.
HUNT_COMPETITOR_COUNTRIES = ["US", "GB", "CA", "AU", "DE", "FR", "ES", "IT",
                             "NL", "SE", "PL", "LT", "BR", "MX", "IN"]
```

3b. Helpers, above `run_hunt_round`:

```python
def _name_similarity(a: str, b: str) -> float:
    """Case/whitespace-insensitive fuzzy ratio for typeahead disambiguation."""
    na = " ".join((a or "").lower().split())
    nb = " ".join((b or "").lower().split())
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _pull_competitor(engine, extractor, round_id: int, name: str,
                     progress: "_Progress", stats: dict,
                     lock: threading.Lock, listing_pages: int) -> None:
    """Resolve one competitor name to its Ad Library page and pull its ads.

    Typeahead matches page NAME and ranks big pages first, so the top hit is
    often a look-alike (live finding 2026-07-15: 'Femmi' -> a fashion page;
    the real app sat at index 3). Rank the top rows by fuzzy similarity to the
    entered name (likes break ties) and try the best few until one yields
    results. Telemetry: the 'pages' slot counts candidate pages ATTEMPTED;
    stop is one of listing_ok | no_match | no_results | error."""
    key = f"page:{name}"
    progress.event(f"Resolving {name}'s ad page")
    try:
        pages = asyncio.run(extractor.typeahead(query=name))
    except ExtractError:
        progress.event(f"Could not resolve {name} — skipped")
        with lock:
            stats["errors"] += 1
        progress.term_stats(key, 0, 0, 0, "error")
        return
    ranked = sorted(
        pages[:HUNT_COMPETITOR_TYPEAHEAD_TOP],
        key=lambda p: (_name_similarity(name, p.get("page_name")),
                       p.get("page_like_count") or 0),
        reverse=True)
    if not ranked:
        progress.event(f"No Ad Library page found for {name}")
        progress.term_stats(key, 0, 0, 0, "no_match")
        return
    attempts = 0
    for page in ranked[:HUNT_COMPETITOR_ATTEMPTS]:
        label = page.get("page_name") or name
        progress.event(f"Pulling ads from '{label}'")
        try:
            records, _truncated = asyncio.run(extractor.search_listing(
                page_id=page["page_id"],
                countries=HUNT_COMPETITOR_COUNTRIES,
                max_pages=listing_pages))
        except ExtractError:
            with lock:
                stats["errors"] += 1
            progress.term_stats(key, attempts, 0, 0, "error")
            return
        attempts += 1
        found, new = _persist_page(engine, round_id, key, records, 1,
                                   stats, lock)
        if found:
            with lock:
                stats["competitor_pages"] += 1
            progress.page(0, key, 1, found, new)
            progress.term_stats(key, attempts, found, new, "listing_ok")
            return
    progress.term_stats(key, attempts, 0, 0, "no_results")
```

3c. Replace the phase-0 loop body (:203-226) with:

```python
    for name in competitors[:HUNT_COMPETITOR_CAP]:
        _pull_competitor(engine, extractors[0], round_id, name, progress,
                         stats, lock, knobs["listing_pages"])
```

(The `raw`/`competitors`/`stats["competitor_pages"] = 0` lines above it stay. Delete the old inline typeahead/`pages[0]`/hardcoded `term_stats(..., 1, ..., "listing")` block entirely. Update the phase-0 comment to mention disambiguated resolution + global countries.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt.py -v`
Expected: PASS all. Pre-existing competitor-pull tests asserting `stop == "listing"` or `[:3]` behavior must be UPDATED to the new telemetry vocabulary/cap — expected fallout.

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/hunt.py tests/discovery/test_hunt.py
git commit -m "feat(hunt): competitor lane — cap 10, fuzzy typeahead disambiguation, real telemetry, global pulls"
```

---

### Task 5: Python — capture web funnels as hunt results

**Files:**
- Modify: `src/agf/discovery/funnel_classify.py` (funnel_signal :116-125)
- Modify: `src/agf/db/hunts.py` (upsert_hunt_result :51-59)
- Modify: `src/agf/discovery/hunt.py` (_persist_page :94-130; competitor call site from Task 4; module docstring; imports)
- Test: `tests/discovery/test_hunt.py`, `tests/discovery/test_funnel_classify.py` (if it exists — else the funnel_signal case lives in test_hunt.py)

**Interfaces:**
- Consumes: `fold_web_funnel(ad, *, keyword, technique)` → `('candidate', frow)` with `frow` keys page_id/page_name/landing_url/landing_domain/...; `upsert_funnel_candidate(conn, frow) -> (id, was_new)`; `FUNNEL_INCUMBENT_PAGE_LIKES_MAX` from `agf.scoring.vetoes`.
- Produces: `funnel_signal` auto-passes `keyword_layer == "hunt_page"`; `upsert_hunt_result` accepts optional `"triage"` in its row dict (default `'pending'`); `_persist_page(..., funnel_autopass: bool = False)` persists web rows as `store='web'`, `store_id=<landing_domain>`, `candidate_id=None`, `triage='kept'`, counted in found/new and in `stats["drops"]["funnel_captured"]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_funnel_signal_autopasses_hunt_page():
    ad = make_web_ad(link_url="https://joggo.run/plan", body_text="start running")
    assert funnel_signal(ad, "hunt_page") is True
    # term-lane hunts still gate on real funnel signals
    assert funnel_signal(ad, "hunt") is False


def test_persist_page_captures_web_funnel_rows(hunt_engine_round):
    """A competitor pull whose ads all land on a web funnel (the Joggo case)
    must produce hunt_results rows: store='web', kept, no candidate_id."""
    engine, round_id = hunt_engine_round
    records = [make_web_ad(page_id="9", page_name="Joggo",
                           link_url="https://joggo.run/plan?utm_source=fb",
                           page_like_count=48310)]
    stats = fresh_stats()
    found, new = _persist_page(engine, round_id, "page:Joggo", records, 1,
                               stats, threading.Lock(), funnel_autopass=True)
    assert found == 1
    with begin(engine) as conn:
        row = conn.execute(text(
            "SELECT store, store_id, triage, candidate_id, link_url "
            "FROM frontend.hunt_results WHERE round_id = :r"),
            {"r": round_id}).one()
    assert row.store == "web"
    assert row.store_id == "joggo.run"
    assert row.triage == "kept"
    assert row.candidate_id is None
    with begin(engine) as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM discovery.funnel_candidates "
            "WHERE landing_domain = 'joggo.run'")).scalar()
    assert n == 1
    assert stats["drops"]["funnel_captured"] == 1


def test_persist_page_term_lane_still_gates_funnels(hunt_engine_round):
    """Without autopass, a plain web ad with no funnel signal stays a drop."""
    engine, round_id = hunt_engine_round
    records = [make_web_ad(page_id="9", page_name="Some Blog",
                           link_url="https://someblog.example/post",
                           body_text="read my story", page_like_count=10)]
    stats = fresh_stats()
    found, _ = _persist_page(engine, round_id, "run coach", records, 1,
                             stats, threading.Lock())
    assert found == 0
    assert stats["drops"].get("funnel_captured") is None
```

(`make_web_ad` = an AdRecord factory with a non-store link_url and no leadgen CTA / funnel host / funnel words, so `funnel_signal` is False for layer "hunt"; follow the AdRecord construction pattern already in the test file. Clean up funnel_candidates rows the test creates, mirroring the file's cleanup style.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/test_hunt.py -k "funnel" -v`
Expected: FAIL (`funnel_signal("hunt_page")` False; `_persist_page` has no funnel_autopass kwarg).

- [ ] **Step 3: Implement**

3a. `src/agf/discovery/funnel_classify.py` — funnel_signal first branch becomes:

```python
    if keyword_layer in ("funnel_phrase", "hunt_page"):
        return True
```

(and extend the docstring: hunt competitor pulls pass `hunt_page` — the page identity is known, so any web landing is that advertiser's funnel.)

3b. `src/agf/db/hunts.py` — replace `upsert_hunt_result`:

```python
def upsert_hunt_result(conn, row: dict) -> None:
    """One found page per round; re-finds (other term/page) keep the first row.
    triage defaults to 'pending' (apps await store triage); web-funnel rows
    insert as 'kept' — no app triage applies to them (design 2026-07-15)."""
    row = {"triage": "pending", **row}
    conn.execute(text(
        "INSERT INTO frontend.hunt_results "
        "  (round_id, candidate_id, page_id, page_name, store, store_id, "
        "   link_url, source_term, was_new, page, triage) "
        "VALUES (:round_id, :candidate_id, :page_id, :page_name, :store, "
        "        :store_id, :link_url, :source_term, :was_new, :page, :triage) "
        "ON CONFLICT (round_id, page_id, store, store_id) DO NOTHING"), row)
```

3c. `src/agf/discovery/hunt.py` — imports: add `from agf.db.funnel_candidates import upsert_funnel_candidate`, `from agf.discovery.funnel_classify import fold_web_funnel`, `from agf.scoring.vetoes import FUNNEL_INCUMBENT_PAGE_LIKES_MAX`. Replace `_persist_page` entirely:

```python
def _persist_page(engine, round_id: int, term: str, records, depth: int,
                  stats: dict, lock: threading.Lock,
                  funnel_autopass: bool = False) -> tuple[int, int]:
    """Classify + upsert one SERP page — apps AND web funnels. Web-funnel ads
    become hunt_results rows (store='web', kept, no candidate) so competitor
    pulls surface web2app advertisers like Joggo (design 2026-07-15);
    funnel_autopass skips the funnel-signal gate for competitor pulls, where
    the page identity is known. Returns (found, new) counting both kinds."""
    rows, seen, drops = [], set(), []
    funnel_rows, funnel_keys = [], set()
    for ad in records:
        disposition, row = classify_candidate(ad, keyword=term, technique="hunt")
        if disposition == "candidate":
            key = (row["page_id"], row["store"], row["store_id"])
            if key in seen:
                continue
            seen.add(key)
            row["first_seen_depth"] = depth
            rows.append(row)
            continue
        if disposition == "non_app":
            likes = ad.page_like_count
            if likes is None or likes <= FUNNEL_INCUMBENT_PAGE_LIKES_MAX:
                fdisp, frow = fold_web_funnel(
                    ad, keyword=term,
                    technique="hunt_page" if funnel_autopass else "hunt")
                if fdisp == "candidate":
                    fkey = (frow["page_id"], frow["landing_domain"])
                    if fkey not in funnel_keys:
                        funnel_keys.add(fkey)
                        funnel_rows.append(frow)
                    continue
        drops.append(disposition)
    found = new = 0
    if rows:
        with begin(engine) as conn:
            for row in rows:
                cid, was_new = upsert_sweep_candidate(conn, row)
                upsert_hunt_result(conn, {
                    "round_id": round_id, "candidate_id": cid,
                    "page_id": row["page_id"], "page_name": row["page_name"],
                    "store": row["store"], "store_id": row["store_id"],
                    "link_url": row["link_url"], "source_term": term,
                    "was_new": was_new, "page": depth})
                found += 1
                new += int(was_new)
    if funnel_rows:
        # Deterministic order: concurrent lanes can find the same funnels in
        # opposite order and deadlock (same discipline as sweep._process_page).
        funnel_rows.sort(key=lambda fr: (fr["page_id"], fr["landing_domain"]))
        with begin(engine) as conn:
            for frow in funnel_rows:
                _, was_new = upsert_funnel_candidate(conn, frow)
                upsert_hunt_result(conn, {
                    "round_id": round_id, "candidate_id": None,
                    "page_id": frow["page_id"], "page_name": frow["page_name"],
                    "store": "web", "store_id": frow["landing_domain"],
                    "link_url": frow["landing_url"], "source_term": term,
                    "was_new": was_new, "page": depth, "triage": "kept"})
                found += 1
                new += int(was_new)
    with lock:
        stats["found"] += found
        stats["new"] += new
        if funnel_rows:
            stats["drops"]["funnel_captured"] = (
                stats["drops"].get("funnel_captured", 0) + len(funnel_rows))
        for disposition in drops:
            stats["drops"][disposition] = stats["drops"].get(disposition, 0) + 1
    return found, new
```

3d. In `_pull_competitor` (Task 4), change its `_persist_page(...)` call to pass `funnel_autopass=True`.

3e. Update the module docstring line "Web-funnel (non_app) ads are counted but not persisted here — the nightly sweep owns funnel capture (v1 simplification)." to: "Web-funnel (non_app) ads are captured too: fold_web_funnel rows persist to discovery.funnel_candidates AND as store='web' hunt results (design 2026-07-15); competitor pulls auto-pass the funnel-signal gate."

Downstream invariants (verify, no code expected): `apply_triage_to_results` joins on candidate_id → web rows keep triage='kept'; judge `_kept_rows` LEFT JOINs apps → web rows judged from page_name/ad text (fine); enrich enqueue filters `kept_advertiser_id IS NOT NULL` → web rows skipped; web UI RoundBlock.tsx already labels store='web' and hides the heart when kept_advertiser_id is null.

- [ ] **Step 4: Run tests to verify they pass**

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest tests/discovery/ -v`
Expected: PASS all discovery tests (funnel_classify tests included).

- [ ] **Step 5: Commit**

```bash
git add src/agf/discovery/hunt.py src/agf/discovery/funnel_classify.py src/agf/db/hunts.py tests/discovery/test_hunt.py
git commit -m "feat(hunt): capture web-funnel advertisers as store='web' hunt results"
```

---

### Task 6: Full-suite gates + whole-branch review prep

- [ ] **Step 1: Python full suite, bare, solo** (confirm no other test consumer first: `pgrep -fl 'pytest|go test' || true`)

Run: `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it python -m pytest -q`
Expected: 0 failed. Read the summary line for skips — a skip is not a pass for touched areas.

- [ ] **Step 2: Go full suite, bare, AFTER the Python suite finishes**

Run: `cd backend && AGF_IT_DB_URL=postgres://agf:agf@localhost:55433/agf_it go test ./...`
Expected: ok everywhere, 0 FAIL.

- [ ] **Step 3: Web typecheck (no web code changed; cheap regression guard)**

Run: `cd web && npx tsc --noEmit`
Expected: 0 errors.

---

### Live verification (controller-run, after review)

1. Deploy: roll the worker/hunt VM image (hunt daemon runs `run_hunt_round`) and the API image (Go changes). Migrations: none new; 158 already applied.
2. In /hunt chat, ask Peon for a women's-running proposal → confirm no term contains an "app" token and up to 10 competitors are accepted (enter the 12 from hunt 5).
3. Start the round. Success criteria:
   - progress events show "Pulling ads from '<page>'" with plausibly-correct page names for Femmi / Joggo / Wild AI (not Femmira Fashion / a person),
   - hunt_results gains store='web' rows for web2app competitors (Joggo → joggo.run),
   - `hunt_rounds.progress.terms` entries carry kept/relevant/off_target after the round finishes,
   - next Peon turn's round summary line includes "(N relevant, M off-target)".
4. Optional probe while verifying: try `countries=["ALL"]` against search_listing once via a scratchpad script; if Meta accepts it, a follow-up commit can swap HUNT_COMPETITOR_COUNTRIES for ["ALL"].
