# Agent-controlled Meta ad research

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
