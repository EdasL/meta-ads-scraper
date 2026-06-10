"""Phase-0 live probes for the Meta Ad Library internal GraphQL.

Verify-before-build: every claim about the GraphQL shape (page_like_count present,
a search-level result count, startDate filtering, relevancy/ASCENDING sort, typeahead)
is a hypothesis until one live request confirms it. This script fires those requests
through the real warm-session + residential-proxy stack and reports YES/NO + raw values.

It rotates to a fresh proxy IP and backs off on Meta's rate-limit (code 1675004), and
shares one response across several probes to keep the request count low.

Run (from repo root):
    AGF_PROXIES="http://user:pass@gw.dataimpulse.com:823" PYTHONPATH=src \
        .venv/bin/python scripts/probe_graphql.py

Side effects: none (read-only GraphQL queries). Writes a JSON summary to
/tmp/agf-probe-<date>.json for reference.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timezone

from agf.extract.facade import Extractor
from agf.extract.search import build_search_variables
from agf.fbjson import parse_fb_json
from agf.request import build_form_body

GRAPHQL = "https://www.facebook.com/api/graphql/"
PACE = 4.0  # seconds between distinct requests

TYPEAHEAD_FRIENDLY = "useAdLibraryTypeaheadSuggestionDataSourceQuery"
TYPEAHEAD_DOC_ID = "9755915494515334"
QUERY = "calorie tracker"

THROTTLE_CODES = {1675004}      # rate limit
SESSION_CODES = {1357004, 1357001}


# --------------------------------------------------------------------------- #
# low-level helpers
# --------------------------------------------------------------------------- #
def raw_post(ws, variables, *, friendly="AdLibrarySearchPaginationQuery", doc_id=None):
    body = build_form_body(lsd=ws.tokens.lsd, variables=variables,
                           doc_id=doc_id or ws.tokens.doc_id)
    body["fb_api_req_friendly_name"] = friendly
    resp = ws.session.post(GRAPHQL, data=body, headers={
        "x-fb-lsd": ws.tokens.lsd,
        "x-fb-friendly-name": friendly,
        "content-type": "application/x-www-form-urlencoded",
    })
    try:
        parsed = parse_fb_json(resp.text)
    except Exception as exc:  # noqa: BLE001
        parsed = {"_parse_error": str(exc), "_raw_head": resp.text[:400]}
    return resp.status_code, parsed


def errors_of(parsed):
    return parsed.get("errors") if isinstance(parsed, dict) else None


def post_resilient(ext, variables, *, friendly="AdLibrarySearchPaginationQuery",
                   doc_id=None, tag="", max_tries=6):
    """POST through a warm session; on rate-limit/session error rotate IP + back off."""
    status, parsed = None, {}
    for t in range(max_tries):
        ws = ext._pool.acquire()
        status, parsed = raw_post(ws, variables, friendly=friendly,
                                  doc_id=doc_id or ws.tokens.doc_id)
        errs = errors_of(parsed)
        code = errs[0].get("code") if errs else None
        if code in THROTTLE_CODES or code in SESSION_CODES or status in (403, 429):
            wait = min(2 ** t + 1, 25)
            print(f"   [{tag}] throttled (status={status} code={code}); rotate IP, wait {wait}s")
            ext._pool.invalidate()
            time.sleep(wait)
            continue
        return status, parsed
    return status, parsed


def search_vars(query, *, sort_mode: str | None = "DESCENDING",
                search_type="KEYWORD_UNORDERED", extra=None):
    v = build_search_variables(query=query, countries=["US"], search_type=search_type)
    v.pop("sortData", None)
    if sort_mode == "DESCENDING":
        v["sortData"] = {"mode": "SORT_BY_TOTAL_IMPRESSIONS", "direction": "DESCENDING"}
    elif sort_mode == "ASCENDING":
        v["sortData"] = {"mode": "SORT_BY_TOTAL_IMPRESSIONS", "direction": "ASCENDING"}
    if extra:
        v.update(extra)
    return v


def connection(parsed):
    main = (parsed.get("data", {}) or {}).get("ad_library_main") \
        or (parsed.get("data", {}) or {}).get("adLibraryMain") or {}
    conn = main.get("search_results_connection") \
        or main.get("searchResultsConnection") or {}
    return main, conn


def ad_nodes(parsed):
    _main, conn = connection(parsed)
    pairs = []
    for edge in conn.get("edges", []) or []:
        coll = edge.get("node", {}) or {}
        for ad in coll.get("collated_results", []) or []:
            pairs.append((coll, ad))
    return pairs


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path + "/" + str(k), str(k), v
            yield from walk(v, path + "/" + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            yield from walk(v, f"{path}[{i}]")


def find_keys(obj, substrs, limit=60):
    hits = {}
    for p, k, v in walk(obj):
        if any(s in k.lower() for s in substrs) and not isinstance(v, (dict, list)):
            hits.setdefault(p, v)
        if len(hits) > limit:
            break
    return hits


def like_of(ad):
    snap = ad.get("snapshot") or {}
    val = snap.get("page_like_count")
    return val if isinstance(val, int) else None


def days_ago(unix_ts):
    if not unix_ts:
        return None
    try:
        return round((time.time() - float(unix_ts)) / 86400.0, 1)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# probes (analyzers of already-fetched envelopes where possible)
# --------------------------------------------------------------------------- #
def V1_node_signals(desc_parsed):
    print("\n=== V1: per-ad signal fields (page_like_count + spend/reach/impressions) ===")
    pairs = ad_nodes(desc_parsed)
    if not pairs:
        print("  no nodes")
        return {"ok": False}
    ad0 = pairs[0][1]
    print("  ad-node keys :", sorted(ad0.keys()))
    print("  snapshot keys:", sorted((ad0.get("snapshot") or {}).keys()))
    sample = []
    nonnull = {"reach_estimate": 0, "spend": 0, "impressions_with_index": 0}
    for _coll, a in pairs[:8]:
        snap = a.get("snapshot") or {}
        row = {
            "page_name": (snap.get("page_name") or a.get("page_name")),
            "page_like_count": snap.get("page_like_count"),
            "reach_estimate": a.get("reach_estimate"),
            "spend": a.get("spend"),
            "impressions_with_index": a.get("impressions_with_index"),
            "total_active_time": a.get("total_active_time"),
            "n_countries": len(a.get("targeted_or_reached_countries") or []),
            "collation_count": a.get("collation_count"),
            "start_age_days": days_ago(a.get("start_date")),
        }
        for k in nonnull:
            if a.get(k) not in (None, "", {}, []):
                nonnull[k] += 1
        sample.append(row)
        print("   ", json.dumps(row, default=str))
    print("  non-null counts (of 8):", nonnull)
    return {"ok": True, "page_like_count_path": "snapshot.page_like_count",
            "spend_reach_impressions_populated": nonnull, "sample": sample}


def V2_result_count(desc_parsed):
    print("\n=== V2: search-level result count field ===")
    main, conn = connection(desc_parsed)
    print("  ad_library_main keys:", sorted(main.keys()))
    print("  connection keys     :", sorted(k for k in conn.keys() if k != "edges"))
    count_hits = find_keys({"main": {k: v for k, v in main.items() if k != "search_results_connection"},
                            "conn": {k: v for k, v in conn.items() if k != "edges"}},
                           ["count", "total", "result", "num"])
    print("  COUNT/TOTAL candidates:", json.dumps(count_hits, indent=2)[:1200])
    return {"ok": True, "count_candidates": count_hits}


def V3_exact_vs_unordered(unordered_parsed, exact_parsed):
    print("\n=== V3: KEYWORD_EXACT_PHRASE vs KEYWORD_UNORDERED (relevancy sort) ===")
    nu, ne = len(ad_nodes(unordered_parsed)), len(ad_nodes(exact_parsed))
    eu = bool(errors_of(unordered_parsed))
    ee = bool(errors_of(exact_parsed))
    print(f"  KEYWORD_UNORDERED   nodes={nu} error={eu}")
    print(f"  KEYWORD_EXACT_PHRASE nodes={ne} error={ee}")
    return {"ok": True, "unordered_nodes": nu, "exact_nodes": ne,
            "exact_returns_results": ne > 0 and not ee}


def V4_start_date(ext, baseline_parsed):
    print("\n=== V4: startDate shape (server-side recency) ===")
    base_nodes = ad_nodes(baseline_parsed)
    base_ages = [days_ago(a.get("start_date")) for _c, a in base_nodes]
    base_ages = [x for x in base_ages if x is not None]
    base_max = max(base_ages) if base_ages else None
    print(f"  baseline(startDate=None): nodes={len(base_nodes)} max_age={base_max}")
    shapes = {
        "iso_min_max": {"startDate": {"min": "2026-05-27", "max": "2026-06-10"}},
        "iso_string": {"startDate": "2026-05-27"},
        "unix_min_max": {"startDate": {"min": 1748304000, "max": 1749513600}},
        "list_pair": {"startDate": [1748304000, 1749513600]},
    }
    out = {"baseline_max_age": base_max, "baseline_nodes": len(base_nodes)}
    for name, extra in shapes.items():
        time.sleep(PACE)
        _s, parsed = post_resilient(ext, search_vars(QUERY, sort_mode="DESCENDING", extra=extra),
                                    tag=f"V4:{name}")
        errs = errors_of(parsed)
        code = errs[0].get("code") if errs else None
        nodes = ad_nodes(parsed)
        ages = [days_ago(a.get("start_date")) for _c, a in nodes]
        ages = [x for x in ages if x is not None]
        amax = max(ages) if ages else None
        narrowed = bool(ages) and base_max is not None and amax is not None and amax < base_max - 5
        print(f"  {name:14s} nodes={len(nodes)} max_age={amax} err={code} "
              f"NARROWED={'YES' if narrowed else 'no'}")
        out[name] = {"nodes": len(nodes), "error_code": code, "max_age": amax, "narrowed": narrowed}
    return {"ok": True, **out}


def V5_relevancy_vs_descending(desc_parsed, rel_parsed):
    print("\n=== V5: relevancy (omit sortData) vs SORT_BY_TOTAL_IMPRESSIONS DESCENDING ===")
    out = {}
    for label, parsed in (("DESCENDING", desc_parsed), ("relevancy(omit)", rel_parsed)):
        pairs = ad_nodes(parsed)
        top = [((a.get("snapshot") or {}).get("page_name"), like_of(a)) for _c, a in pairs[:8]]
        likes = sorted(lk for _n, lk in top if isinstance(lk, int))
        med = likes[len(likes) // 2] if likes else None
        print(f"  {label:16s} nodes={len(pairs)} median_like_top8={med}")
        for nm, lk in top:
            print(f"      like={lk}  {nm}")
        out[label] = {"nodes": len(pairs), "median_like_top8": med, "top": top}
    return {"ok": True, **out}


def V6_ascending(asc_parsed):
    print("\n=== V6: sortData direction ASCENDING ===")
    errs = errors_of(asc_parsed)
    code = errs[0].get("code") if errs else None
    n = len(ad_nodes(asc_parsed))
    accepted = not errs and n > 0
    print(f"  nodes={n} err_code={code} accepted={'YES' if accepted else 'no'}")
    return {"ok": True, "accepted": accepted, "nodes": n, "error_code": code}


def V7_typeahead(ext):
    print("\n=== V7: typeahead keyword->pages (page_id + likes) ===")
    variables = {"queryString": "personalized plan", "isMobile": False,
                 "country": "US", "adType": "ALL"}
    _s, parsed = post_resilient(ext, variables, friendly=TYPEAHEAD_FRIENDLY,
                                doc_id=TYPEAHEAD_DOC_ID, tag="V7")
    main, _conn = connection(parsed)
    pages = (main.get("typeahead_suggestions") or {}).get("page_results") or []
    print(f"  page_results={len(pages)}")
    for p in pages[:8]:
        print(f"      likes={p.get('likes')}  id={p.get('page_id')}  {p.get('name')}")
    return {"ok": bool(pages), "n_pages": len(pages),
            "sample": [{"page_id": p.get("page_id"), "likes": p.get("likes"),
                        "name": p.get("name")} for p in pages[:8]]}


def main():
    if not os.environ.get("AGF_PROXIES"):
        print("ERROR: AGF_PROXIES not set — a residential proxy is required.", file=sys.stderr)
        sys.exit(2)

    ext = Extractor(search_delay=2.0, search_jitter=1.5)
    print("Warming session (rd_challenge + token heal) ...")
    ws = None
    for attempt in range(6):
        try:
            ext._pool.invalidate()
            ws = ext._pool.acquire()
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  warm attempt {attempt} failed: {exc}; retry on fresh IP ...")
            time.sleep(2.0)
    if ws is None:
        print("ERROR: could not warm a session.", file=sys.stderr)
        sys.exit(3)
    print(f"  doc_id={ws.tokens.doc_id}")

    # Shared requests (rotated/backed-off), reused across probes to limit call count.
    print("\nfetching shared envelopes ...")
    _s, desc = post_resilient(ext, search_vars(QUERY, sort_mode="DESCENDING"), tag="desc")
    time.sleep(PACE)
    _s, rel = post_resilient(ext, search_vars(QUERY, sort_mode=None), tag="relevancy")
    time.sleep(PACE)
    _s, exact = post_resilient(ext, search_vars(QUERY, sort_mode=None,
                                                search_type="KEYWORD_EXACT_PHRASE"), tag="exact")
    time.sleep(PACE)
    _s, asc = post_resilient(ext, search_vars(QUERY, sort_mode="ASCENDING"), tag="asc")

    results = {}
    results["V1"] = V1_node_signals(desc)
    results["V2"] = V2_result_count(desc)
    results["V3"] = V3_exact_vs_unordered(rel, exact)
    results["V5"] = V5_relevancy_vs_descending(desc, rel)
    results["V6"] = V6_ascending(asc)
    results["V4"] = V4_start_date(ext, desc)
    results["V7"] = V7_typeahead(ext)

    out_path = f"/tmp/agf-probe-{date.today().isoformat()}.json"
    with open(out_path, "w") as fh:
        json.dump({"ts": datetime.now(timezone.utc).isoformat(),
                   "doc_id": ws.tokens.doc_id, "results": results}, fh, indent=2, default=str)
    print(f"\nSummary written to {out_path}")


if __name__ == "__main__":
    main()
