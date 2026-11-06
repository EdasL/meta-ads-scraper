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
