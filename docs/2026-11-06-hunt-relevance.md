# Hunt relevance and review

The Quizlock round returned 825 rows. Store triage, advertising relevance and
unjudged results are mixed together; competitor searches bypass relevance, and
the judge requires matching product mechanics instead of the advertised need.

1. Keep a bounded sample of the actual ads found. Judge every advertiser once,
   including competitors and apps rejected by store triage. Compare customer
   outcome and audience; leave insufficient evidence uncertain.
2. Group before filtering. Show a bounded set of relevant results with reasons and ad copy;
   keep uncertain and off-target results searchable without flooding the page.
3. Verify persistence, judgment and grouping regressions, test the real model on
   Quizlock evidence, and inspect the rendered results before release.

The existing Hunt orchestrator remains in use. This change does not represent a
full migration of the website to the separate agent-controlled MCP workflow.

The full production re-assessment exposed a gap in the four-advertiser check:
BePresent was rejected in one round for lacking quizzes, while generic reward and
content apps were accepted. The follow-up derives one customer outcome and audience
from the original brief, excludes the reference mechanism, and independently checks
the advertised offer, outcome and primary audience. It uses Gemini 3.5 Flash, the
model already used by Hunt's conversation agent. Supported advertisers are packed
into full batches. Replacement judgments are checked before any existing labels
are updated; unavailable evidence remains uncertain.

## Verification

Implemented: each page retains up to three distinct ad-copy samples; judgments
use the original user request and give one consistent verdict across store/web
rows. Missing evidence is uncertain. Competitor lookups and store-vetoed results
are assessed too. Popular pages are eligible as ad references. The UI groups
before filtering, starts with 12 relevant advertisers, searches all groups, and
shows reasons, captured copy and direct ad links. GemScore/store triage no longer
appear as advertising-relevance labels.

- 71 Python tests passed against local Postgres, including migration 167,
  persistence/deduplication, malformed model responses, missing evidence,
  competitor pulls, store-vetoed results, and original-brief selection.
- 33 Hunt UI helper tests passed; scoped ESLint and the Next production build
  passed. Go helper/controller/MCP tests, Hunt repository tests and Hunt HTTP
  integration tests passed; the database-backed checks used local Postgres.
  Scoped Go vet and `git diff --check` passed.
- Production MCP scrapes 6–9 returned 22 ads from one sec, BePresent, Untappd and
  Mathilde Fashion Focus. Using those real ads and the original Quizlock request,
  Gemini kept the first two and rejected the latter two in 1.50 seconds. A second
  run through local persistence → live judge → persisted results took 1.42 seconds
  and gave consistent verdicts to all seven store/web rows, including one sec's
  vetoed store row. This is a focused four-advertiser regression check, not a
  general accuracy estimate.
- Rendered the actual RoundBlock with the captured Quizlock result set. Verified
  the relevant-results view, 12-result review page, cross-group search and expandable ad copy
  at wide and narrow container widths. The preview updated only the four checked
  advertisers; the other 778 pages remained unassessed. Temporary preview routes
  were removed before the final build.

## Production release — 2026-11-06

Source commit: `8ed73db20fb2426eb7d353fd57dca2ab1029b2d9`, published to `main`.
Migration 167 is applied. The API, web and Hunt worker use these immutable images:

- API: `europe-west1-docker.pkg.dev/peon-489013/agf/api@sha256:58e2331a02070589ef994baa02b75c972ab2f4b4afcc8e3e3e1eee7f104215ab`
- Web: `europe-west1-docker.pkg.dev/peon-489013/agf/web@sha256:d5e82d61e6705eedab9e3deadbdfd19ac0445d589c07f4c235fb71bd209436ce`
- Hunt worker: `europe-west1-docker.pkg.dev/peon-489013/agf/worker@sha256:69434b134385f41627563761caaa2112f9a404fb87b23a0588bda1687e4463e9`

Cloud Build `8de1a919-5951-41da-9dd7-91d6e7e931d4` built API and worker.
Web build `6e511569-0c92-47b8-adaf-c2c4f96530c7` explicitly included the ignored
public production build configuration. The first build's web image was not
selected because the default upload rules excluded that configuration. Both
builds passed. Source archives came from the clean, tested commit.

The GKE rollouts completed with ready containers and zero restarts. Public API
and web health endpoints returned 200; the authenticated Hunt API returned the
new `ad_evidence` field on all 1,598 existing result rows. Only the Hunt worker
service was recreated; its source label matches the release commit. The previous
compose configuration is backed up at
`/opt/agf/docker-compose.before-hunt-relevance-8ed73db.yml` on the worker VM.

The 1,598 original verdicts for Quizlock Hunt 24, rounds 51 and 52, are backed up
at `/opt/agf/release-backups/hunt-24-before-rejudge-8ed73db.json`. Evidence refresh
and re-assessment results are recorded below once complete.

Terminology: this release improves **relevant Hunt results**. A user's shortlist
means their saved/liked advertisers, organized for a product. This release does
not create saves or likes and does not add product-specific learning from saves.
The existing Hunt orchestrator and text-based judge remain in use.

## Follow-up verification

The original four-case test did not establish batch accuracy. Full-round testing
exposed false positives and inconsistent decisions with the older models. The new
judge separates the original product mechanism from its customer outcome and sends
the rubric as system instructions, with advertiser records supplied as data.

- 82 Python tests passed against local Postgres. After the final prompt/model
  selection and formatting, all 23 owning judge tests passed again. These cover
  target validation, malformed and duplicate decisions, the outcome/audience
  decision rules, batching, grouping and persistence.
- A live persistence → judge → database check covers seven rows for one sec,
  BePresent, Untappd and Mathilde Fashion Focus, including a store-vetoed row.
- The complete captured inputs contain 97 and 94 advertisers with usable evidence.
  Packing them reduces 72 sparse classification requests to 10 full requests,
  plus one target derivation per round. This is a request-count improvement;
  the stronger model is slower per request.
- An additional exploratory comparison against the existing MCP judge's 25 labels
  matched 21: all 12 calm cases and 9 of 13 fitness cases, with no false positive.
  Three fitness references were missed and one expected uncertain result was
  rejected. That comparison did **not** pass its exact-match assertion and is not
  evidence of universal accuracy. The separate MCP Jev implementation is unchanged.

Release CI remains red on pre-existing findings in unchanged files:
[backend-ci](https://github.com/EdasL/meta-ads-scraper/actions/runs/42194244171)
reports ST1005 error-string formatting in `tools_engine.go`, `tools_history.go`
and `tools_member.go`;
[backend-security](https://github.com/EdasL/meta-ads-scraper/actions/runs/42194244173)
reports G404 recommendation/diversity randomness and G104 response-body closes
in `preference_repo.go`, `diversity.go` and `saved_ads_controller.go`. These match
the previous release's known baseline; none of those files changed here. Scoped
feature checks and the production builds passed, as recorded above.
