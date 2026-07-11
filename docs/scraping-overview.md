# how scraping work — grug walkthrough

_as of 2026-07-10 (updated for the concurrency + queue refactor: parallel sweep, enrich moved to a queue, compute moved to cron). all numbers checked against code same day. code locations at bottom._

grug want find small app that spend real money on meta ads before app get big. meta not give grug list of all ads. meta only give search box. so grug search. many times. forever. everything below follow from this.

**three clocks run the whole thing** (used to be two — the refactor split enrich off into its own):

- **worker clock**: one python process on the VM. does one discovery cycle — sweep → triage → keeper history → housekeeping → **enqueue the gems that need enriching** — then sleeps 1 hour, then again. the sweep now runs **3 keywords at once** (see step 1), so the cycle is faster than the old ~40 min. around the clock, ~14 cycles a day.
- **pipeline clock**: a SECOND always-on process (the `pipeline` container). it drains the enrich queue continuously. the worker no longer does the slow enrich itself — it drops gems on a queue and this worker picks each one up within seconds and does the deep pull. so a kept gem gets enriched minutes after it's found, all day, instead of waiting for the end of a worker cycle.
- **cron clock**: separate container. **every 15 min** a compute pass (embeddings + taste preference + weekly counts). **every 30 min** the big scoring pass (scores every advertiser, writes score rows, refreshes the feed). **every hour at :00** a feed-view refresh. the 30-min scoring is what puts a NEW gem on the website — remember this, it is the punchline.

## one worker cycle, in order

### step 1 — sweep (~most of the cycle, now 3-wide)

grug take **301 keywords** (hand-made niche terms like "sleep tracker app", plus store-link fragments) and searches meta's ad library with each one. not the official API — that one is empty for commercial ads. grug calls the same internal GraphQL endpoint the ad library website itself uses, dressed up as a real Chrome browser (curl_cffi TLS impersonation), through rotating residential proxies (DataImpulse) so meta not get suspicious of one IP.

**new (2026-07-10): grug runs 3 of these searches at the same time.** three lanes, each with its OWN proxy and its OWN warm browser session. DataImpulse hands every connection a different residential IP, so 3-at-once still doesn't look like one suspicious IP. same politeness delays *per lane* (2s between pages, 3s between keywords), but ~3x more keywords covered per minute — the sweep used to eat most of a ~40-min cycle, now it's the big win of the refactor. (`AGF_SWEEP_WORKERS=3`; drop to 2 if meta starts throwing challenges. if a whole gateway dies all 3 lanes trip a shared circuit-breaker and the cycle aborts + retries next tick.)

**newer (2026-07-11): the lanes share one dynamic queue instead of fixed thirds.** before, keywords were split into 3 fixed shards up front — the slowest shard set the finish time while the other two lanes sat idle at the end. now a free lane just claims the next unstarted keyword, and a keyword's follow-up pages stay on the lane that fetched page 1 (cursor + warm session belong together). even the 2s between-pages wait is useful now: the lane fetches some other keyword's page in the gap. no lane idles while work remains.

**measuring it:** `scripts/benchmark_sweep_pool.py` runs a bounded real pass (real proxies, real DB, no worker loop) and prints pages/hour, per-lane telemetry, and candidates per 1,000 requests. to compare scheduler variants honestly, run the SAME keywords, `--pages`, proxy set, and pacing env at `--workers 1` (serial baseline) then `--workers 3`, and only call the faster run a win if errors/challenges stay flat — more retries is not more throughput. the worker also prints the same telemetry each cycle in its summary line (`pages= request_s= queue_s= persist_s= lane_errors= peak_ready=`).

each search: **US only, active ads only**. meta answers **30 ads per page**, sorted **biggest spenders first**, plus a cursor for next page. per keyword grug keeps flipping pages until one of three things:

- meta says no more pages, or
- **3 pages in a row with nothing new** (patience runs out), or
- **15 pages** (hard cap).

