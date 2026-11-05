# Agent-controlled Meta ad research

## Jev usage correction: current implementation

The v2 judge asked Jev to infer a target from a brief and several source ads, then
answer overlapping questions about need, audience and overall relevance. Requiring
all of those probabilities to clear separate thresholds suppressed useful results.
The preceding prompt change had reduced a false positive without measuring recall.

Working search implementations use a concrete query/candidate pair, narrow questions
and measured rankings. Sources inspected on November 5:

- [gbesse/jev-rerank-server](https://github.com/gbesse/jev-rerank-server#how-it-decides)
  sends one query and passage per request and sorts the unchanged Noul output. Its
  checked-in 25-query SciFact study reports nDCG@10 improving from 0.616 to 0.718;
  it explicitly warns that adding unrelated candidates to the state can hurt quality.
- [hotchpotch/jev-reranker](https://github.com/hotchpotch/jev-reranker/blob/main/src/jev_reranker/instructions.py)
  uses explicit query/document references and separates ranking from relevance
  filtering. Its evaluation reports how many labeled positives filtering removes.
- TypeSafe's worked [RAG passage classifier](https://docs.typesafe.ai/cookbooks/classifying_rag_passages)
  asks independent questions on one query/passage pair and combines them in code.
  Its [skill suggestion study](https://docs.typesafe.ai/cookbooks/skill_suggestion)
  checks shortlisted candidates with fuller evidence and permits no recommendation.
- [Steve Klosterman's independent experiments](https://www.steveklosterman.com/diy-jev/)
  show that wording and question type can change decisions. A model's probability
  is not a substitute for evaluating the application's policy on labeled examples.

Rubric `ad-peer-v3` now requires the calling agent to supply `target.need` and
`target.audience` after reviewing source ads or a user brief. Jev receives only
that target and one candidate's copy. Three independent Noul questions check the
promised outcome, an explicit audience conflict, and whether the offer is clear.
There is no fourth overall-relevance question. Short meaningful copy reaches the
model; the earlier eight-word minimum has been removed.

Code applies the review bands and returns all three raw probabilities. The optional
`ranking_score = offers_need * (1 - audience_conflict)` prioritizes review; it is
an ordering heuristic, not a calibrated probability. Nothing is filtered or stored
as a relevance classification. Different source campaigns need separate targets.
The [embedded guide](../backend/mcpserver/ad_research_guide.md) documents the new input
and migration from the earlier `brief`/`reference_ads` judge input. The finder keeps
its existing input and is unchanged.

### Real-ad evaluation

The checked-in quality fixture contains 25 observed ad/target pairs across fitness
and consumer relaxation: 12 development cases and 13 validation cases. Labels were
written from actual copy before obtaining each split's predictions. Validation used
the unchanged target descriptions, questions and decision bands. These are agent
labels on a small sample, not an independently adjudicated accuracy benchmark.

Replaying the previous v2 questions and routing on the same candidates and target
wording, with its original source references, produced 15/25 exact labels and 2/11
positive matches. The final Go v3 run produced 22/25 exact labels and 8/11 positive
matches. All 11 labeled mismatches were rejected, and all three deliberately sparse
or ambiguous cases stayed uncertain. On validation alone, v3 returned 12/13 exact
labels and 6/7 positive matches, versus v2's 7/13 and 2/7. The complete v3 quality run
took 1.76 seconds across four sequential groups with up to four calls in parallel.

The two Go runs gave 8–9 positive matches out of 11. Running Week crossed the match
threshold between runs; an unrelated style offer crossed the clear-offer threshold.
Both runs gave 22/25 exact labels and no false positive matches. This variability is
why scores and uncertainty remain visible. The short EMDR/Spotify ad and Tony Horton's
broad fitness offer still need review. No prompt changes were made after validation.

`TestAdJudgeQualityLive` counts abstentions as misses, checks exact-label accuracy,
rejects confident contradictions and requires positive recall. Its first invocation
exposed an overly strict assertion that every negative must receive a hard rejection;
that assertion now allows abstention while still counting it as an accuracy miss.
The accuracy floors and labels were retained. Returning uncertain for everything
cannot pass. Provider failures also fail instead of counting as model uncertainty.

Run it from `backend` with `TYPESAFE_API_KEY` supplied securely:

```sh
AGF_JEV_QUALITY_LIVE=1 go test ./helpers -run '^TestAdJudgeQualityLive$' -count=1 -v
```

HTTP MCP tests passed for the required target, account ownership, missing credentials
and the revised schema. A live HTTP MCP run judged seven stored real ads in 0.50 seconds
and called the unchanged finder successfully. Helper/MCP/repository race suites,
Go build and scoped vet passed. Deployment and an installed agent-client session
remain outside this local verification.

A fresh full Femrun workflow also passed: live advertiser lookup, source ads,
Gemini search terms, exact/broad Meta searches and Jev judging through HTTP MCP.
It took 60.36 seconds, including 0.73 seconds for twelve judgements; the cached
source-page replay took 41.71 ms. Evlo Fitness and Gentle Gains matched, eight
candidates were mismatches, and two remained uncertain. The previous BetterMe
weightlifter false positive was rejected. This validates the revised input through
the real queue/worker/provider path; it does not establish that the search has found
every relevant advertiser. The change is local and has not been deployed to Peon.

## Earlier continuous workflow follow-up (v2)

Completed the HTTP MCP workflow from brand lookup through fresh source ads,
finder-generated queries, candidate retrieval and Jev judging in two niches.
The first fitness run exposed an overconfident match between complete beginners
and established lifters seeking endurance. Rubric `ad-peer-v2` now compares audience
starting points and respects the caller's brief. The observed ad is preserved in
a live regression fixture; the complete workflow and owning checks were repeated.

Final runs used real Meta, Gemini and TypeSafe calls, local PostgreSQL, an
authenticated HTTP MCP SDK client and the actual queue/Python subprocess path:

- Femrun: 75.22 seconds through judging, including a broad retry when exact search
  returned only one distinct candidate. Twelve distinct candidates were judged:
  six mismatches and six uncertain. Gemini took 2.09 seconds and Jev 0.67 seconds;
  source-page cache replay took 33.19 ms.
- Calm: 36.83 seconds through judging. Ten distinct candidates were judged:
  three mismatches and seven uncertain. Gemini took 1.99 seconds and Jev 0.73
  seconds; source-page cache replay took 40.01 ms.

Neither run produced a confident Jev match. Manual copy inspection found promising
fitness leads: Evlo Fitness ad `1798223641621068` emphasizes consistency without an
all-or-nothing approach; Gentle Gains ad `1135408645816454` offers short, guided,
beginner home workouts for core/body shaping. Both remained uncertain to Jev.
These are ad-level research leads, not validated brand-wide recommendations.
The Calm search also returned parenting, pet and medical advertorials, showing why
raw keyword results still require inspection of the actual audience and offer.

The original false positive, BetterMe Men ad `1372017226001179`, now returns
uncertain both in the fresh full workflow and the saved regression. Its copy
targets existing lifters adding endurance, which does not establish the same
starting point as complete fitness beginners. The seven-ad regression took
0.47 seconds for Jev, rejected unrelated offers and left sparse or partial overlaps
uncertain. A relevant Running Week example also became uncertain; the change
reduces this false positive but does not establish high recall or broad accuracy.

`TestAdResearchFullFlowLive` verifies successful sources, fresh evidence flowing
between tools, complete provider responses and cache identity. It deliberately
does not require a positive verdict. It searches finder-generated phrases; it
does not verify the suggested brands by fetching their pages. The Python worker
has both model keys cleared, so model calls remain explicit API tool calls.

To reproduce, configure `AGF_IT_DB_URL` for a disposable migrated database and
provide `AGF_PROXIES`, `GEMINI_API_KEY` and `TYPESAFE_API_KEY` through the environment.
From `backend`, run the live gates separately:

```sh
AGF_AD_RESEARCH_FLOW=1 go test ./internal/integration -run '^TestAdResearchFullFlowLive$' -count=1 -v -timeout 10m
AGF_AD_RESEARCH_LIVE=1 go test ./internal/integration -run '^TestAdResearchModelsLive$' -count=1 -v
```

The model gate defaults to the checked-in observed-ad fitness fixture.
`AGF_FLOW_BRAND`, `AGF_FLOW_BRIEF`, `AGF_FLOW_NEED`, `AGF_FLOW_AUDIENCE` and
`AGF_FLOW_REPORT` customize the continuous workflow; `AGF_AD_RESEARCH_FIXTURE` selects another observed-ad model fixture.
Final helper/MCP/repository race suites, Go build and scoped vet passed.
These checks cover local integration; Peon deployment and an installed Claude
Code/Codex client session remain unverified. Jev remains advisory, and these small
samples do not establish search quality across brands.

## Optional assistance tools

The assistance tools expose model calls explicitly, while the calling agent
continues to control the workflow. `find_similar_brands` takes a brief and/or
selected source ad snapshots and returns unverified advertiser leads and search
phrases. `judge_ads` uses Jev to assess caller-selected candidate snapshots against
the caller-defined customer need and audience. Neither tool scrapes, changes stored ads or hides results.

Implemented bounded provider helpers, owner-scoped snapshot loading and two MCP
handlers, with usage and error handling in the embedded guide. The brand finder
reuses the existing Gemini client; the judge uses TypeSafe's Jev Noul decisions.
No database migration or change to direct scraping is required. Existing Hunt's
app-category judge is not used to label these ad-level results.

Live HTTP MCP checks used twelve selected, previously observed fitness and
relaxation ads. In the final runs, Jev assessed six ads in 0.44 seconds and six in
1.57 seconds; Gemini returned five unverified brand leads in about 2.2 seconds.
The checks include ownership, explicit provider invocation and no queued scrape
side effects. Unit and integration checks cover missing credentials, malformed
provider data, sparse copy, evidence truncation and incompatible audiences.

The Go helper, MCP and repository race suites, scoped HTTP integration test,
build/vet, Compose configuration check and secret scan passed for this increment.

These checks establish the integration, not broad classification accuracy. A
manually relevant short EMDR/Spotify ad remained uncertain; WalkFit and a
calisthenics ad also required agent review. The advisory live gate permits positive
abstentions, records them and rejects confident contradictions. Leads remain
unverified until the calling agent inspects their actual ads. No stored ads are
changed or hidden by either helper.

Both keys belong on the API server: GEMINI_API_KEY for find_similar_brands and
TYPESAFE_API_KEY for judge_ads. The local API Compose service forwards optional
keys; GKE already imports its API environment through agf-secrets. Configure the
keys there when deploying. Raw scraping remains usable without either provider.

## Delivered contract

The calling Claude Code or Codex owns research decisions: identify the source,
read its ads, choose references, derive audience/problem/outcome/offer/angle,
retrieve candidates and judge relevance. Field Desk supplies raw evidence.
No Gemini or Jev call runs in the new scraping path.

The embedded [agent guide](../backend/mcpserver/ad_research_guide.md) is returned
by `ad_research_guide`. MCP initialization instructions tell agents to read it.
The tools are:

- `find_advertisers`: up to three names, all candidate page identities returned.
- `get_advertiser_ads`: one active-ad page for a selected numeric page ID.
- `search_meta_ads`: up to three concurrent exact or broad keyword searches.
- `ad_scrape_status`: incremental raw ads, per-source errors and stored-result pagination.
- `next_ad_page`: an owner-scoped, idempotent provider continuation.
- `read_ad_copy`: full stored copy in bounded chunks, without another Meta request.

The guide covers brands without ads, explicit reference brands, choosing references
from verified active ads, distinct campaigns, sparse media evidence, aliases,
primary advertised needs and grouping matches by advertiser. No brand names or
rankings are hardcoded. Model-generated category labels are not returned.

## Implementation

Migration 166 adds isolated scrape requests and ad snapshots. The API commits the
request and queue job atomically. Existing Hunt continues independently. A second
lane in the Hunt worker processes raw scrape jobs in bounded subprocesses, with
90-second inner budgets, a 120-second outer timeout and 180-second leases.
Interrupted jobs preserve their evidence and become explicit errors.

Requests and results are scoped to the authenticated account. Existing admin
permissions remain in force; enabling scraping for other roles is not part of
this change. At most two scrapes per account can be active, and each scrape handles
at most three sources. Each source fetches one Meta page; the agent controls depth.

Successful identical requests are cached for six hours within the same account.
Refresh bypasses cache. Idempotency keys protect retries and reject changed payloads.
Provider cursors are stored privately and inherited through next_ad_page; the
caller cannot substitute another owner's cursor. Stored-result offsets and
provider pagination are distinct. Ad IDs deduplicate snapshots, while source
reports retain the IDs observed for every query.

No store enrichment or discovery-corpus mutation occurs. Full copy, destination,
media URLs, active flag, dates, page identity, creative hash and observation time
are retained. Empty results and retrieval failures have different source statuses.
Long copy is abbreviated in lists and can be read in full through read_ad_copy.

## Verification, November 5, 2026

The live gate uses the real HTTP MCP SDK, account authentication, PostgreSQL,
queue claim, Python subprocess, Meta retrieval and MCP status response. Both
GEMINI_API_KEY and TYPESAFE_API_KEY are explicitly cleared in the worker.

- Femrun page: 10 real ads, 25.59 seconds of worker time; cached retry returned
  the same completed scrape. Source lookup is bypassed when a page ID is supplied.
- Three-query batch (walk to run, hanging belly, bilateral stimulation): first
  ads in 4.34 seconds, 29 unique ads in 25.27 seconds, cache replay in 10.43 ms.
  Partial evidence was visible through MCP before all three sources completed.
- Calm page: 10 ads; next_ad_page fetched another 10 ads through the real provider.
  Cached first-page response took 18.57 ms.

- Advertiser lookup for Femrun, WalkFit and Calm completed through MCP with
  page candidates preserved; cached replay took 12.07 ms.

These are local measurements, not a production SLA. Meta/proxy retries can dominate
cold latency. The results include both useful and off-topic ads by design; the
calling agent evaluates them using the guide and actual creative evidence.

Python checks cover sparse ads, identity collisions, country/cursor propagation,
empty/failure distinction, persistence before slow sources finish, crash handling,
lease recovery and creative hashes. Go tests cover authentication, ownership,
cache, idempotency, capacity, raw-copy pagination and MCP continuation behavior.

Final scoped checks passed: 28 Python tests (scraping, Hunt daemon and queue),
Go MCP/repository race suites, the HTTP MCP integration test, Go build/vet,
Python lint and a secret scan of the 17 owned files. The four explicit live
provider runs passed; opt-in tests skipped during ordinary suites are not counted
as live verification.

The broad Go race suite exposed an existing time-sensitive TestScaling assertion:
it expects March 30, 2026, but the unchanged scaling query caps the chart at 26
weeks, starting May 11 on this date. The assertion also fails in isolation.
This is outside the new scrape tables and code path; it has not been changed.
One earlier concurrent invocation collided with the integration suite's shared
seed fixtures; subsequent database test invocations run sequentially.

## Deployment and remaining boundaries

Apply migration 166, rebuild/restart the Go backend and Hunt worker, and refresh
the MCP client's tool list. The worker needs its existing database/proxy settings;
no additional model key is required. This increment is verified locally and is
not deployed to Peon. No new UI or account-access policy is included.

Earlier Jev/Gemini experiments showed why hiding relevance decisions was brittle:
category-like queries missed useful peers, identical keywords attracted unrelated
offers, and short excerpts sometimes hid the real offer in advertorials. Those
experimental model modules are excluded from this increment. The user's revised
architecture places those decisions with the calling agent and makes the full
evidence available through documented scraping primitives.
