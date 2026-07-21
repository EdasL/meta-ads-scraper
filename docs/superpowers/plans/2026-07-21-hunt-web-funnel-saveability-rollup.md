# Hunt Web-Funnel Saveability + Per-Page Rollup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make hunt web-funnel cards saveable (icon/GemScore/heart/save) by resolving their advertiser via `page_id`, and collapse a round's duplicate cards into one card per page.

**Architecture:** Backend — `ListHuntResults` resolves an effective advertiser id `COALESCE(hr.kept_advertiser_id, page→advertiser)` via a `LEFT JOIN LATERAL` scalar subquery and drives all existing joins off it. Frontend — a pure `rollupByPage` helper in `lib/hunt.ts` groups a round's rows by `page_id`; `RoundBlock` renders one `ResultCard` per group with a union of store badges and a fold count.

**Tech Stack:** Go + GORM (raw SQL) backend; Next.js/React + TypeScript frontend; Go tests via `go test` (gated on `AGF_IT_DB_URL`); frontend pure-helper tests via `node --test`.

## Global Constraints

- Product copy is plain, clear, professional — no marketing fluff, grounded in real data (CLAUDE.md). Badges/labels restate real fields only.
- No schema change; no migration. `apply_triage_to_results` is NOT touched.
- The scalar subquery (not a JOIN) resolves page→advertiser so a hunt row never fans out.
- Resolve whenever an advertiser exists for the page; triage/relevance stays a separate axis.
- Rollup is per `page_id`, within each round, within each on-/off-target bucket.
- Product nouns intact: Field Desk, GemScore, cohort.

---

### Task 1: Backend — resolve advertiser by page_id in ListHuntResults

**Files:**
- Modify: `backend/repositories/hunt_repo.go` (the `ListHuntResults` raw query)
- Test: `backend/repositories/hunt_repo_test.go` (add one test; package `repositories_test`)

**Interfaces:**
- Consumes: existing `models.HuntResultRow` (unchanged; `KeptAdvertiserID *int` scans the returned `kept_advertiser_id`).
- Produces: `ListHuntResults` returns rows whose `kept_advertiser_id` is the effective id — the row's own kept id when set, else the advertiser whose `page_id` matches, else null.

- [ ] **Step 1: Write the failing test**

Add to `backend/repositories/hunt_repo_test.go` (uses the package's `ratingsDB`, `must`, `cleanHunts`, `huntTestEmail`; seeds real tables — `advertiser_scores` and `saved_gems` are tables; `emerging_scaling_apps` is a matview so icon isn't asserted, the resolution + score + saved prove the effective-id join):