why go deep at all? because biggest-spenders-first means page 1 is Calm and Duolingo. the gems — small brand, real spend — live on page 3, 4, 7. fertile keyword gets dug deep, dead keyword stops at page 1 or 2 and budget goes elsewhere.

what one ad gives grug: the meta **page** running it (name, id, like count), the creative (text, image, video), **the link it points to**, start date, active or not. grug sorts every page found:

- ad links to **App Store / Google Play** → new row in the candidates table, with the keyword that found it and how deep it sat. no page-size limit for apps (the old 10k-likes ceiling was removed 2026-07-10 — the store ratings gate at triage is the size filter now)
- ad links to a **real web landing page** (not a store redirect, not an ad-tracker bounce) → funnel candidate, side lane, see bottom (funnels DO keep a likes ceiling, a huge one: 5M)
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

- app store metadata resolved / refreshed (each app once a day: ratings, reviews)
- **corpus refresh** (once a day, own budget of **3,000 requests**): re-pull the *stalest* known advertisers' listings so their history keeps moving — new ads appear, dead ads get flipped inactive. without this, every advertiser stays frozen at its discovery snapshot and the feed plateaus (this exact thing happened; it was bad).
- **flywheel**: sweep the dynamic keywords and mint new ones — full mechanics in "how the keyword pool grows" below.
- funnel lane sweep (28 quiz-y phrases, 8 pages; once a week a 100-page deep dig)
- generic-deep sweep (once a week): ~19 plain copy-phrases run to exhaustion, catching advertisers the niche-noun keywords miss

(weekly ad-count snapshots and the embeddings used to live here too — they moved to the 15-min compute cron in the refactor, see "the cron passes" below.)

### step 5 — hand the gems off to enrich (fast now)

grug USED to do the deep enrich right here — ~21 gems, the slowest part of the whole cycle. **not anymore (2026-07-10).** now grug just picks the same ~21 gems and drops them on a queue (the `pipeline_jobs` table), then the worker cycle moves on. same selection as before: taste-sorted (each candidate's store listing embedded and compared to gems the user liked — 75% of the queue most-like-what-user-liked, 25% random so grug doesn't tunnel-vision), same per-cycle budget, same "skip anything enriched in the last 7 days" rule.

the **pipeline worker** (its own always-on process) drains that queue continuously — claims the oldest queued gem (Postgres SKIP-LOCKED, so it never double-runs), and does the deep look: re-pull the advertiser's ads **including inactive ones**, download media for the top ads to cloud storage (so dashboard shows real creatives), snapshot the store listing, and if any ad points at a web page — render it headless, screenshot it, classify it (quiz? subscription checkout?). one gem at a time, **crash-isolated** (each enrich runs in its own subprocess with a **180s ceiling**), and if the pipeline worker itself dies mid-gem, a **lease-reaper** requeues the job so nothing gets stuck. after each gem — win OR fail — grug stamps the "last crawled" clock so a failing gem waits out its TTL instead of getting re-tried every cycle.

net effect: enrich is no longer gated by the once-an-hour worker cycle. a kept gem is usually fully enriched **minutes** after it's found. (the on-demand "Refresh data" button on the dashboard is a SEPARATE queue with the same shape — untouched by this change.)

### step 6 — repairs, then sleep

missing media gets patched (12 per cycle), missing funnel screenshots retried. cycle over. sleep 3600 seconds. do it all again.

## the cron passes (every 15 and 30 min — NOT nightly)

two passes run on the clock, in the `cron` container, separate from the worker:

