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
