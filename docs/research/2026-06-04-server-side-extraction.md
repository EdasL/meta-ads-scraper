# Server-Side Extraction Research — Do We Need a Real Browser?

**Date:** 2026-06-04 · **Status:** researched + **empirically validated on this machine** · **Feeds:** Plan 2 (Extraction core)

> **TL;DR — No, you do not need a real browser, and you do not need to run it from your PC.**
> The 403 you hit is Facebook's **`rd_challenge`** (a defense shipped ~Jan 14, 2026). It is a *trivial* fetch-then-reload, solvable in pure Python. The reason plain `httpx` failed has **two** parts: (1) it never solved the challenge, and (2) its **TLS/JA3 fingerprint** is detectably non-browser — so it 403s even from a residential IP. Fix: use **`curl_cffi`** (impersonates Chrome's TLS) + a ~10-line `rd_challenge` solver. We **proved this end-to-end today**: browserless, no proxy, real US ads through our `parse.py`.

---

## 1. What the 403 actually is (root cause)

`GET https://www.facebook.com/ads/library/` from a non-browser client returns **HTTP 403 + a ~481-byte stub** whose only logic is:

```js
fetch('/__rd_verify_<token>?challenge=3', {method:'POST'}).finally(() => window.location.reload());
```

The token and `challenge=3` are **pre-baked by the server** — there is nothing to compute. The solve is three HTTP calls on one session:

1. `GET /ads/library/?...` → 403 + stub
2. regex `/__rd_verify_<token>?challenge=3` out of the stub → **empty-body POST** it → response sets `Set-Cookie: rd_challenge=...; Max-Age=86400` (**24 h**, HttpOnly)
3. re-`GET` the original URL with that cookie → **200** with the real page (which contains `LSD`, `fb_dtsg`, and the `doc_id` references)

This was reverse-engineered in **yt-dlp issue #15577** and fixed in **PR #15582** (merged 2026-01-27) — a pure-Python solver, no browser, no JS engine. The gate is the missing **`rd_challenge`** cookie, **not** `datr` (a separate long-lived device id that arrives as an ordinary Set-Cookie and matters for *login* flows, not the public Ad Library).

