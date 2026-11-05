# How The App Finder Work (Grug Brain Version)

grug write this so grug remember how thing work. no big word. if grug future read this and confused, grug past fail. let go.

---

## Meta ad research through MCP

The hosted `/mcp` endpoint exposes `find_advertisers`, `get_advertiser_ads`,
`search_meta_ads`, `ad_scrape_status`, `next_ad_page`, and `read_ad_copy`.
Claude Code or Codex reads `ad_research_guide` first, then controls the research
and judges the raw ad evidence. It can also call optional `find_similar_brands`
(Gemini leads) and `judge_ads` (Jev assessments). Direct scraping runs without
model calls; the agent chooses which assistance to use. Live scraping
currently uses the existing admin access boundary.

The [agent guide](backend/mcpserver/ad_research_guide.md) is embedded in the MCP
server, so clients receive the same instructions as this repository. See the
[implementation and verification record](docs/2026-11-05-ad-relevance-plan.md)
for deployment requirements and measured behavior.


## what grug build

grug want find small app that getting big by paying for facebook ads. NOT big famous app everybody know. grug want find them EARLY, when still small, before other grug notice.

think: somebody just made new app. they pour money into facebook ads. app start to blow up. grug want be the one who see it first, and copy what work: their product, their sales page (funnel), their ads.

so grug build robot that watch for these apps all day. robot make list. grug look at list. grug get rich idea. good.

---

## the problem: facebook hide the good rock

grug want know "which app spend most money on facebook ads right now." simple question. but facebook NOT tell grug.

facebook have thing call **Ad Library**. it show all the ads. grug think "great, grug just ask it!" but facebook is sneaky. two big trap:

1. facebook hide the money number for America ads. grug can SEE the ad. grug can NOT see how much money they spend. (only Europe ads show numbers, because Europe make law.)
2. facebook give two different door, and one door is fake-helpful. grug fall in trap first time.

grug now explain the two door because this is most important thing.

---

## two thing both call "Ad Library" (grug confuse, then grug understand)

**door 1: the official API.** this is the "proper" door facebook tell developer to use. grug think this is good door. grug WRONG. this door only show political ads (election stuff) for America. for normal app ads, it only work for Europe. so for America app, this door show NOTHING. trap door. grug no use.

**door 2: the website.** this is the normal facebook ad library website any human can open in browser. it show EVERY ad, America too, no login. THIS is the door grug use. grug make robot pretend to be human looking at website, and steal the data the website loads.

lesson grug tattoo on club: **do not use the proper API. use the website.** everybody who do this (the ad-spy companies like Foreplay) use the website too. grug not crazy, grug normal.

---

## America ads have no money number, so grug use clue instead

grug can not see spend. sad. but grug is clever caveman. grug use CLUES that mean "this ad working":

- **how long ad been running.** facebook show start date. if ad been running 14+ days, that mean it WORK. why? because nobody pay to keep bad ad alive. you stop bad ad fast. old ad = good ad = they making money.
- **how many ads they run.** lots of ads = lots of money = scaling.
- **how many copies of the SAME winning ad.** when they find one ad that work, they make 10 copies and pour money in. grug see the copies, grug know "they found winner."

these clue not perfect. but every spy tool use same clue, because nobody have the real money number. grug in good company.

---

## how grug find new app nobody know about (the three nets)

this the HARD part. grug think hard here.

if grug just search "workout app" in the library, grug only find app grug already know to search for. grug want find app grug NEVER HEARD OF. how grug search for thing grug not know exist? grug can not.

so grug throw THREE big nets at once. each net catch different fish. grug not rely on one net.

**net A — the new-app firehose.** apple have secret list of brand-new apps, updated every day, free. grug check it every morning. brand new app = small by definition = maybe a gem. grug also use the MWM list (mwm.ai, 266 thousand apps with their birthday date) to grab new ones. this net catch baby apps the day they born.

**net B — the Europe ranking trick (grug feel smart here).** remember Europe show the money/reach numbers? so on the EUROPE library, grug can sort ALL ads by "most reach, biggest first" with NO search word. this is firehose of whoever spend most right now. the famous app AND the unknown app show up on the SAME list, ranked by spend. so a no-name app burning money float right to the top next to Nike. grug not need to know its name. the spending sort it for grug.

