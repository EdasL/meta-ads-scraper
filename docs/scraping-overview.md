# how scraping work — grug walkthrough

_as of 2026-07-10. all numbers checked against code same day. code locations at bottom._

grug want find small app that spend real money on meta ads before app get big. meta not give grug list of all ads. meta only give search box. so grug search. many times. forever. everything below follow from this.

two clocks run the whole thing:

- **worker clock**: one python process on the VM. does one full cycle (~40 min of work), then sleeps 1 hour, then does it again. so a new cycle starts roughly every 1h40m, ~14 cycles a day, around the clock.
- **cron clock**: separate container. every hour at :00 it re-refreshes the feed view. every night at **02:15 UTC** it runs the big scoring pass. this nightly pass is the ONLY thing that can put a NEW gem on the website. remember this, it is the punchline of the whole doc.

## one worker cycle, in order

### step 1 — sweep (~most of the cycle)

grug take **301 keywords** (hand-made niche terms like "sleep tracker app", plus store-link fragments) and searches meta's ad library with each one. not the official API — that one is empty for commercial ads. grug calls the same internal GraphQL endpoint the ad library website itself uses, dressed up as a real Chrome browser (curl_cffi TLS impersonation), through rotating residential proxies (DataImpulse) so meta not get suspicious of one IP.

each search: **US only, active ads only**. meta answers **30 ads per page**, sorted **biggest spenders first**, plus a cursor for next page. grug waits **2 seconds between pages**, **3 seconds between keywords** (politeness = not getting blocked).

per keyword grug keeps flipping pages until one of three things:

- meta says no more pages, or
- **3 pages in a row with nothing new** (patience runs out), or
- **15 pages** (hard cap).

why go deep at all? because biggest-spenders-first means page 1 is Calm and Duolingo. the gems — small brand, real spend — live on page 3, 4, 7. fertile keyword gets dug deep, dead keyword stops at page 1 or 2 and budget goes elsewhere.

what one ad gives grug: the meta **page** running it (name, id, like count), the creative (text, image, video), **the link it points to**, start date, active or not. grug sorts every page found:

- page has **>10,000 likes** → incumbent, ignore forever
- ad links to **App Store / Google Play** → new row in the candidates table, with the keyword that found it and how deep it sat
- ad links to a **real web landing page** (not a store redirect, not an ad-tracker bounce) → funnel candidate, side lane, see bottom
- anything else → trash

### step 2 — triage, right after sweep (~minutes)

candidate just means "someone runs app ads". triage asks the only question grug cares about: **small or already big?** grug looks the app up in its store — apple lookup API in batches of 200, google play via a node scraper in batches of 50 — and applies ONE rule:

> **more than 5,000 ratings → drop. else → keep.**

that's the whole gate. installs, revenue guesses, age floors — all tried, all removed. rating count is the cleanest "already made it" signal that works on both stores.

a keeper is *promoted*: it gets an advertiser row in the corpus. important detail: at this moment grug knows the app exists but has **zero of its ads** stored. that comes next.

### step 3 — keeper history (same cycle, minutes later)

now grug uses the OTHER door into the ad library: pull one specific page's ads directly by page id, no keyword. for the new keeper grug pulls its whole ad listing and — key trick — **rebuilds the last 16 weeks of history from the ads' own start/end dates**. meta says when each ad started, so grug can compute "how many ads did this page have running in each of the last 16 weeks" **on day one**, without waiting 16 weeks. this reconstructed weekly series is the raw material for scoring.

budget for this: half of the 600-requests-per-cycle discovery budget.

### step 4 — housekeeping stages (same cycle)

