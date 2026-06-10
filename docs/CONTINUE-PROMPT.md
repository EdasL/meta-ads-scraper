Continue building the Emerging App Finder at ~/Projects/emerging-app-finder. First read, in order: docs/HANDOFF.md (2026-06-10 update block), docs/superpowers/specs/2026-06-10-real-gems-feed.md (the latest shipped change), docs/HANDOFF-gem-signals-2026-06-10.md, and the project memory dir.

State (2026-06-10 evening): the feed shows REAL emerging apps end to end. The discovery→persist→score→feed pipeline ran live: 40 keyword-discovered app candidates persisted with FULL ad sets (3,184 ads; was 483) + reconstructed 16-week velocity curves; quality vetoes shipped (game / no_app / unverified_app / incumbent_audience >500k likes / established_traction >5M installs or >200k ratings across ALL linked listings / incumbent_ad_volume >50 active ads or cap-hit); matview 111 finally honors vetoed=FALSE and structurally excludes DEMO (demo seed = every formerly broken store link). Feed = 8 store-verified gems, each with a MEASURED ad library ≤50 (Plant Identifier, Rosebud, Umi, My Water Tracker, Islamic Habit Tracker, Genart…). 318 Python + Go suite + 56 web tests green. 4 commits: c372658 · c7133aa · ff82781 · 9843229.

Run the pipeline (all proven 2026-06-10):
1. discovery sweep → scripts/discover_experiment.py (writes /tmp/agf-candidates-<date>.json)
2. persist top app candidates: AGF_PERSIST_KINDS=app AGF_PERSIST_LIMIT=40 + AGF_PROXIES + MinIO env → scripts/persist_candidates.py <candidates.json>
3. scripts/count_ads.py (true counts; the incumbent veto's teeth — supports --ids)
4. scripts/refresh_apps.py (free store data + daily snapshots) · scripts/backfill_is_game.py (one-shot for NULLs)
5. run_scoring → matview refresh (persist does this itself at the end)

Highest-leverage next work:
1. **Daily cadence** — run the worker + count_ads + refresh_apps daily so velocity curves accrue beyond the reconstructed backfill.
2. **Candidate breadth** — only the top-40 (of 234) app candidates from the 2026-06-10 sweep were persisted; persist the tail + re-sweep with fresh keywords (src/agf/discovery/taxonomy.py).
3. **Rotate the DataImpulse password** (still recoverable from session logs; staged copy at ~/.agf_proxy_url chmod 600).
4. The parallel discovery-session WIP (window_growth "ramp" scoring in run_scoring/gemscore, tracker/growth+history, web design tokens, backend gemini explainers, migrations 103–106) is still uncommitted — leave its staging to that workstream; features.py already carries its FV groundwork (committed, additive).

Carry the hard rules: curl_cffi+rd_challenge behind residential proxies (backoff on 1675004); Meta has no US impressions/spend; veto thresholds live in src/agf/scoring/vetoes.py as constants (ad-volume gem ceiling = 50 by user decision 2026-06-10); NULL/unknown never vetoes EXCEPT unverified_app (a linked app whose store lookup resolved nothing = delisted = veto); stage explicit git paths; plain product voice, grug only in GrugExplainer; live e2e before declaring done.