**net C — the fingerprint net.** apps that sell subscriptions all use same few tools to take your money (RevenueCat, Superwall, Adapty) and same facebook tracking pixel. grug search the internet's source code for these tool fingerprints. find a web page that has a subscription tool AND a facebook pixel = a page built to buy facebook traffic. mostly tiny unknown ones. this net catch the WEB funnels, not just phone apps.

three nets. all dump fish into one bucket. grug now have big list of "maybe gem."

---

## the Europe → America trick (grug bridge)

problem: net B is Europe. but grug want America data.

trick: when grug find an app on the Europe list, grug grab its page id. then grug go BACK to the library and ask "show me THIS page's America ads." now grug have its full America ads. 

why this work: app that spend big in America almost always also run SOME ads in Europe. so Europe is grug's radar. America is where grug get the detail. clever.

(honest: app that ONLY run in America, never Europe, grug miss on net B. that why grug also have net A and net C. three nets cover the holes.)

---

## the BIG check: is app REALLY spending on facebook?

careful! a small app can get popular from TikTok videos for FREE, zero facebook money. that not what grug want. grug want apps SPENDING on facebook.

so before grug believe any app is a gem, grug check the library: does this app have ACTIVE ads that been running 14+ days? 

- yes → real, they paying, keep it.
- no → maybe just TikTok lucky, throw away.

grug say this twice because grug always forget: **finding the app and proving it spends on facebook are TWO separate steps.** never skip the second step.

---

## GemScore: the magic that throw away the famous apps

now grug have list of apps that spend on facebook. but some are famous (grug no want famous). grug need a score that gives famous apps a ZERO.

grug measure THREE things for each app:

1. **how fast it scaling** (more ads, longer running, growing fast) — call it SV
2. **how unknown it still is** (few ratings, nobody google its name, not in top charts) — call it UR
3. **does it have a real money-making funnel** (subscription tool + pixel) — call it FI

now the clever part. grug NOT add them. grug MULTIPLY them:

```
score = SV × UR × FI
```

why multiply matter so much? because if app is famous, its "unknown" number (UR) is near ZERO. and anything times zero is ZERO. so famous app gets score zero NO MATTER how much it spend. the famous apps delete themselves. only fast-AND-unknown apps survive. 

if grug had ADDED the numbers, a famous app's huge spending could hide that everyone knows it. multiply not let that happen. this is the one trick that make grug tool different from every boring "trending apps" list. grug proud.

(small warning: multiply also mean if grug MISSING one number, score become zero by accident and grug throw away good app. grug must put small floor so missing-number not kill a real gem. grug write this down so grug not forget.)

---

## grug care how FAST, not how BIG

big apps are big because they already won. boring. grug want apps that are SMALL but moving FAST. so grug never look at "how big." grug look at "how fast growing" (speed) and even "is the growing speeding UP" (acceleration).

big famous app sit still, speed near zero, grug ignore. small app shooting up, speed high, grug excited. grug measure the change week to week, not the size.

(this why grug need to watch each app for 3-4 weeks before grug trust the speed. one good week could be luck.)

---

## after grug find a gem: grug grab everything

once an app pass all the checks and score high, grug send it to a human (grug self) to look. grug never let robot decide alone — real gems are rare, robot make mistakes, human must check.

for each gem grug grab the full story:
- **product:** the app store page (when born, how many ratings, what category)
- **funnel:** grug robot click through their whole sales quiz and screenshot every step + the price
- **ads:** grug download all their actual ad pictures and videos (download FAST, the links expire)
- **company:** who made it, how old the website, did they raise money

now grug have full dossier. grug learn. grug copy what work. good.

---

## how the machine built (boring part, grug keep short)

- one computer. one database (Postgres). the database is ALSO grug's to-do list — workers ask database "what next?" no fancy queue stuff. grug not need it yet.
- small worker programs that each do one job: fetch ads, classify, enrich, score.
- a robot Chrome browser (Playwright) for the tricky scraping and for clicking funnels.
- pictures and videos go in cheap file storage (S3/MinIO), database only keep the address, not the picture.
- grug NOT add Redis, Kafka, Celery, all the big-brain tools. grug add them ONLY if grug computer cry from too much work. probably never.

