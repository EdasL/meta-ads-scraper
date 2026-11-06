# Hunt relevance and review

The Quizlock round returned 825 rows. Store triage, advertising relevance and
unjudged results are mixed together; competitor searches bypass relevance, and
the judge requires matching product mechanics instead of the advertised need.

1. Keep a bounded sample of the actual ads found. Judge every advertiser once,
   including competitors and apps rejected by store triage. Compare customer
   outcome and audience; leave insufficient evidence uncertain.
2. Group before filtering. Show a bounded shortlist with reasons and ad copy;
   keep uncertain and off-target results searchable without flooding the page.
3. Verify persistence, judgment and grouping regressions, test the real model on
   Quizlock evidence, and inspect the rendered results before release.

The existing Hunt orchestrator remains in use. This change does not represent a
full migration of the website to the separate agent-controlled MCP workflow.

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
  the shortlist, 12-result review page, cross-group search and expandable ad copy
  at wide and narrow container widths. The preview updated only the four checked
  advertisers; the other 778 pages remained unassessed. Temporary preview routes
  were removed before the final build.

## Release status

Not deployed. The Google Cloud account requires reauthentication (`gcloud auth
login`); the existing MCP personal key still works. No production Hunt verdicts
have been rewritten. After access is restored, apply migration 167 before
rolling out the API, web and Hunt worker, then refresh evidence and rejudge the
existing Quizlock rounds. Older rounds without captured copy may remain uncertain
until evidence is fetched. The judge remains text-based and the old Hunt search
orchestrator remains in place.
