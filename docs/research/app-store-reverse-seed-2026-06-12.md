# App-Store -> Funnel Reverse Seed: source inventory (2026-06-12)

Goal: seed list of top-grossing Health/Fitness + Lifestyle subscription apps
(apps likely to run web2app funnels) -> then find each one's web funnel + Meta ads.

## Seed sources ranked by automatability

### 1. Apple legacy iTunes RSS top-grossing (BEST — free RSS, named, genre-scoped) [VERIFIED LIVE]
- URL pattern (confirmed returning 200 + named JSON on 2026-06-12):
  `https://itunes.apple.com/us/rss/topgrossingapplications/limit=<N>/genre=<GID>/json`
- Genre IDs: Health & Fitness = **6013**, Lifestyle = **6012**. (topfreeapplications also works.)
- Returns JSON `feed.entry[]` with `im:name`, `im:artist`, `category.label`, app id, price, icon.
- Max returned per call ~**100** (limit=200 truncates to 100). Storefront swappable (`/us/` -> `/gb/` etc).
- HF top-grossing top 25 (2026-06-12) included real funnel apps: MyFitnessPal, LADDER, Flo, Calm,
  Peloton, **Cal AI**, Runna, MacroFactor, Headspace, Lose It!, BetterSleep, Fitbod, WeightWatchers.
- Note: the modern `rss.applemarketingtools.com/api/v2/...` generator does NOT support top-grossing
  or genre in the path (404) — only top-free/top-paid ungenre'd. The LEGACY itunes.apple.com path is
  the one that still does genre + top-grossing. Use it.
- Verdict: PRIMARY automatable seed. Daily cron, 2 genres x storefronts, dedupe by app id.

### 2. AppMagic (free top charts incl. Top Grossing + Top Publishers) [scrapable HTML]
- Top apps: https://appmagic.rocks/top-charts/apps/  (Top Free / Top Grossing / Top Featured tabs)
- Top publishers: https://appmagic.rocks/top-charts/publishers/
- Free tier NAMES apps + publishers + rough download buckets (">5,000,000" style). Genre filter exists.
- Now owned by Sensor Tower (acquired AppMagic). Top Trending chart was replaced by Period Comparison.
- Verdict: good secondary; HTML scrape (Firecrawl). Free but lighter detail than paywalled API.

### 3. Similarweb top-apps (free public ranked lists, NAMES apps) [scrapable HTML]
- Google Play top-grossing H&F: https://www.similarweb.com/top-apps/google/health-fitness/top-grossing/
- Also iOS + free/paid variants by category. Daily-dated ranked tables with publisher names.
- Verdict: best free Google Play top-grossing source (Play has no official top-grossing RSS).

### 4. Google Play store category pages [scrapable but JS-heavy]
- https://play.google.com/store/apps/category/HEALTH_AND_FITNESS — has Top grossing / Top free / Top paid.
- No official RSS. Render-heavy; use `google-play-scraper` (npm/py) lib instead of raw scrape.
- Verdict: usable but more brittle than Apple RSS / Similarweb.

### 5. RevenueCat "State of Subscription Apps" [report, benchmarks-not-a-namelist]
- https://www.revenuecat.com/state-of-subscription-apps/ (2026 ed. live; 2025 archived)
- World's largest sub-app dataset BUT it publishes aggregate benchmarks/medians, generally does
  NOT publish a ranked named-app leaderboard. Good for context, weak as a seed namelist.

### 6. Sensor Tower / data.ai (App Annie) [mostly paywalled]
- Sensor Tower free top-charts: https://app.sensortower.com/top-charts (login-gated; category ids
  e.g. ?category=6013&country=US&os=ios). State-of-Mobile report = PDF, names some top apps but not
  structured/automatable.
- data.ai = acquired by Sensor Tower (data.ai login page says "We've Been Acquired"); App Annie brand
  dead. No free structured list survives.
- Verdict: paywalled API / image-only report. Low automatability. Skip for v1.

## Starter named-app seed list (documented web2app subscription funnels, H&F + Lifestyle)
Sources: Heyflow weight-loss funnel teardown (Noom, BetterMe, SIMPLE, V Shred, Juniper),
Paddle/Adapty/PaywallPro paywall studies, Apple HF top-grossing live feed, brand sites.

Weight loss / calorie / nutrition: Noom, BetterMe, SIMPLE (Simple.Life), Cal AI, Yazio, Fastic,
Lose It!, MyFitnessPal, MacroFactor, WeightWatchers, Juniper, V Shred, Lasta, Fasting/Window, Cronometer.
Fasting: Fastic, Simple, Zero, BodyFast, DoFasting.
Women's health / cycle: Flo, Clue, Stardust.
Sleep / mental wellness / meditation: Calm, Headspace, BetterSleep, Insight Timer, Rise (RISE Sleep), Finch.
Fitness / workout: Sweat, Peloton, Fitbod, Runna, Ladder, Gymshark/Centr, Future.
Skincare/face/lifestyle: FaceApp, Plantum / PictureThis (plant id), Cleo/Stellar (astrology-lifestyle).
Wearable-adjacent: Oura (web checkout), Whoop.

These are immediate seeds: search each in Meta Ad Library + look for a web quiz/checkout domain.

## Confirmed Apple RSS feed URL (explicit)
`https://itunes.apple.com/us/rss/topgrossingapplications/limit=100/genre=6013/json`  (Health & Fitness)
`https://itunes.apple.com/us/rss/topgrossingapplications/limit=100/genre=6012/json`  (Lifestyle)
