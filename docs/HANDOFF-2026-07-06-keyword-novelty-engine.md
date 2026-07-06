# HANDOFF — Keyword Novelty Engine (2026-07-06)

**For:** the next session picking this up.
**One-line state (updated 2026-07-06, second session):** BOTH pipes are LIVE + verified
in prod. The §5 fast-follow shipped (commits 9742f89 / 1f860e6 / 7e46188): all mint I/O
moved outside the write txn (regression-tested), Google Play suggest batched into one
sidecar process, basin lazy-embed bounded + txn-safe (`load_basin_embeddings`), gate env
knobs unified. `AGF_MINT_AUTOCOMPLETE=1` + `AGF_MINT_BRAINSTORM=1` on the VM (backup:
`docker-compose.yml.bak-preP2enable`). Live one-off mint in the deployed container:
145s, 0 errors, 39 keywords across new_release/autocomplete/brainstorm tags. Suite 659
passed / 0 failed. Remaining §5 wall-clock lever (optional): parallelize the ~90
sequential Apple-hints calls + batch per-category embeds if 145s ever matters. The
shared run-wide slot ceiling (§5 item 4, "consider") was deliberately NOT built — it
could let the new-release pass starve Pipe 2's slots; per-pass budgets stay (≤~45/run
at SLOTS=15). QUALITY FIX (same day, commit c736fe6): the first live run showed the
autocomplete lane minting raw app/brand names ("capcut: photo & video editor", "ugi
utilities, inc." — ~12/15) — user flagged it. Raw autocomplete now routes through
`generalize_autocomplete_keywords` (Gemini: keep generic queries, rewrite app-specific
to their niche, drop no-niche business names) BEFORE the novelty gate; a generalize
failure SKIPS the category (deliberately not degrade-open — raw autocomplete is
majority junk). The 15 junk rows were deleted from prod (unswept trials). Re-verified
live: 175s, 0 errors, 32 minted, autocomplete tag 100% generic niches ("sleep tracker
app", "wifi analyzer app", "event ticketing app"). Suite 664 passed / 0 failed.

---

## 1. Why this exists

The keyword flywheel converges — it re-finds the known app corpus instead of new apps.
Two research docs diagnosed it:
- `docs/research/2026-07-06-adlibrary-scraping-techniques.md` — selecting the next keyword
  by corpus-frequency IS the bug (semantic-subset bias + Meta top-k truncation).
- `docs/research/2026-07-02-novel-discovery-algorithm.md` — flywheel = depth, external
  frame = breadth, must be separate subsystems.

The fix is two "pipes" through one shared redundancy gate:
- **Pipe 1 (fix the flywheel):** drop mined keywords semantically close to basins we've
  already exhausted (retired / high-dry keywords).
- **Pipe 2 (breadth):** source keywords from OUTSIDE our corpus — app-store search
  autocomplete (Apple hints + Google Play suggest) and an LLM adjacent-niche brainstorm.

Spec: `docs/superpowers/specs/2026-07-06-keyword-novelty-engine-design.md`
Plan: `docs/superpowers/plans/2026-07-06-keyword-novelty-engine.md`
SDD ledger (full task-by-task history): `.superpowers/sdd/progress.md`

## 2. What shipped (branch `main`, commits d599b7b..a09383c, 11 commits)

| File | What |
|---|---|
| `db/schema/146_dynamic_keyword_embeddings.sql` | keyword-embedding cache (applied to prod + test) |
| `src/agf/discovery/keyword_redundancy.py` | the gate: `redundancy_scores`, `admit_by_novelty`, `exhausted_basin_embeddings` |
| `src/agf/discovery/store_autocomplete.py` | `apple_hints`, `gather_autocomplete_candidates` |
| `src/agf/enrich_full/appstore.py` + `sidecar/gp_suggest.js` | `gplay_suggest` (Google Play autocomplete) |
| `src/agf/discovery/release_keywords.py` | `brainstorm_niche_keywords` (LLM adjacent-niche) |
| `src/agf/discovery/frame_mint.py` | mint stage: new-release + autocomplete + brainstorm passes, all through the gate + yield-weighted budget |
| `src/agf/workers/main.py` | flywheel gate wiring (`_gate_keywords`) + env knobs |

Full suite: **652 passed, 1 skipped (media-route MinIO), 0 failed.** Whole-branch review: GO.

## 3. What is LIVE in prod (Pipe 1)

The flywheel now admits keywords by novelty. Verified in the deployed worker
(2026-07-06): warm-cache basin load 0.52s, **gate latency 1.35s**, real retired keyword
`ai coach` scored 1.000 → DROPPED, novel phrase 0.635 → ADMITTED. Cosine = dot product
(embeddings are L2-normalized — never re-normalize).

- Env: `AGF_KEYWORD_REDUNDANCY_THRESHOLD` (0.90), `AGF_KEYWORD_BASIN_DRY_FLOOR`
  (= `retire_dry_streak()`, i.e. 5). Gate degrades OPEN on any embed/DB failure.
- The 1,438-keyword basin cache was PRE-WARMED out-of-band so the gate is a cache-hit.
  New retired keywords get lazily embedded a few at a time per flywheel cycle — cheap.

## 4. What is HELD and WHY (Pipe 2)

`AGF_MINT_AUTOCOMPLETE=0` and `AGF_MINT_BRAINSTORM=0` are set in the prod VM compose file
(`/opt/agf/docker-compose.yml`). With both off, the mint stage runs new-release-only
(fast, unchanged from before) — the hoisted basin fetch only runs `if autocomplete or
brainstorm`, so nothing slow fires.

**The bug live-verify caught (fakes hid it — all tests were green):** two things run
INSIDE `with engine.begin() as conn:` in `mint_keywords_from_releases`:
1. `exhausted_basin_embeddings` on a COLD cache embeds ALL ~1438 retired keywords (one
   Gemini HTTP call each, ~7 min) — a long-held transaction. (Watchdog is 125 min so not
   a hard wedge, but wrong.) Mitigated in prod by the pre-warm; still wrong in code.
2. `gather_autocomplete_candidates` spawns ~120 Google Play **node subprocesses** +
   ~120 Apple HTTP calls — inside the same transaction, EVERY daily run (not cached).

## 5. THE FAST-FOLLOW (do this to turn Pipe 2 on)

Refactor `mint_keywords_from_releases` (`src/agf/discovery/frame_mint.py`) so network /
subprocess / LLM work happens OUTSIDE the write transaction:

1. **Move the gather + LLM calls out of `engine.begin()`.** Shape:
   `gather autocomplete + brainstorm candidates (no DB)` → `read basins in a short read
   txn` → `embed candidates + score in memory (network, no txn)` → `short write txn: dedup
   vs tried + budget + insert admitted`. Only the DB writes stay in a transaction.
2. **Batch Google Play `suggest` into ONE node process**, not ~120 spawns. Add a
   sidecar entry (e.g. `gp_suggest_batch.js`) that takes a JSON array of terms and returns
   `{term: [suggestions]}`; call it once per run. (`src/agf/enrich_full/sidecar/`.)
3. **Never embed the full basin set in one hot-path txn.** Either keep the pre-warm as a
   standalone worker stage, or bound/batch the lazy embed with heartbeats.
4. Consider a **shared run-wide slot ceiling**: today the 3 mint passes each draw a full
   `AGF_FRAME_MINT_SLOTS`, so a listings-rich run mints up to ~3× (final review finding).
   With `AGF_FRAME_MINT_SLOTS=15` that's ≤~45/run — fine for now, but a shared decrementing
   ceiling is cleaner. Also fold in: `AUTOCOMPLETE_THRESHOLD`/`AUTOCOMPLETE_DRY_FLOOR` are
   module constants in `frame_mint.py` — make them read the same env knobs as the flywheel
   gate so a tuned dry_floor doesn't desync the two basin definitions.
5. Then: rebuild image, roll worker, set `AGF_MINT_AUTOCOMPLETE=1` + `AGF_MINT_BRAINSTORM=1`,
   run the one-off live mint (below) and confirm it completes in well under a minute with
   keywords tagged `autocomplete:*` / `brainstorm:*` and 0 errors.

Deferred minors (final review, all non-blocking): `content_hash=''` in
dynamic_keyword_embeddings inserts; redundant dedup loop in
`gather_autocomplete_candidates` (`out[cat]=_normalize(raw)` suffices);
`autocomplete_by_category`/`brainstorm_by_category` not pre-init in stats.

## 6. Ops facts (how to build / deploy / verify)

- **Test DB:** `AGF_TEST_DB_URL=postgresql://agf:agf@localhost:55433/agf_it` (docker
  `agf_test_pg`). Run suite: `AGF_TEST_DB_URL=... .venv/bin/pytest -q`. A skip is NOT a
  pass. Raw psql: `docker exec -i agf_test_pg psql -U agf -d agf_it`.
- **Prod DB (read/probe):** `postgresql://agf:<pw>@localhost:5433/agf` via the Mac
  cloud-sql-proxy. Password is in `.mcp.json` (`AGF_DATABASE_URL`).
- **Build image (clean HEAD worktree):** `bash ops/prod/02_build_image.sh` (~3 min,
  Cloud Build → Artifact Registry `worker:latest`).
- **Roll VM:** `gcloud compute ssh agf-worker --zone=europe-west1-b --project=peon-489013
  --command='sudo docker compose -f /opt/agf/docker-compose.yml pull worker cron && ... up
  -d worker cron'`.
- **Env lives in** `/opt/agf/docker-compose.yml` worker `environment:` block (NOT
  `/opt/agf/.env` for these). Backup before novelty: `docker-compose.yml.bak-preNovelty`.
  Set so far: `AGF_FRAME_MINT_SLOTS=15`, `AGF_MINT_AUTOCOMPLETE=0`, `AGF_MINT_BRAINSTORM=0`.
- **One-off live mint in the container** (base64-pipe trick, since the daily gate blocks a
  direct call): `echo <b64 python> | base64 -d | sudo docker compose -f
  /opt/agf/docker-compose.yml exec -T worker python -`. Example scripts used this session
  live in the session scratchpad (mint_verify.py / gate_verify.py / warm_basins.py) — the
  gate_verify.py pattern is the quick health check.
- **Apple hints** needs header `X-Apple-Store-Front: 143441-1,29`. **Google Play
  new-releases is DEAD** (Google removed NEW_* collections — only TOP charts remain, which
  are incumbents we veto); Google Play contributes via `suggest` autocomplete only.

## 7. Memory pointers

- `keyword-novelty-engine-2026-07-06.md` — this whole effort + the held state.
- `adlibrary-scraping-research-2026-07-06.md` — the domain research behind it.
- `yield-weighted-mint-budget-shipped-2026-07-06.md` — the sibling (which CATEGORIES get
  keywords; this handoff is which KEYWORDS).
- Lesson (lessons.md, 2026-07-06 testing): fakes hide real-world I/O cost; never run
  unbounded network/subprocess work inside a write transaction; live-e2e is the gate.
