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
Migration 167 is applied. The initial deployment used these immutable images;
the Hunt worker was subsequently replaced by the correction recorded below:

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
at `/opt/agf/release-backups/hunt-24-before-rejudge-8ed73db.json`.

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
  the stronger model is slower per request. Each full round took about 117 seconds;
  five omitted decisions were recovered in one focused 8.76-second retry.
- An additional exploratory comparison against the existing MCP judge's 25 labels
  matched 21: all 12 calm cases and 9 of 13 fitness cases, with no false positive.
  Three fitness references were missed and one expected uncertain result was
  rejected. That comparison did **not** pass its exact-match assertion and is not
  evidence of universal accuracy. The separate MCP Jev implementation is unchanged.

## Existing Quizlock hunt refresh

Hunt 24 contains 1,598 rows and 1,352 unique advertiser pages across rounds 51 and
52. Fifty production MCP searches refreshed previously relevant and named pages:
48 completed, two failed, and 373 ads were returned. The failed Quizlock and
Workout Locker searches were not treated as evidence of irrelevance. Stored ad
copy and listing descriptions remained available where present.

The first full re-assessment failed its BePresent regression assertion and was
superseded. The verified replacement decisions were applied in one transaction,
after checking that the original brief, result membership and evidence still
matched the captured input. Only relevance labels and reasons were changed.

- Round 51: 14 relevant, 82 off-target and 686 uncertain advertiser pages.
- Round 52: 8 relevant, 85 off-target and 655 uncertain advertiser pages.
- Seventeen distinct advertisers are relevant in at least one round. One sec and
  BePresent are relevant in both; Untappd and Mathilde Fashion Focus are off-target.
  The checked trivia, cash-reward, diet, drama, compensation and research-recruitment
  examples are off-target.
- The authenticated production API returned all 1,598 rows with reasons and
  evidence arrays. All store/web rows for a page agree within each round. Saved
  status and ratings match the snapshot taken before this replacement.
- One borderline advertiser, Scroll The Bible, differs across rounds. This is
  not a guarantee of stable judgments across separate searches. Most uncertain
  results lack usable captured evidence; the refresh did not rescrape every page.

The local UI was rendered and checked during implementation. The production
browser required login, so production verification used the authenticated API
and public health endpoints; it did not include an authenticated browser session.

## Worker correction release

Commit `e272227d07160e99ea76b5143f284b76e3fe22c0` is published to `main`.
Cloud Build `10f698c2-fcc2-4cae-87a6-ffc453a238d1` succeeded from a clean export of
that commit. The Hunt service now uses:

`europe-west1-docker.pkg.dev/peon-489013/agf/worker@sha256:1edf9c6760e037eb844526659e906c57cc76fcfa48815ddc2010e4ffad3512a4`

The API and web retain their verified images above. Only the Hunt service was
recreated. Its preceding compose configuration is preserved at
`/opt/agf/docker-compose.before-hunt-target-e272227.yml`. The outgoing source commit
passed Gitleaks. No additional GitHub workflow was triggered by this Python-only
correction; the earlier backend CI baseline remains documented below.

The deployed worker is running with zero restarts and the expected source revision.
A read-only check inside that container used its production credentials and the
new model: target derivation and four real-ad judgments passed in 10.38 seconds,
covering seven store/web rows. One sec and BePresent were relevant; Untappd and
Mathilde Fashion Focus were off-target. API and web health endpoints returned 200.

Release CI remains red on pre-existing findings in unchanged files:
[backend-ci](https://github.com/EdasL/meta-ads-scraper/actions/runs/42194244171)
reports ST1005 error-string formatting in `tools_engine.go`, `tools_history.go`
and `tools_member.go`;
[backend-security](https://github.com/EdasL/meta-ads-scraper/actions/runs/42194244173)
reports G404 recommendation/diversity randomness and G104 response-body closes
in `preference_repo.go`, `diversity.go` and `saved_ads_controller.go`. These match
the previous release's known baseline; none of those files changed here. Scoped
feature checks and the production builds passed, as recorded above.