```go
func TestListHuntResultsResolvesWebRowByPageID(t *testing.T) {
	if ratingsDB == nil {
		t.Skip("AGF_IT_DB_URL not set")
	}
	g := ratingsDB
	ctx := context.Background()
	cleanHunts(g)
	t.Cleanup(func() { cleanHunts(g) })

	const pageID = "HUNTB_PAGE_1"
	// Clean any stray fixtures from a prior run.
	must(g.Exec(`DELETE FROM frontend.saved_gems WHERE advertiser_id IN (SELECT id FROM public.advertisers WHERE page_id = ?)`, pageID).Error)
	must(g.Exec(`DELETE FROM adtech.advertiser_scores WHERE advertiser_id IN (SELECT id FROM public.advertisers WHERE page_id = ?)`, pageID).Error)
	must(g.Exec(`DELETE FROM public.advertisers WHERE page_id = ?`, pageID).Error)
	t.Cleanup(func() {
		must(g.Exec(`DELETE FROM frontend.saved_gems WHERE advertiser_id IN (SELECT id FROM public.advertisers WHERE page_id = ?)`, pageID).Error)
		must(g.Exec(`DELETE FROM adtech.advertiser_scores WHERE advertiser_id IN (SELECT id FROM public.advertisers WHERE page_id = ?)`, pageID).Error)
		must(g.Exec(`DELETE FROM public.advertisers WHERE page_id = ?`, pageID).Error)
	})

	// A confirmed web funnel: an advertiser exists on this page_id with a score.
	var advID int
	must(g.Raw(`INSERT INTO public.advertisers (page_id, page_name) VALUES (?, 'Web Funnel Co') RETURNING id`, pageID).Scan(&advID).Error)
	must(g.Exec(`INSERT INTO adtech.advertiser_scores (advertiser_id, gem_score) VALUES (?, 73)`, advID).Error)
	must(g.Exec(`INSERT INTO frontend.saved_gems (advertiser_id, user_email) VALUES (?, ?)`, advID, huntTestEmail).Error)

	// A hunt + round + a WEB hunt_result with NO kept_advertiser_id, same page_id.
	huntID, err := repositories.CreateHunt(ctx, g, huntTestEmail, "web funnels")
	require.NoError(t, err)
	terms, _ := json.Marshal([]string{"web funnel"})
	comps, _ := json.Marshal([]string{})
	roundID, err := repositories.InsertHuntRound(ctx, g, huntID, nil, terms, comps, nil, nil)
	require.NoError(t, err)
	must(g.Exec(`INSERT INTO frontend.hunt_results
		(round_id, page_id, page_name, store, store_id, link_url, source_term, was_new, triage)
		VALUES (?, ?, 'Web Funnel Co', 'web', 'webfunnelco.com', 'https://webfunnelco.com/', 'web funnel', true, 'kept')`,
		roundID, pageID).Error)

	rows, err := repositories.ListHuntResults(ctx, g, huntID, huntTestEmail)
	require.NoError(t, err)
	require.Len(t, rows, 1, "one web row, no fan-out from the page→advertiser resolution")
	require.NotNil(t, rows[0].KeptAdvertiserID, "web row resolves its advertiser by page_id")
	require.Equal(t, advID, *rows[0].KeptAdvertiserID)
	require.NotNil(t, rows[0].GemScore)
	require.EqualValues(t, 73, *rows[0].GemScore)
	require.True(t, rows[0].Saved, "resolved advertiser's saved flag flows through")
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && AGF_IT_DB_URL="postgres://agf:agf@localhost:55433/agf_it?sslmode=disable" go test ./repositories/ -run TestListHuntResultsResolvesWebRowByPageID -v`
Expected: FAIL — `KeptAdvertiserID` is nil (current query keys joins on `hr.kept_advertiser_id`, which is null for the web row).

- [ ] **Step 3: Modify the ListHuntResults query**

In `backend/repositories/hunt_repo.go`, replace the raw query body of `ListHuntResults` with (adds the `LEFT JOIN LATERAL eff`, returns `eff.adv_id AS kept_advertiser_id`, and points every advertiser join at `eff.adv_id`):

```go
		return tx.Raw(
			`SELECT hr.id, hr.round_id, hr.page_id, hr.page_name, hr.store, hr.store_id,
			        hr.link_url, hr.source_term, hr.was_new, hr.triage,
			        eff.adv_id AS kept_advertiser_id,
			        hr.relevance, hr.relevance_note, hr.page,
			        s.gem_score,
			        esa.app_name, esa.icon_url, esa.tagline, esa.cohort,
			        (sg.advertiser_id IS NOT NULL) AS saved,
			        ar.rating
			 FROM frontend.hunt_results hr
			 JOIN frontend.hunt_rounds r ON r.id = hr.round_id
			 LEFT JOIN LATERAL (
			     SELECT COALESCE(
			                hr.kept_advertiser_id,
			                (SELECT a.id FROM public.advertisers a
			                  WHERE a.page_id = hr.page_id LIMIT 1)
			            ) AS adv_id
			 ) eff ON true
			 LEFT JOIN adtech.advertiser_scores s ON s.advertiser_id = eff.adv_id
			 LEFT JOIN frontend.emerging_scaling_apps esa ON esa.advertiser_id = eff.adv_id
			 LEFT JOIN frontend.saved_gems sg
			        ON sg.advertiser_id = eff.adv_id AND lower(sg.user_email) = lower(?)
			 LEFT JOIN frontend.app_ratings ar
			        ON ar.advertiser_id = eff.adv_id AND lower(ar.user_email) = lower(?)
			 WHERE r.hunt_id = ?
			 ORDER BY hr.id`,
			email, email, huntID,
		).Scan(&rows).Error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && AGF_IT_DB_URL="postgres://agf:agf@localhost:55433/agf_it?sslmode=disable" go test ./repositories/ -run TestListHuntResultsResolvesWebRowByPageID -v`
