# Product-linked folders

Attach the user's own product to an existing Saved folder. The product identifies
what they are researching; brands saved inside the folder are references for that
product. A folder name alone or another saved competitor is not this association.

1. Add one optional product field to existing folders (name, URL or brief). Reuse
   folder ownership and update endpoints; expose the product and memberships to
   agents so they can distinguish the reference product from saved brands.
2. Let a hunt reference an owned folder. Supply its attached product and a bounded
   set of saved brands with actual ad copy to planning and relevance. Add a Hunt
   action in the folder view and show its selected context in the Hunt composer.
3. Verify ownership, deletion, context updates, model payloads and persistence
   against local Postgres; check the rendered folder/Hunt flow and owning builds.

Existing folders and likes remain intact. Empty folders supply no saved examples;
unrelated folders and account-wide likes do not enter the hunt's context.

## Verification

- Migration 168 applied successfully to disposable local Postgres. HTTP/database
  integration checks passed for attach, trim, clear, rename preservation,
  ownership, cross-owner reference exclusion, memberships, updated context, and
  folder deletion without deleting a hunt.
- Go helper, controller and MCP tests passed; scoped Go vet passed. Python judge
  and Hunt database tests passed (32 tests). Saved/Hunt helper tests passed
  (57 tests), along with scoped ESLint, the Next production build, and
  `git diff --check`.
- Browser → Next proxy → Go API → Postgres: attached Quizlock to a folder,
  saved it, reloaded, and confirmed one sec/BePresent remained separate saved
  references. Starting a hunt from that folder sent the stored association to
  the real Gemini planner, which proposed screen-time searches and both saved
  references. Reloading restored the created conversation and folder context.
- The actual Python judge read the same product and two saved references from
  Postgres. With seven captured real-ad rows for four advertisers, the live
  model kept one sec and BePresent and rejected Untappd and Mathilde Fashion
  Focus; all persisted rows agreed. The check took 9.95 seconds. This verifies
  the integration and four examples, not general recommendation accuracy.

The browser used disposable test data and normal local authentication. Temporary
login tooling was removed before the production build. The current browser tool
has no viewport-resize capability; only its standard viewport was inspected.

Context is scoped to the selected folder and its owner. Planning and judging use
up to 12 saved brands, each with up to two captured ad-copy samples and a store
description where available. This adds explicit product context to new hunts;
it does not automatically attach existing hunts or reorganize existing saves.
Agents can read the association through `my_saved_gems` (`folders`, `group_ids`)
and pass `folder_id` when starting a managed hunt.

## Production release — 2026-11-06

Source commit `a34974365cd1035138fd8b996fdb69217aa21e97` is published to `main`.
Cloud Build `31669bc4-c95f-4a6e-8692-933f74c311ba` succeeded for all three images
from a clean export of that commit. The web build explicitly included the public
production API URL and live-data configuration. Migration 168 was applied before
the rollout and is recorded in `schema_migrations`.

Deployed immutable images:

- API: `europe-west1-docker.pkg.dev/peon-489013/agf/api@sha256:cb010151d1b2ed6d34947aa9b4c4f38e70f296ec0af6999e81a153cf03f3804b`
- Web: `europe-west1-docker.pkg.dev/peon-489013/agf/web@sha256:1a9c69e43259b97b461b5668555ee40b496b19621e59d34e73246cc974bbb12c`
- Hunt worker: `europe-west1-docker.pkg.dev/peon-489013/agf/worker@sha256:0b5fa5ff9c1ec76af2b0cdcb70d70af68216cf5c6016ed4bc3d3aac704725c1f`

API rolled out before web. Both deployments are ready with zero container
restarts. API and web health endpoints returned 200; anonymous access to saved
folders remained 401. No Hunt rounds were active before updating the worker.
Only the Hunt service was recreated; its preceding compose configuration is
preserved at `/opt/agf/docker-compose.before-folder-products-a349743.yml`.

Production verification:

- Authenticated API: created a temporary folder, attached a product, read it
  back, renamed the folder without losing the product, cleared the product,
  then removed the temporary folder. Existing folders and saves were untouched.
- Authenticated MCP: `run_hunt` exposes `folder_id`; the research guide describes
  its use; `my_saved_gems` returns the two existing folders with `product` and
  all 47 saved brands with `group_ids`.
- Deployed worker: its folder reader and shared reference view work with actual
  production database credentials; migration 168 is recorded.
- The first API/MCP probes encountered transient 502 responses during rollout.
  Subsequent authenticated checks passed. The production browser reached login;
  authenticated visual verification used the local browser flow described above.

