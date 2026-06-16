"""Inspect the RAW Ad Library GraphQL ad node for any impressions/reach/spend field —
in particular the coarse "impression bucket" / "Low Impression Count" signal Meta
reportedly added to ad cards in late 2025. If such a field is in the response for US
ads, we can sort the reel by it client-side even though the server-side sort is ignored.

Dumps, for the first few ad nodes of a US page-scoped search:
  - the full set of top-level node keys and snapshot keys
  - every key path (anywhere in the node) matching impress|reach|bucket|spend|impact + its value

Run:  source ops/.env && AGF_DATABASE_URL=...localhost:5433... \
      PYTHONPATH=src .venv/bin/python scripts/inspect_ad_fields.py [PAGE_ID]
"""
import os
import re
import sys

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.request import build_form_body
from agf.fbjson import parse_fb_json
from agf.extract.backoff import raise_for_fb_error
from agf.extract.search import build_search_variables, _GRAPHQL
from agf.extract.proxy import ProxyPool
from agf.extract.tls_client import AdLibrarySession
from agf.extract.session_pool import SessionPool

WANT = re.compile(r"impress|reach|bucket|spend|impact|audience", re.I)


def first_page_id(engine):
    with engine.connect() as c:
        row = c.execute(text(
            """SELECT a.page_id FROM public.advertisers a
               JOIN public.ads ad ON ad.advertiser_id = a.id
               WHERE a.page_id IS NOT NULL
               GROUP BY a.page_id HAVING count(ad.id) >= 8
               ORDER BY count(ad.id) DESC LIMIT 1"""
        )).first()
    return str(row[0]) if row else None


def walk(obj, path=""):
    """Yield (path, value) for every leaf/branch key matching WANT."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if WANT.search(str(k)):
                shown = v if not isinstance(v, (dict, list)) else f"<{type(v).__name__} len={len(v)}>"
                yield p, shown
            yield from walk(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2]):  # sample first 2 of each list
            yield from walk(v, f"{path}[{i}]")


def main():
    db = os.environ.get("AGF_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db:
        sys.exit("no AGF_DATABASE_URL in env")
    pid = sys.argv[1] if len(sys.argv) > 1 else first_page_id(get_engine(db))
    if not pid:
        sys.exit("no page_id available")

    proxies = ProxyPool.from_env()
    pool = SessionPool(make_session=lambda: AdLibrarySession(
        proxy=(p := proxies.acquire()), proxy_dict=proxies.as_curl_proxies(p)))
    ws = pool.acquire()

    country = os.environ.get("PROBE_COUNTRY", "US")
    variables = build_search_variables(query="", countries=[country], active_status="ALL",
                                       search_type="page", view_all_page_id=pid)
    body = build_form_body(lsd=ws.tokens.lsd, variables=variables, doc_id=ws.tokens.doc_id)
    resp = ws.session.post(_GRAPHQL, data=body, headers={  # type: ignore[attr-defined]
        "x-fb-lsd": ws.tokens.lsd,
        "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
        "content-type": "application/x-www-form-urlencoded",
    })
    parsed = parse_fb_json(resp.text)
    raise_for_fb_error(parsed)

    main_ = (parsed.get("data", {}) or {}).get("ad_library_main") \
        or (parsed.get("data", {}) or {}).get("adLibraryMain") or {}
    conn = main_.get("search_results_connection") or main_.get("searchResultsConnection") or {}
    nodes = []
    for edge in conn.get("edges", []):
        nodes.extend((edge.get("node", {}) or {}).get("collated_results", []))

    print(f"page_id={pid}  nodes_on_page={len(nodes)}\n")
    if not nodes:
        print("no ad nodes returned"); return

    n0 = nodes[0]
    print("TOP-LEVEL NODE KEYS:\n  ", sorted(n0.keys()), "\n")
    print("SNAPSHOT KEYS:\n  ", sorted((n0.get("snapshot") or {}).keys()), "\n")

    print("MATCHES (impress|reach|bucket|spend|impact|audience) across first 3 nodes:")
    any_hit = False
    for idx, node in enumerate(nodes[:3]):
        hits = list(walk(node))
        if hits:
            any_hit = True
            print(f"  node[{idx}] ad={node.get('ad_archive_id')}:")
            for p, v in hits:
                print(f"     {p} = {v}")
    if not any_hit:
        print("  NONE — no impressions/reach/bucket/spend field in the US ad node.")


if __name__ == "__main__":
    main()