Expected: PASS.

- [ ] **Step 5: Run the existing hunt-repo tests to confirm no regression**

Run: `cd backend && AGF_IT_DB_URL="postgres://agf:agf@localhost:55433/agf_it?sslmode=disable" go test ./repositories/ -run TestHunt -v && go vet ./repositories/`
Expected: PASS; vet clean.

- [ ] **Step 6: Commit**

```bash
git add backend/repositories/hunt_repo.go backend/repositories/hunt_repo_test.go
git commit -m "feat(hunt): resolve web-row advertiser by page_id in ListHuntResults"
```

---

### Task 2: Frontend — rollupByPage pure helper

**Files:**
- Modify: `web/src/lib/hunt.ts` (add `RolledResult` type + `rollupByPage`)
- Test: `web/src/lib/hunt.test.ts` (add cases; run with `node --test`)

**Interfaces:**
- Consumes: `HuntResult` from `./api` (fields incl. `page_id`, `store`, `link_url`, `kept_advertiser_id`, `triage`, `app_name`, `page_name`, `store_id`).
- Produces:
  ```ts
  export interface RolledResult {
    page_id: string;
    rep: HuntResult;                 // resolved row if any, else first
    stores: { store: string; link_url: string | null }[];  // distinct, first-seen order
    fold_count: number;              // rows folded (>=1)
    triage: "pending" | "kept" | "vetoed";
  }
  export function rollupByPage(results: HuntResult[]): RolledResult[];
  ```

- [ ] **Step 1: Write the failing test**

Add to `web/src/lib/hunt.test.ts` (match its `node:test` style; build minimal `HuntResult` fixtures via a helper). Append:

```ts
import { rollupByPage } from "./hunt.ts";

function hr(over: Partial<import("./api").HuntResult>): import("./api").HuntResult {
  return {
    id: 1, round_id: 1, page_id: "P", page_name: "Page", store: "web",
    store_id: "x", link_url: "https://x/", source_term: "t", was_new: false,
    triage: "kept", kept_advertiser_id: null, gem_score: null, page: null,
    app_name: null, icon_url: null, tagline: null, cohort: null, saved: false,
    rating: null, relevance: null, relevance_note: null, ...over,
  };
}

test("rollupByPage collapses a page's rows and unions its stores", () => {
  const rows = [
    hr({ id: 1, page_id: "A", store: "ios", link_url: "https://ios/a", kept_advertiser_id: 42, gem_score: 41, app_name: "Coopah" }),
    hr({ id: 2, page_id: "A", store: "android", link_url: "https://and/a" }),
    hr({ id: 3, page_id: "A", store: "web", link_url: "https://web/a" }),
    hr({ id: 4, page_id: "B", store: "web", link_url: "https://web/b" }),
  ];
  const out = rollupByPage(rows);
  assert.equal(out.length, 2, "two pages -> two cards");
  const a = out[0];
  assert.equal(a.page_id, "A");
  assert.equal(a.fold_count, 3);
  assert.deepEqual(a.stores.map((s) => s.store), ["ios", "android", "web"]);
  assert.equal(a.rep.id, 1, "representative is the resolved row");
  assert.equal(a.rep.kept_advertiser_id, 42);
  assert.equal(out[1].fold_count, 1);
});

test("rollupByPage picks first row when none resolved, and keeps kept triage", () => {
  const rows = [
    hr({ id: 1, page_id: "A", store: "web", triage: "vetoed", kept_advertiser_id: null }),
    hr({ id: 2, page_id: "A", store: "ios", triage: "kept", kept_advertiser_id: null }),
  ];
  const out = rollupByPage(rows);
  assert.equal(out.length, 1);
  assert.equal(out[0].rep.id, 1, "no resolved row -> first row is representative");
  assert.equal(out[0].triage, "kept", "any kept -> group triage is kept");
});
```