Repository-wide CI remains red on the existing baseline:
[backend-ci](https://github.com/EdasL/meta-ads-scraper/actions/runs/42204614351)
reports 48 ST1005 error-string findings. Every flagged source line also exists in
the preceding commit. Its later lint/race-test steps were skipped, not passed.
[backend-security](https://github.com/EdasL/meta-ads-scraper/actions/runs/42204614330)
reports the same four G404/G104 findings in unchanged preference, diversity and
saved-ad code. The scoped feature checks and production builds passed; Gitleaks
found no secrets in the outgoing source commit.

## Follow-up: reusable products from links

The folder text field does not represent a reusable product. Replace the UI with
an owned product record, created from an Ad Library, store or website link. Match
existing scraped records first; show ambiguous matches for selection. Unknown
products remain usable with a name/description, without claiming their ads exist.

1. Add owned products and explicit product IDs on folders and Hunt chats. Preserve
   legacy folder text and migrate existing associations without losing context.
2. Share a link resolver and product picker between folder attachment and the
   new-chat composer. A chat keeps its selected product independently of later
   folder changes. Use that product's actual ads and saved folder references.
3. Expose the same products/context through MCP, verify ownership and URL matching
   against Postgres, inspect the actual browser flow and live planner/judge, then
   release the verified source and record the deployed images.

### Reusable-product verification

Implemented owned products from website, App Store, Google Play and Meta Ad
Library links. Existing shared discovery data and the caller's own MCP captures
supply names, descriptions and ad samples; private captures from other users are
excluded. Multiple matching pages require an explicit choice. Unknown products
can be added with a name/description without claiming that ads were found.
Link lookup does not fetch a website or start a scrape. Agents can explicitly
fetch fresh ads with the existing scraping tools.

Folders now attach a `product_id`; new Hunt chats select their product directly.
A folder launch inherits its product. A product-only chat uses references from
all its attached folders; a folder launch narrows them to that folder. Changing
a folder's attachment cannot change an existing chat's product or import another
product's references. Migration 169 preserves earlier folder descriptions as
product records and associates existing folder-linked hunts.

- New database-backed API/repository and MCP workflow checks passed, including
  unknown links, stored Ad Library and App Store data, ambiguous websites,
  duplicate identities, cross-owner privacy, folder detach, stable chat products,
  and MCP product creation → product-selected hunt with a default intent.
  Existing folder/Hunt/MCP integration checks passed against local Postgres.
- Go helpers/controllers/MCP checks and scoped vet passed. Python judge/Hunt
  database checks passed (33 tests). Web Saved/Hunt/proxy checks passed (62 tests),
  scoped ESLint and the Next production build passed, and `git diff --check`
  was clean. Temporary local login tooling was removed before the build.
- Browser → Next proxy → Go → Postgres: added Quizlock from a website with no
  stored match, attached it to the reference folder and reloaded successfully.
  Added one sec from its Ad Library page, reusing its stored name and two captured
  ad samples. Started a new chat by selecting Quizlock directly, without a folder
  launch; the saved chat has `product_id` and no `folder_id`, and reload retains
  its product and reference folders. A stale initial history response can no
  longer replace a newly opened blank chat.
- The actual planner received the selected product and saved references and
  proposed screen-time searches including one sec and BePresent. The Python judge
  read the same product record and two references from Postgres, then judged seven
  captured rows for four advertisers in 12.88 seconds: one sec and BePresent were
  relevant; Untappd and Mathilde Fashion Focus were off-target. This is a focused
  integration check, not a general accuracy measurement.

The product evidence view uses separate advertiser/app lookups and a new partial
index on `public.ads(app_id)`. For production, build that index concurrently
before applying migration 169, so the migration's idempotent index declaration
will not block ongoing ad ingestion.

### Reusable-product production release — 2026-11-07

Source commit `26d64a166731eea429d9d0afea4fc2218ffa51cf` is published to `main`.
Regional Cloud Build `d4778fe0-3ee0-409c-bc93-e158aaa743fa` succeeded from a clean
export of that commit, with the public production API URL and live-data setting
included in the web build.

Deployed immutable images:

- API: `europe-west1-docker.pkg.dev/peon-489013/agf/api@sha256:dac793932ceaa7bd41764857bc1c9f407ea4ebe3eb4c785df5ef74f9f42c031f`
- Web: `europe-west1-docker.pkg.dev/peon-489013/agf/web@sha256:40a93a50a0d2e12948ebdb04da59c3e21371082b849341ce7b667a4b3ef81b95`
- Hunt worker: `europe-west1-docker.pkg.dev/peon-489013/agf/worker@sha256:9a53b574c88a7ba65d4d4606ae1ecd5783c53b1f976921e074252c43e1e86826`

The app/ad index was built concurrently and verified valid. Migration 169 then
completed transactionally and was recorded in `schema_migrations`. No Hunt rounds
were queued or running before the worker restart. Only the Hunt service was
recreated; its preceding compose configuration is preserved at
`/opt/agf/docker-compose.before-research-products-26d64a1.yml`.

API rollout and authenticated checks completed before web rollout. Both deployed
pods are ready on the expected digests with zero container restarts. The Hunt
worker is running with zero restarts and reports the exact source revision above.

Production verification:

- A stored one sec Ad Library link resolved in 0.1 seconds. A temporary product
  was created, read back, attached to a temporary folder, and detached.
- Authenticated MCP exposed the product-aware Hunt schema and guide, resolved a
  product link, and reused the product created through the API without duplication.
- The deployed worker read the product evidence view with production credentials;
  migration and index checks passed. The selected-product planner/judge and browser
  flows were verified locally against real dependencies as described above.
- All temporary product/folder records were removed. API and web health endpoints
  returned 200. Anonymous product/folder API requests returned 401, and the public
  Hunt route redirected to login as expected.

Repository-wide CI still reports the pre-existing baseline:
[backend-ci](https://github.com/EdasL/meta-ads-scraper/actions/runs/42224886768) has 48 ST1005
findings with no newly flagged source lines; its skipped later checks are not
passes. [backend-security](https://github.com/EdasL/meta-ads-scraper/actions/runs/42224886580)
reports the same four G404/G104 findings in unchanged files. The scoped feature
checks and production builds passed; Gitleaks found no secrets in the source
commit.