- **every 15 min — compute.** embed new/changed apps (Gemini), re-score every user's taste preference against those embeddings, and snapshot this week's ad counts for everyone. this is what keeps the Discover feed's personalization fresh. it was moved off the worker cycle on 2026-07-10 so feed freshness never waits behind a slow scrape. (crowding is recomputed here too, only when new embeddings actually landed.)
- **every 30 min — scoring.** score **every advertiser in the corpus**:
  - compute **GemScore** 0–100. two things dominate: **scaling velocity** (~42% weight — how long the winner ads have been running, capped at 35 days because one month of continuous spend already proves a winner and beyond that "age" just rewards incumbents; plus ad-count growth and creative iteration) and **demand** (~38% — is the store side confirming: ratings accumulating, reviews coming). small weights for rating quality, funnel, web presence.
  - apply **vetoes** — hard kills regardless of score: fewer than **2 weeks** of observed weekly history (too early to tell), more than **200 active ads** (funded growth team), more than **5,000 ratings**, is a game, no resolvable app, is a relaunch of an old app. (the 10,000-page-likes veto existed until 2026-07-10, then removed.)
  - write the score rows, then **refresh the feed view**.

the website reads only that view. the hourly :00 refresh updates numbers/media for gems that already have a score row — but a NEW gem physically cannot appear until a scoring pass gives it a score row. that pass runs every 30 minutes, so once a gem has a weekly series (from keeper history, day one) it becomes visible at the next scoring, **within ~30 minutes** — not the next morning. that is the gate on "when does the user see it".

## full example: one gem, found to seen

fake app "SleepySheep" (sleep-sounds subscription app). all times tuesday, UTC.

- **09:00** — worker wakes, cycle starts. sweep begins walking the 301 keywords, 3 workers in parallel.
- **09:21** — keyword "sleep tracker app", page 4 (one of the 3 workers owns it). ad #17: page "SleepySheep", ad links to the App Store. candidate row written: found by "sleep tracker app", depth 4.
- **09:30** — sweep ends (3-wide, so faster than the old single-file walk). triage runs on this cycle's ~40 new candidates. apple batch lookup: SleepySheep has **1,850 ratings** → under 5,000 → **KEEP**. advertiser row created. still zero ads stored.
- **09:38** — keeper history: pull SleepySheep's whole page by page id → **34 ads**. oldest still-running ad started 45 days ago. rebuild 16 monday-aligned weeks from the start dates → **7 weeks of non-zero history, on day one**. ad rows + weekly series written.
- **09:40** — the enqueue step: SleepySheep made the ~21-gem taste cut (its store listing looks like stuff the user liked) → dropped on the enrich queue. worker cycle moves on to repairs.
- **09:43** — the pipeline worker (always on) claims it off the queue: full pull with inactive ads, media for top 5 ads → cloud storage, store snapshot, one ad points to `quiz.sleepysheep.com` → rendered, screenshotted, classified: subscription quiz funnel. done in ~3 minutes, all before the worker cycle even finished.
- **10:00** — the 30-min scoring pass runs. SleepySheep: 7 weeks observed (≥2, passes), 34 active ads (≤200), 1,850 ratings (≤5k), not a game, app verified, not a relaunch → **no veto**. winner ad age 45d → capped at 35 → strong velocity axis; ratings clearly accumulating → strong demand axis. **GemScore 64** → flagged as alert (≥60). score row written, feed view refreshed.
- **08:40 → actually ~10:01** — user opens app.peon.works, Discover feed. SleepySheep is there: GemScore, real ad creatives, growth series, quiz-funnel link. review queue sees it too (same view underneath).

**found 09:21 → visible ~10:01. under an hour**, most of it "wait for the next 30-min scoring". worst case (found right after a scoring pass, enrich a bit slow) ≈ 1.5 hours. this is the payoff of the refactor: the old design did enrich as a once-a-cycle batch and (as the docs used to describe) scoring only ran nightly, so found-to-visible was ~17 hours — now it's ~1.

the one slow path: if ALL of SleepySheep's ads had started only 4 days ago, the reconstructed series would show 1 week → vetoed as "insufficient persistence" → invisible. grug keeps refreshing it daily anyway, and the first scoring pass after its history crosses 2 weeks it scores and appears by itself. this veto is why the feed doesn't fill with day-old ad tests.

and the loop closes: if the user LIKES SleepySheep, its ad copy gets mined for phrases ("fall asleep faster", "sleep sounds") → new keywords → tomorrow's sweeps search for its cousins. what user likes literally steers what grug hunts next.