### The second, quieter blocker: TLS fingerprinting
Even after solving `rd_challenge`, **plain `httpx`/`requests` still get blocked** because their TLS/JA3 + HTTP/2 handshake is recognizably non-browser. This is why our residential-IP `httpx` GET also 403'd. The fix is **`curl_cffi`** (`impersonate="chrome"`) — it sends Chrome's TLS fingerprint with no browser. (`rnet` is an equivalent Rust-based alternative; it's what Zyte uses.)

---

## 2. Empirical validation (this machine, 2026-06-04, no proxy)

Ran `curl_cffi` + the `rd_challenge` solver + our existing `request.py`/`parse.py`:

```
GET #1            -> 403  len=481            # the rd_challenge stub, exactly as documented
rd_challenge path : /__rd_verify_Q_6hBQNE9Z7FfIhrI6-9hWbT9aaHVKDP8...
verify POST      -> 200  rd_challenge cookie set: True
GET #2 (cookied) -> 200  len=822884          # real 822 KB page
LSD harvested    : AdRAQDUWxP...
doc_id harvested : none inline (lives in JS bundle) -> used fallback
GraphQL doc_id=27201872659451053 -> HTTP 200  ads=10
   parse.py: 10 ads, kinds={'web': 4, 'ios': 6}, sample='Freeletics'
```

**Conclusion:** the full chain runs **headless / server-side with a plain HTTP client**. Our PoC's fallback `doc_id 27201872659451053` is still valid; the `doc_id` is *not* inline (it lives in a hashed JS bundle), so the durable scraper must re-harvest it (with a hardcoded fallback). Open-source repos cite current values `25464068859919530` (pagination) and `33730956806489539` (page-1/foundation) — keep these as additional fallbacks.

---

## 3. Recommended architecture: "warm-once + HTTP-reuse" (hybrid)

A documented, named pattern (Zyte calls it *hybrid scraping*). On **one small VPS** via docker-compose:

```
                   ┌─────────────────────────────────────────────┐
                   │ VPS (8 GB / 4 vCPU, ~$30-50/mo, Hetzner/DO)   │
  residential   ┌──┤  WARMER (1 process)                          │
  / ISP proxy ──┤  │   curl_cffi: solve rd_challenge, GET page,   │
  (sticky/sess) │  │   harvest {rd_challenge, LSD, fb_dtsg, doc_id}│
                │  │   refresh every ~20-30 min  ──► Redis bundle  │
                │  ├─────────────────────────────────────────────┤
                │  │  N WORKERS (curl_cffi, lightweight)           │
                └──┤   hydrate Redis bundle → POST                 │
                   │   AdLibrarySearchPaginationQuery (cursor)     │
                   │   → parse → Postgres (the spine, Plan 3)      │
                   │  Browser (Playwright/Camoufox) = STANDBY only │
                   └─────────────────────────────────────────────┘
```

- **Warmer** mints sessions (pure-HTTP `rd_challenge` solve; a real browser is only a *fallback* if the challenge ever becomes a genuine computed one). Writes a small JSON bundle `{rd_challenge, lsd, fb_dtsg, doc_id, fetched_at}` to Redis, refreshed every ~20–30 min.
- **Workers** are cheap `curl_cffi` processes that hydrate a bundle and paginate the GraphQL feed. No browser, low RAM.
- **Re-warm triggers** (wire all of these): `403 + __rd_verify` (challenge expired), `429` (rate limit → back off 5–8 s), GraphQL JSON `errors` code `1357004/1357001` (session) or `1675004` (rate limit), `HTTP 400` (stale `doc_id`/`fb_dtsg` → re-harvest), or empty edges on a known-good query (shadow-throttle).
- **Proxy:** pin **one residential/ISP IP per warm session** for its lifetime; never rotate a warm session's IP. Datacenter ASNs get behaviorally blocked at volume (see §5 caveat).

### Reuse, don't reinvent
**`promisingcoder/MetaAdsCollector`** (PyPI `meta-ads-collector`, MIT) already implements *all* of this: `curl_cffi` Chrome-TLS, automatic `rd_challenge` solve, `MAX_SESSION_AGE=1800` proactive refresh, `ProxyPool` with `max_failures=3`/`cooldown=300s`, dynamic `doc_id` discovery + fallbacks, `for(;;);` strip. **Recommended first step: `pip install meta-ads-collector`, verify it returns ads from the VPS, then either use it directly or lift its session/challenge/proxy modules into our Plan 2.** Other references: yt-dlp PR #15582 (cleanest challenge solver), `rachitm99/closer-ads-cron-service` (Playwright-mint → `requests`-reuse), `Rani-Maman/Adora` (pure-HTTP 6-step flow).

---

## 4. "Off my PC" options, cheapest → most hands-off

| Tier | What | Cost (rough) | Maintenance | When to pick |
|---|---|---|---|---|
| **A. Self-host (recommended)** | VPS + `curl_cffi` + `rd_challenge` solver + residential proxy (the §3 stack) | ~$30-50/mo VPS + **~$10-40/mo** budget residential (Evomi ~$0.49/GB, IPRoyal ~$1.75/GB) for a few-k searches/day, ex-media | We own the doc_id/challenge treadmill (~2-4 wk rotation) | Full control, lowest cost, fits the project's single-VPS plan |
| **B. Managed browser / unblocker** | Bright Data **Web Unlocker** (per-request, forwards the GraphQL POST) or **Scraping Browser** / **Browserbase** (mint tokens in a hosted browser) | Web Unlocker ~$1-3 / 1k req; Scraping Browser ~$9.5-20/GB | Vendor owns anti-bot; we still own doc_id/token logic | Don't want to run proxies/browsers, but want our own pipeline |
| **C. Managed data API** | **ScrapeCreators** or Apify **curious_coder** actor ($0.75 / 1k ads) — clean JSON, they own everything | ~$0.75/1k ads + markup | **Zero** — vendor absorbs Meta's cat-and-mouse | Want results now, no infra, accept per-call cost + vendor dependency |

All three run **nothing on your PC**. A sensible path: **start with C** (Apify/ScrapeCreators) to get data flowing and de-risk, while building **A** as the durable owned pipeline; keep **B** as the fallback if self-hosted proxies degrade.

---

## 5. Proxy guidance (for Tier A)

- **Type:** rotating **residential** (or static **ISP**) with short sticky windows (5–10 min, ~10–30 req/IP). The Ad Library is **unauthenticated/public**, so you do *not* need warmed logged-in accounts (unlike Marketplace).
- **Datacenter:** avoid at volume — major DC ASNs are on Meta's known-infra list and get behaviorally flagged. *(Adversarial-verify verdict: "mixed" — this is directionally true and the prudent default, but it is operator/vendor consensus, not an Ad-Library-specific controlled benchmark; one vendor even claims DC works. Your own 403 was the challenge+TLS, not the IP.)*
- **Vendors (2026):** Evomi (~$0.49-0.99/GB, cheapest credible) → IPRoyal (~$1.75/GB) / Webshare (~$1-2/GB) → Oxylabs/Bright Data as premium fallback.
- **Bandwidth:** GraphQL JSON ≈ 0.5-1 MB/search → a few-k searches/day ≈ **45-90 GB/mo**. Pull bulky creative bytes (fbcdn) **outside** the residential proxy (far less ASN-sensitive) to keep residential GB on the light JSON.

---

## 6. Implication for Plan 2 (proposed change)

The master plan §2 guardrail currently says *"One extraction method: Playwright GraphQL interception."* **Recommend revising** to:

> **Primary method:** `curl_cffi` (Chrome-TLS impersonation) → `rd_challenge` HTTP solver → token/`doc_id` self-heal from page HTML/JS bundle → replay `AdLibrarySearchPaginationQuery` with cursor pagination, through residential/ISP proxies, using the warm-once/HTTP-reuse pool. **Playwright (or Camoufox) is a *standby fallback* warmer only** — used if the `rd_challenge` ever becomes a genuine computed challenge or token harvesting needs a live browser.

This is **lighter, cheaper, and headless** vs. a Playwright-per-fetch design, and it stays squarely within the existing "scrape the web GraphQL, never the official API" north star. Plan 2's task list should be re-scoped accordingly (add `curl_cffi`, `token_heal` from JS bundle, `rd_challenge` solver; demote Playwright to an optional warmer module). The §3.4 `Extractor` facade contract is unaffected — only its internals change.

---

## 7. Risks / maintenance reality (unchanged but clearer)

- **`doc_id` rotation** every ~2–4 weeks → re-harvest each warm cycle; keep fallbacks (`27201872659451053`, `25464068859919530`, `33730956806489539`).
- **`rd_challenge` could evolve** into a real computed challenge → that's when the standby browser warmer earns its keep.
- **Session staleness** → wire the re-warm signal list in §3.
- **ToS:** unchanged — private competitive analysis; no redistribution/reuse-in-own-ads/model-training of scraped creatives.

---

## Sources (selected)
- yt-dlp issue **#15577** + PR **#15582** — the `rd_challenge` mechanism + pure-Python solve (load-bearing, Jan 2026)
- `promisingcoder/MetaAdsCollector` (PyPI `meta-ads-collector`) — full warm+reuse+refresh+proxy reference impl
- Zyte — "Hybrid scraping: the architecture for the modern web" (warm browser → HTTP-reuse, TLS fingerprint)
- Scrapfly — 403 / TLS-fingerprint guide; `lexiforest/curl_cffi`, `0x676e67/rnet`
- `rachitm99/closer-ads-cron-service`, `Rani-Maman/Adora`, `Andriy-Kulak/madstack` — FB-specific HTTP-reuse implementations
- Bright Data Web Unlocker / Scraping Browser, Browserbase — managed options; ScrapeCreators, Apify `curious_coder` ($0.75/1k ads) — managed APIs
- Proxy: Evomi/IPRoyal/Webshare/Oxylabs/Bright Data pricing; SpyderProxy, Scraperly (residential vs datacenter for Meta)
