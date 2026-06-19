# Funnel sharpening — make `has_funnel` mean a REAL web2app funnel

Written 2026-06-19. Small, targeted data-quality fix (not a subsystem — plan folded
into execution). A non-bias Spec 2b item: tightens a signal that feeds the preference
model + GemScore. All work on `main`.

## Problem (measured)
`funnel_url` in the feed (97 of 688 apps) is already ~92% real web2app landings, but
~8 leak through as MMP/store-bounce redirects, not real funnels:
`appstack.link/deeplink/resolve?...`, `*.onelink.me` (AppsFlyer), `lnk.to`, plus a few
non-app pages (luma/kickstarter/fanaticscollect). These resolve to a `web` final_url
carrying `pid=`/`af_ad`/a deep path, so `_looks_like_funnel` (run_enrich.py) records them
and the matview's exclusion regex (migration 129) — which lists store+social but NOT MMP
hosts — passes them. MMP detection is also scattered (firehose_fingerprint, funnel_repair,
deeplink, funnel_classify all carry partial lists).

## Decision (user-approved): the small targeted fix
1. **One authoritative store-bounce host predicate** in the shared pure module
   `src/agf/discovery/funnel_classify.py`: `STORE_BOUNCE_HOSTS` (frozenset of MMP /
   attribution / store-redirect host suffixes) + `is_store_bounce(host) -> bool`
   (suffix match: `host == h or host.endswith("." + h)`). Hosts: `appstack.link`,
   `onelink.me`, `onelink.to`, `app.link`, `go.link`, `adj.st`, `bnc.lt`, `adjust.com`,
   `appsflyer.com`, `branch.io`, `kochava.com`, `singular.net`, `lnk.to`, `apple.co`.
2. **Write gate**: `record_web_funnel` (`run_enrich.py:156`) — add
   `or is_store_bounce(funnel_host)` to the existing social/store exclusion, so future
   re-enrich never stores a bounce as a funnel.
3. **Read gate / matview migration 138**: rebuild `frontend.emerging_scaling_apps`
   with the `fn.funnel_url` exclusion regex extended to also reject the MMP hosts (same
   list). This is the authoritative gate for `has_funnel`/`funnel_url`; non-destructive.
4. **Backfill** `scripts/cleanup_bounce_funnels.py`: `DELETE FROM public.funnels` whose
   resolved host is a store-bounce (these are mis-classified rows; the write gate keeps
   them out on re-enrich), then `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

Out of scope: the non-app pages (luma/kickstarter/fanaticscollect) — fuzzier "real page
but not an app funnel" problem, whack-a-mole, low value; left as-is. Not touching the
discovery-side `FUNNEL_HOST_HINTS` (leaving funnel candidate discovery unchanged — the
write+read gates make the mis-listing harmless).

## Units / files
- `src/agf/discovery/funnel_classify.py` — `STORE_BOUNCE_HOSTS` + `is_store_bounce`.
- `src/agf/enrich_full/run_enrich.py` — import + write-gate line.
- `db/schema/138_emerging_matview_funnel_mmp_gated.sql` — matview rebuild, regex + MMP hosts.
- `scripts/cleanup_bounce_funnels.py` — backfill DELETE + refresh.

## Testing
- Pure unit (`tests/discovery/`): `is_store_bounce` True for `appstack.link`,
  `aicalc….onelink.me`, `lnk.to`, `go.link`; False for `foodhealth.co`, `web2wave.com`,
  `meetaugust.ai`, `""`.
- `record_web_funnel` returns 0 for a `web` target whose final_url host is a bounce
  (unit with a fake ResolvedTarget + deps; mirrors existing run_enrich tests).
- Full suite green (`AGF_TEST_DB_URL=… pytest -q`), 0 failed.
- Live: apply migration 138, run the backfill, confirm `funnel_url` count drops by ~8 and
  no remaining `funnel_url` matches the MMP host list.

## Error handling / risk
- Non-destructive read gate (matview) is the user-visible fix; the backfill DELETE is the
  explicit cleanup (safe — re-enrich won't re-add, gated). Suffix match avoids
  false-positives on lookalike domains (`myonelink.me` would need to end with `.onelink.me`).
- Low blast radius: one pure predicate + one write-gate line + one matview migration.