## how the keyword pool grows

the 301 static keywords never change. growth happens in a second table, `discovery.dynamic_keywords`, fed by two pipes. both pipes end the same way: new row, status **trial**.

**pipe 1 — flywheel mining (every cycle).** grug takes the ad copy of scored gems (gem score ≥ 40) and looks for phrases that repeat across **3 or more different advertisers**. one app saying "fall asleep faster" = slogan. five apps saying it = a real marketing angle worth searching. then grug throws away:

- phrases already in the static list or already tried before
- "token supersets" — an existing keyword plus a noise word ("best sleep tracker app 2026" when "sleep tracker app" already exists). those just re-find the same ads.
- anything too similar to a **dead basin**: every retired/dried-up keyword has an embedding, and a new candidate that lands ≥ 0.90 cosine to one gets rejected — that ground is already hunted out. (this gate fails OPEN: if embeddings break, candidates pass. gate must never starve discovery.)

survivors become trials. the mining pipe only fills up to **40 trials of its own at a time** — it mines into the room left, no more. (frame mint's daily slots land on top of that cap, so the live trial pool is usually bigger than 40.)

**pipe 2 — frame mint (once a day, taste-blind on purpose).** the flywheel only finds cousins of what grug already keeps — left alone it converges into a bubble. frame mint pushes outward from an external frame: the app-store category taxonomy. **15 slots a day**, split across categories by yield — **75% to categories that recently produced keepers, 25% to under-covered ones** — so fertile ground gets most of the budget but deserts still get a probe (not more than a probe: a category can be "under-covered" because emerging apps there simply don't advertise on meta). two lanes fill the slots:

- **autocomplete**: take a category's seed terms, run them through the stores' own search-autocomplete, then have Gemini generalize the raw app/brand names that come back into generic niche queries ("capcut: photo & video editor" → "quick video editor app")
- **brainstorm**: Gemini asked directly for head phrases in the category's niches

same subset-dedup and dead-basin novelty gate as pipe 1, then into the trial pool.

**crowding penalty (shipped 2026-07-10).** the flywheel used to promote a trial keyword on raw candidate count — which rewards saturated niches (there's always another calorie tracker to find), the opposite of the "find interesting apps" goal. now every kept app carries a **crowding** score (mean similarity to its 10 nearest neighbours in the corpus; ≥0.80 = sitting in a clone cluster, measured to like-rate 2.8% vs 8-12% elsewhere). a trial only promotes on its **uncrowded** yield, the enrich queue and the review deck both demote crowded apps, and a one-off audit rests existing crowded trawling-net keywords. (known gap: a rested keyword can still wake and re-activate on raw yield — durable lifecycle gating is a follow-up.)

**then the lifecycle takes over.** every cycle the flywheel sweeps all live dynamic keywords:

- a **trial** that yields keepers → promoted to **active**, now swept like any other keyword
- a trial that stays dry through its allowance → **retired** (and its embedding becomes a dead basin the novelty gate checks against — retirement teaches the minter what not to mint)
- an **active** that goes dry → **rests** with an exponentially growing cooldown, then wakes and gets retried. nothing is deleted; dry keywords sleep, they don't die.

so the pool only ever gains rows. what the user likes → mined into keywords (pipe 1). what categories yield → more mint slots (pipe 2). what dries up → becomes a lesson the gate enforces. that is the whole self-improvement loop.

**live numbers (2026-07-10, prod DB):** the dynamic pool went 140 → 1,899 keywords in five weeks (+473 this week). the graduation funnel is brutal on purpose — most mints die in trial, and that's the gate doing its job, not a bug. hit rate ("ever found a candidate") by source: funnel-copy mining 64%, gem-copy mining 39%, autocomplete 18%, brainstorm 5%, new-release 1% — which is exactly why the new-release lane was killed on 07-09.

## side lane: funnels

some of the best finds are not app ads — they are **web quiz funnels** (localized quiz → subscription checkout), often before an app exists. web-funnel rejects from the app sweep plus the 28 dedicated funnel phrases feed this lane; daily 8-page sweeps, weekly 100-page deep dig (funnels hide deep in impressions-ranked results). each candidate's landing page is actually rendered and confirmed (quiz steps counted, payment/pixel vendors fingerprinted, payoff classified — only digital kept). app-backed funnels bridge back into the app pipeline; pure web ones show in the feed with a "Web only" badge.

## where code live

- worker loop, stage order, budgets — `src/agf/workers/main.py` (`run_once`)
- **parallel sweep** (3 workers, shared circuit-breaker) — `src/agf/discovery/sweep.py` (`run_parallel_sweep`, `SweepBreaker`), extractors built in `main.py` (`build_extractors`)
- keywords: taxonomy / lifecycle / mining / minting — `src/agf/discovery/taxonomy.py`, `db/dynamic_keywords.py`, `keyword_mining.py`, `frame_mint.py`, novelty gate in `keyword_redundancy.py`
- meta GraphQL client + proxies — `src/agf/extract/search.py`, `session_pool.py`, `proxy.py`, `tls_client.py`
- triage + store lookups — `src/agf/discovery/triage.py`, `enrich_full/appstore.py`, `enrich_full/sidecar/gp_lookup.js`
- keeper history (16-week reconstruction) — `src/agf/discovery/keeper_history.py`
- **enrich queue** — table `db/schema/152_pipeline_jobs.sql`, helpers `src/agf/db/pipeline_jobs.py` (enqueue / claim SKIP-LOCKED / lease-reaper), consumer `src/agf/enrich_full/pipeline_daemon.py`; the enrich work itself — `src/agf/enrich_full/run_enrich.py`, `enrich_one.py`, `funnel_capture.py`
- **compute cron pass** (embeddings + preference + weekly counts) — `src/agf/workers/compute_cycle.py`
- corpus refresh — `src/agf/discovery/corpus_refresh.py`
- funnel sweep/confirm — `src/agf/discovery/funnel_sweep.py`, `funnel_confirm.py`
- gemscore + vetoes — `src/agf/scoring/gemscore.py`, `vetoes.py`, `run_scoring.py`
- feed view — `db/schema/141_emerging_matview_websub.sql` → `frontend.emerging_scaling_apps`
- cron schedule — `ops/prod/crontab` (compute every 15 min, scoring every 30 min, matview refresh hourly at :00)
- runtime — `ops/prod/docker-compose.yml` on the VM: `worker` (discovery loop) + `pipeline` (enrich consumer) + `cron` + `enrich` (on-demand Refresh-data queue); Go API on GKE
- **on-demand stage runs** — `python -m agf.workers.run_stage <stage>` inside the worker container runs any single stage right now, gates bypassed, through the exact code the loop uses. Test mode by default (writes no run row, so tonight's gates are untouched); `--record` writes the real run row. Scope it down for cheap checks: `run_stage flywheel --keywords "sleep tracker app"`, `run_stage corpus --budget 50`. Stages: sweep, flywheel, funnel, corpus, generic-deep, mint, triage, compute, score.

knobs grug touches most: `AGF_SWEEP_WORKERS=3` (parallel sweep width — drop to 2 if meta challenges), `AGF_SWEEP_INTERVAL_SEC=3600` (sleep between cycles), `AGF_SWEEP_PATIENCE_PAGES=3` / `AGF_SWEEP_MAX_DEPTH=15` (dig depth), `AGF_DISCOVERY_ENRICH_BUDGET=600` (per-cycle meta requests, half keeper-history half the enqueue budget), `AGF_CORPUS_REFRESH_BUDGET=3000` (daily re-scrape), `AGF_FUNNEL_DEEP_PAGES=100` (weekly deep dig), `AGF_ENRICH_ONE_TIMEOUT_SEC=180` (per-gem enrich ceiling in the pipeline worker), `AGF_PIPELINE_POLL_SEC=5` (how often the pipeline worker checks the queue), `AGF_PROXIES` (DataImpulse).