(`import { test } from "node:test"` and `assert` are already imported at the top of `hunt.test.ts`; do not re-import.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && node --test src/lib/hunt.test.ts`
Expected: FAIL — `rollupByPage` is not exported.

- [ ] **Step 3: Implement rollupByPage in hunt.ts**

Add to `web/src/lib/hunt.ts` (place near `splitByRelevance`; the `RolledResult` type + function):

```ts
export interface RolledResult {
  page_id: string;
  rep: HuntResult;
  stores: { store: string; link_url: string | null }[];
  fold_count: number;
  triage: "pending" | "kept" | "vetoed";
}

/** Collapse a round's rows to one entry per page_id, preserving first-seen order.
 *  rep = the group's resolved row (kept_advertiser_id set) if any, else the first.
 *  stores = distinct store values (first-seen order) with each store's link.
 *  triage = "kept" if any row in the group is kept, else rep's triage. */
export function rollupByPage(results: HuntResult[]): RolledResult[] {
  const order: string[] = [];
  const groups = new Map<string, HuntResult[]>();
  for (const r of results) {
    const g = groups.get(r.page_id);
    if (g) g.push(r);
    else {
      groups.set(r.page_id, [r]);
      order.push(r.page_id);
    }
  }
  return order.map((page_id) => {
    const rows = groups.get(page_id)!;
    const rep = rows.find((r) => r.kept_advertiser_id != null) ?? rows[0];
    const seen = new Set<string>();
    const stores: { store: string; link_url: string | null }[] = [];
    for (const r of rows) {
      if (seen.has(r.store)) continue;
      seen.add(r.store);
      stores.push({ store: r.store, link_url: r.link_url });
    }
    const triage = rows.some((r) => r.triage === "kept") ? "kept" : rep.triage;
    return { page_id, rep, stores, fold_count: rows.length, triage };
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && node --test src/lib/hunt.test.ts`
Expected: PASS (all hunt.ts helper tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/hunt.ts web/src/lib/hunt.test.ts
git commit -m "feat(hunt): rollupByPage helper — one card per page per round"
```

---

### Task 3: Frontend — render one card per page in RoundBlock

**Files:**
- Modify: `web/src/components/hunt/RoundBlock.tsx` (parent map → `rollupByPage`; `ResultCard` renders a `RolledResult`)

**Interfaces:**
- Consumes: `rollupByPage`, `RolledResult` from `../../lib/hunt` (Task 2); `HuntResult` unchanged.
- Produces: UI only. No exported API change.

**Note on verification:** the codebase has no React component test runner (only `node --test` pure-helper tests), so this task is verified by `tsc`/lint + a live visual check, not a unit test. The collapsing logic itself is already covered by Task 2.

- [ ] **Step 1: Route each relevance bucket through rollupByPage**

In `RoundBlock.tsx`, find where results are rendered per bucket (the `splitByRelevance(...)` result mapped to `<ResultCard result={r} .../>`). Replace the `.map` over `HuntResult[]` with a map over `rollupByPage(bucket)`, passing the rolled entry:

```tsx
// import at top:
import { splitByRelevance, rollupByPage, type RolledResult } from "@/lib/hunt";

// where a bucket (onTarget / offTarget: HuntResult[]) is rendered:
{rollupByPage(bucket).map((g) => (
  <ResultCard key={g.page_id} group={g} userEmail={userEmail} />
))}
```

- [ ] **Step 2: Change ResultCard to take a RolledResult and render badges + fold count**

Replace `ResultCard`'s signature and the store-badge/heart region so it reads from `g.rep` and renders one `StoreBadge` per `g.stores`, plus a `·N ads` chip when folded. Keep the existing `scored`/heart gates (now driven by `g.rep`):

```tsx
function ResultCard({ group: g, userEmail }: { group: RolledResult; userEmail: string | null }) {
  const r = g.rep;
  const scored = r.gem_score != null && r.kept_advertiser_id != null;
  const name = r.app_name ?? r.page_name ?? r.store_id;
  // ...existing card wrapper + icon block unchanged, using `r` for icon_url...
  //   badges row:
  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2, flexWrap: "wrap" }}>
    {g.stores.map((s) => (
      <StoreBadge key={s.store} store={s.store} href={s.link_url} />
    ))}
    {scored && <ScoreBadge score={r.gem_score!} variant="card" />}
    {g.fold_count > 1 && (
      <span className="t-xs nums" style={{ color: "var(--muted)" }} title="Ads folded into this page">
        ·{g.fold_count} ads
      </span>
    )}
  </div>
  //   heart (unchanged gate, driven by rep):
  {r.kept_advertiser_id != null && userEmail && <HeartButton result={r} userEmail={userEmail} />}
  //   footer: use r.page_id for the Ads link, r.source_term, r.page, r.was_new, and g.triage for <TriageState>
}
```

Drive `TriageState` off `g.triage`; the footer `Ads` link, `source_term`, `p{page}`, and `New` badge read from `r`. Leave `HeartButton`, `StoreBadge`, `ScoreBadge`, `resultToRow`, and `TriageState` implementations unchanged.

- [ ] **Step 3: Typecheck + lint**

Run: `cd web && npx tsc --noEmit && npm run lint`
Expected: no type errors; lint clean.

- [ ] **Step 4: Live visual verification**

Start the app (or the hunt view) signed in, open a hunt round that had duplicate pages (e.g. a `runna`/`Coopah` round). Confirm: one card per page; multi-store pages show multiple badges (App Store · Play Store · Web); folded pages show `·N ads`; a web page whose funnel resolved shows an icon + GemScore + a working heart; saving persists. Use the `/verify` skill or drive the page with Playwright against the running app.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/hunt/RoundBlock.tsx
git commit -m "feat(hunt): render one card per page with store badges + fold count"
```

---

## Self-Review

**Spec coverage:**
- (b) page_id resolution → Task 1 (query + Go test). ✓
- (c) per-page rollup helper → Task 2; render → Task 3. ✓
- Scalar-subquery no-fan-out → Task 1 test asserts `Len == 1`. ✓
- Heart/save on resolved web card → Task 1 (saved flag) + Task 3 (heart gate on `r.kept_advertiser_id`). ✓
- Store badges union + fold count → Task 2 test + Task 3 render. ✓
- Keep on-/off-target split, roll up within → Task 3 Step 1 (rollup applied per bucket). ✓
- No schema change / `apply_triage_to_results` untouched → nothing in the plan edits them. ✓

**Placeholder scan:** none — every code step is complete; Task 3 Step 4 is an explicit manual-verification step (justified: no component test runner).

**Type consistency:** `RolledResult` fields (`page_id`, `rep`, `stores[].store/link_url`, `fold_count`, `triage`) defined in Task 2 and consumed identically in Task 3. `ResultCard` prop renamed `result` → `group: RolledResult` consistently in Task 3 Steps 1–2. `KeptAdvertiserID *int` used unchanged in Task 1.

**Scope:** single coherent feature, three tasks, backend + frontend — one plan.
