# Free / low-cost ways to scrape Meta Ad Library (no paid residential proxies)

**Date:** 2026-06-07 · **Question:** can we run discovery sustainably for ~$0, instead of buying residential proxies?

## The reframe that matters

The hard part of scraping Meta is **anti-bot fingerprinting** (TLS/HTTP2 handshake, the
`rd_challenge`, `doc_id` rotation) — and **this project already solves it** (`curl_cffi`
impersonate=chrome + `rd_challenge` re-heal + `token_heal`). A residential **proxy adds only
one thing: IP rotation for volume.** Our crawl is light (~70 KB/request, well under 1 GB/month,
a few hundred requests/day at a once-daily cadence), so the volume that *requires* rotation is
low. That means a $0 path is viable to start; paid proxies become an optimization for scale/
reliability, not a prerequisite.

## Official Meta Ad Library API — ruled out (founder + research agree)

Founder tried it: "you don't get the info from it." Research confirms the why: for EU commercial
ads it returns **longevity (`ad_delivery_start_time/stop_time`) + a single `eu_total_reach` int**
but **no spend/impressions** (those are political-ads-only) and **no sort/reach-ranking** — so it
cannot do the keyword-free "find the biggest EU advertisers" discovery firehose, and covers **zero
US commercial ads**. Free + proxy-free, but not the engine. (Could, in theory, serve the per-page
14-day confirm for free — parked unless we want it later.)

## The $0 options (ranked)

| Option | Cost | Handles anti-bot? | Best for | Catch |
|---|---|---|---|---|
| **Home residential IP, once-daily, polite** | $0 | yes (our existing stack) | starting now, uses built code | single IP → soft-blocks/login-walls at volume (not a clean ban); no rotation |
| **Apify free tier** (prebuilt FB Ad Library actors) | **$5/mo recurring, no card, permanent** | yes (managed) | zero-maintenance, always-works | modest volume on $5; relies on a community actor; needs a small integration |
| **Oracle Cloud Always Free** (4 OCPU / 24 GB ARM) | $0 forever | n/a (hosting) | always-on host for the whole stack | datacenter IP → **can't** scrape Meta directly; pair with Apify or a home tunnel |
| Tor / free cloud-VM IPs / free proxy lists | $0 | no | — | Meta blocklists Tor exits + datacenter ASNs; public lists are dead/malware |
| Mobile 4G tether | ~$0 | partial | a manual backup IP | single IP, no programmatic rotation |
| *Cheap-robust baseline* (when scaling) | ~$4/mo Hetzner + ~$14 IPRoyal 2 GB (lasts months) | yes | reliable always-on at volume | not $0 |

### Fact-check verdicts (adversarial)
- **Apify free tier works for Meta:** SUPPORTED — $5/mo prepaid credit resets each cycle, no card; prebuilt Ad Library actors return structured ad JSON. Catch: $5 is modest throughput; it's a community actor.
- **Single home IP sustainable at low volume:** PARTLY-SUPPORTED — once-daily is the right cadence, but there's no published threshold and a naked single IP tends to get **degraded/login-walled** rather than cleanly blocked. Our `curl_cffi`+`rd_challenge` stack mitigates the fingerprint half; the IP half is the residual risk.
- **Official API = free EU commercial data:** PARTLY-SUPPORTED — longevity + `eu_total_reach` yes; spend/impressions/sort no. Not a discovery engine.

## Recommendation — a genuinely free setup

1. **Start now, $0:** run the **existing scraper from home, once-daily, polite** (jittered delays,
   backoff, conservative `AGF_*` caps). No new services. Good enough to keep accumulating velocity
   data and validate. We already proved one live sweep works from this IP.
2. **Managed fallback, $0:** add **Apify's free tier** ($5/mo recurring) as an alternate discovery
   source — if the home IP gets flaky, Apify's actor scrapes Meta for you with no IP/anti-bot work.
   Small integration (call the actor, map results into `Candidate`).
3. **Always-on, $0:** when you want it running 24/7 independent of your home machine, host the stack
   on **Oracle Cloud Always Free** — but route Meta calls via **Apify** (token/managed, datacenter-IP-
   safe) *or* a tunnel back to the home IP, since Oracle's own IP is datacenter and Meta-blockable.
4. **Upgrade only if/when needed:** if free volume/reliability isn't enough, the cheap-robust path is
   Hetzner (~$4/mo) + a one-time IPRoyal 2 GB (~$14, lasts months) — not $0, but small.

**Net:** the cheapest sustainable combo is **Oracle Always Free (host) + Apify free tier (Meta egress)**,
or even simpler, **the home-IP scraper on a daily cron ($0, no new services)**. Buying proxies is a
scale optimization, not a requirement to begin.

## Sources
- Apify pricing (recurring $5 free, no card): https://apify.com/pricing · FB Ad Library actor: https://apify.com/curious_coder/facebook-ads-library-scraper
- Oracle Cloud Always Free: https://www.oracle.com/cloud/free/
- Scrapfly / Zyte / ScraperAPI free tiers (one-time or weak-on-Meta): https://scrapfly.io/pricing · https://www.zyte.com/zyte-api/ · https://www.scraperapi.com/pricing/
- Official Ad Library API (ruled out): https://developers.facebook.com/docs/graph-api/reference/ads_archive/