- weekly active-ad counts snapshotted for everyone
- app store metadata resolved / refreshed (each app once a day: ratings, reviews)
- **corpus refresh** (once a day, own budget of **3,000 requests**): re-pull the *stalest* known advertisers' listings so their history keeps moving — new ads appear, dead ads get flipped inactive. without this, every advertiser stays frozen at its discovery snapshot and the feed plateaus (this exact thing happened; it was bad).
- **flywheel**: sweep the dynamic keywords (mined from ad copy of gems grug already keeps) and mint/rest/wake them. a keyword that keeps finding nothing rests with an exponentially growing cooldown, then wakes and gets retried. nothing dies forever.
- funnel lane sweep (28 quiz-y phrases, 8 pages; once a week a 100-page deep dig)

### step 5 — enrich (~21 gems per cycle)

the deep look, budgeted at ~14 meta requests per gem. grug re-pulls the advertiser's ads **including inactive ones**, downloads media for the top ads to cloud storage (so dashboard shows real creatives), snapshots the store listing, and if any ad points at a web page — renders it headless, screenshots it, classifies it (quiz? subscription checkout?).

which gems go first? each candidate's store listing gets embedded and compared to gems the user liked — 75% of the queue is taste-sorted, 25% random so grug doesn't tunnel-vision.

### step 6 — repairs, then sleep

missing media gets patched (12 per cycle), missing funnel screenshots retried. cycle over. sleep 3600 seconds. do it all again.

## night: the scoring pass (02:15 UTC)

once a night the cron scores **every advertiser in the corpus**:

- compute **GemScore** 0–100. two things dominate: **scaling velocity** (~42% weight — how long the winner ads have been running, capped at 35 days because one month of continuous spend already proves a winner and beyond that "age" just rewards incumbents; plus ad-count growth and creative iteration) and **demand** (~38% — is the store side confirming: ratings accumulating, reviews coming). small weights for rating quality, funnel, web presence.
- apply **vetoes** — hard kills regardless of score: fewer than **2 weeks** of observed weekly history (too early to tell), more than **200 active ads** (funded growth team), more than **10,000 page likes**, more than **5,000 ratings**, is a game, no resolvable app, is a relaunch of an old app.
- write the score rows, then **refresh the feed view**.

the website reads only that view. the hourly :00 refresh updates numbers/media for gems that already have a score row — but a NEW gem physically cannot appear before the nightly scoring gives it a score row. that is the gate on "when does the user see it".

## full example: one gem, found to seen

fake app "SleepySheep" (sleep-sounds subscription app). all times tuesday, UTC.

- **09:00** — worker wakes, cycle starts. sweep begins walking the 301 keywords.
- **09:31** — keyword "sleep tracker app", page 4. ad #17: page "SleepySheep", 2,340 likes (under 10k — not incumbent), ad links to the App Store. candidate row written: found by "sleep tracker app", depth 4.
- **09:44** — sweep ends. triage runs on this cycle's ~40 new candidates. apple batch lookup: SleepySheep has **1,850 ratings** → under 5,000 → **KEEP**. advertiser row created. still zero ads stored.
- **09:52** — keeper history: pull SleepySheep's whole page by page id → **34 ads**. oldest still-running ad started 45 days ago. rebuild 16 monday-aligned weeks from the start dates → **7 weeks of non-zero history, on day one**. ad rows + weekly series written.
- **10:05** — embeddings: SleepySheep's store listing embedded; it looks like stuff the user liked before → front of the enrich queue.
- **10:12** — enrich (it made the ~21-gem cut): full pull with inactive ads, media for top 5 ads → cloud storage, store snapshot, one ad points to `quiz.sleepysheep.com` → rendered, screenshotted, classified: subscription quiz funnel. nice signal.
- **10:40** — repairs, cycle ends, worker sleeps. rest of tuesday: ~8 more cycles happen; SleepySheep is now just a known advertiser getting routine refreshes. **it is NOT on the website yet** — no score row exists.
- **wednesday 02:15** — nightly scoring. SleepySheep: 7 weeks observed (≥2, passes), 34 active ads (≤200), 2,340 likes (≤10k), 1,850 ratings (≤5k), not a game, app verified, not a relaunch → **no veto**. winner ad age 45d → capped at 35 → strong velocity axis; ratings clearly accumulating → strong demand axis. **GemScore 64** → also flagged as alert (≥60).
- **~02:25** — scoring finishes, refreshes the feed view. SleepySheep now exists in `frontend.emerging_scaling_apps`.
- **08:30** — user opens app.peon.works, Discover feed. SleepySheep is there: GemScore, real ad creatives, growth series, quiz-funnel link. review queue sees it too (same view underneath).