grug copy a real working project (`ddxv/adscrawler`) for the database shape and a real scraper (`ChrisFeldmeier/fb_ad_scraper`) for the facebook part. grug not invent from scratch. smart grug stand on other grug shoulder.

---

## what can go wrong (grug worry list)

- **facebook no like scraping.** it is against their rules. grug doing it private to learn, that mostly ok, but grug not allowed to re-sell their ad pictures or train AI on them. business risk, grug know it.
- **the secret id changes.** facebook change a secret number (`doc_id`) about every week. when it change, grug robot break and find zero ads. grug must re-grab the new number. annoying but expected. grug build robot to grab it fresh each time.
- **facebook block grug for scraping too fast.** grug need to rent many internet addresses (proxies) so facebook not notice. this cost money every month. this the real ongoing cost.
- **the score numbers are guesses.** grug picked the weights from gut. grug must test them against apps grug KNOW blew up, to see if grug score would have caught them. until then, grug not trust the score too much.

---

## how grug build it: small first, then big

grug NOT build whole thing at once. grug build a tiny version first to prove it even work.

**first (the PoC):** one small script. search one word, find apps, check if new, check if running ads, print a list. takes one day. if this work, grug know the whole idea is real. if it not work, grug find out cheap, not after months.

**then:** grug build the real durable version piece by piece — proper scraper, database, the three nets, the full score, the dossier grabber, a nice screen to look at results. seven plans total. each plan only start after the one before it work.

---

## grug final word

the whole thing in one breath: **throw three nets to catch unknown apps → prove they really pay facebook (14-day-old ads) → multiply three scores so famous apps become zero → human look at what survive → grab their product, funnel, ads.**

the magic trick is the multiply (famous = zero). the hard part is the finding (three nets). everything else is plumbing. 

grug done. grug go look at list now. complexity demon stay away.

---

## PoC Quickstart (Plan 1)

The Plan-1 PoC proves the full chain: US Ad Library search → app-classify → emerging enrich → scaling proxy → ranked CSV. No DB, no proxies, no browser.

### Setup
    python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt

### Run the unit tests
    .venv/bin/pytest -v

### Run the live smoke test (hits Facebook + Apple)
    .venv/bin/pytest -m integration -v

### Run the PoC end-to-end
    .venv/bin/python -m agf.run_poc "workout app" "calorie tracker"
    # → writes gems.csv ranked by PoC GemScore

### Maintenance
The Ad Library `doc_id` rotates (~weekly). If a live search returns 0 ads,
capture the current `AdLibrarySearchPaginationQuery` doc_id from browser
DevTools (Network tab) and update `_FALLBACK_DOC_ID` in `src/agf/livesearch.py`.
This fragility is why Plan 2 replaces this with Playwright GraphQL interception.

### Live validation (2026-06-04)
Verified against the real Meta Ad Library + iTunes:
- **iTunes Lookup** — works via plain `httpx` (live test passes).
- **GraphQL search** — our `request.py` body + fallback `doc_id 27201872659451053`
  returns **HTTP 200 with real ads** (confirmed live); `parse → classify → scale → score`
  all handle the real response correctly (10 ads parsed, iOS/web classified, advertisers
  aggregated, GemScore computed). The fallback `doc_id` was current on this date.
- **Bootstrap GET is anti-bot-blocked** — `GET /ads/library/` over plain `httpx` returns
  HTTP 403 / a JS challenge stub (no `LSD` token, no IP fix), so `run_poc` cannot complete
  via httpx alone. A **real browser** passes the challenge (it sets the `datr` cookie and
  executes the JS), after which the exact same request chain works. **This is the load-bearing
  reason Plan 2 uses a Playwright browser session** rather than raw HTTP. The `test_bootstrap_and_search_returns_ads`
  smoke test is marked `xfail` to reflect this known external block (not a code defect).

### Scope
PoC only: no DB, no proxies, no browser, no funnel/creative download.
How the live system works end-to-end: `docs/scraping-overview.md`.