**found 09:31 tuesday → visible 02:25 wednesday. ~17 hours.** the wait is almost entirely "waiting for the nightly scoring". worst case (found at 03:00, just after the pass) ≈ 23 hours.

the one slow path: if ALL of SleepySheep's ads had started only 4 days ago, the reconstructed series would show 1 week → vetoed as "insufficient persistence" → invisible. grug keeps refreshing it daily anyway, and the night its history crosses 2 weeks it scores and appears by itself. this veto is why the feed doesn't fill with day-old ad tests.

and the loop closes: if the user LIKES SleepySheep, its ad copy gets mined for phrases ("fall asleep faster", "sleep sounds") → new keywords → tomorrow's sweeps search for its cousins. what user likes literally steers what grug hunts next.

## side lane: funnels

some of the best finds are not app ads — they are **web quiz funnels** (localized quiz → subscription checkout), often before an app exists. web-funnel rejects from the app sweep plus the 28 dedicated funnel phrases feed this lane; daily 8-page sweeps, weekly 100-page deep dig (funnels hide deep in impressions-ranked results). each candidate's landing page is actually rendered and confirmed (quiz steps counted, payment/pixel vendors fingerprinted, payoff classified — only digital kept). app-backed funnels bridge back into the app pipeline; pure web ones show in the feed with a "Web only" badge.

## where code live

- worker loop, stage order, budgets — `src/agf/workers/main.py` (`run_once`)
- keywords: taxonomy / lifecycle / mining — `src/agf/discovery/taxonomy.py`, `db/dynamic_keywords.py`, `keyword_mining.py`
- sweep + adaptive depth — `src/agf/discovery/sweep.py`
- meta GraphQL client + proxies — `src/agf/extract/search.py`, `session_pool.py`, `proxy.py`, `tls_client.py`
- triage + store lookups — `src/agf/discovery/triage.py`, `enrich_full/appstore.py`, `enrich_full/sidecar/gp_lookup.js`
- keeper history (16-week reconstruction) — `src/agf/discovery/keeper_history.py`
- enrich — `src/agf/enrich_full/run_enrich.py`, `funnel_capture.py`
- corpus refresh — `src/agf/discovery/corpus_refresh.py`
- funnel sweep/confirm — `src/agf/discovery/funnel_sweep.py`, `funnel_confirm.py`
- gemscore + vetoes — `src/agf/scoring/gemscore.py`, `vetoes.py`, `run_scoring.py`
- feed view — `db/schema/141_emerging_matview_websub.sql` → `frontend.emerging_scaling_apps`
- cron — `ops/cron/crontab` (matview hourly at :00, scoring nightly 02:15 UTC)
- runtime — `ops/docker-compose.yml` (worker + cron containers on the VM), Go API on GKE

knobs grug touches most: `AGF_SWEEP_INTERVAL_SEC=3600` (sleep between cycles), `AGF_SWEEP_PATIENCE_PAGES=3` / `AGF_SWEEP_MAX_DEPTH=15` (dig depth), `AGF_DISCOVERY_ENRICH_BUDGET=600` (per-cycle meta requests, half keeper-history half enrich), `AGF_CORPUS_REFRESH_BUDGET=3000` (daily re-scrape), `AGF_FUNNEL_DEEP_PAGES=100` (weekly deep dig), `AGF_PREFETCH_TIMEOUT_SEC=180` (per-advertiser hang ceiling), `AGF_PROXIES` (DataImpulse).
