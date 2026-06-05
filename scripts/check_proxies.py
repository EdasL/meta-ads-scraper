"""Verify AGF_PROXIES before a real crawl — run this the moment you have proxy creds.

    AGF_PROXIES="user:pass@host:port,user2:pass2@host:port" .venv/bin/python scripts/check_proxies.py

For each proxy it shows the EGRESS ip + country + ISP (proves the proxy routes and is
residential, not datacenter), then does one real Meta Ad Library request through the pool
to confirm the whole path works. No DB or app state touched."""
import asyncio
import sys

from agf.extract.proxy import ProxyPool


def _mask(proxy: str) -> str:
    """Hide credentials: user:pass@host:port -> ***@host:port."""
    return "***@" + proxy.split("@", 1)[1] if "@" in proxy else proxy


def _proxies_dict(proxy: str) -> dict:
    url = proxy if "://" in proxy else f"http://{proxy}"
    return {"http": url, "https": url}


def _check_one(proxy: str) -> str:
    """Route a 'what is my IP' request through the proxy; return a one-line verdict."""
    from curl_cffi import requests
    try:
        r = requests.get("http://ip-api.com/json", proxies=_proxies_dict(proxy),
                         impersonate="chrome", timeout=20)
        d = r.json()
        if d.get("status") != "success":
            return f"FAIL {_mask(proxy)}: ip lookup said {d!r}"
        isp = d.get("isp", "?")
        hosting = d.get("hosting")     # ip-api flags datacenter/hosting ranges
        flag = "  ⚠ looks like DATACENTER (Meta may block)" if hosting else ""
        return f"OK   {_mask(proxy)} -> {d.get('query')} {d.get('countryCode')} ({isp}){flag}"
    except Exception as exc:
        return f"FAIL {_mask(proxy)}: {type(exc).__name__}: {exc}"


def main() -> None:
    pool = ProxyPool.from_env()
    n = len(pool)
    if n == 0:
        sys.exit("AGF_PROXIES is empty. Set it to user:pass@host:port (comma/newline "
                 "separated) and re-run.")
    print(f"{n} prox{'y' if n == 1 else 'ies'} configured. Checking egress IPs…\n")
    ok = 0
    for proxy in pool._proxies:
        line = _check_one(proxy)
        print("  " + line)
        ok += line.startswith("OK")
    print(f"\n{ok}/{n} proxies reachable.")
    if ok == 0:
        sys.exit("No working proxy — fix creds/endpoint before crawling.")

    print("\nMeta Ad Library smoke through the pool…")
    from agf.extract.facade import Extractor
    try:
        ads = asyncio.run(asyncio.wait_for(
            Extractor(proxies=pool).search(query="", countries=["DE"],
                                           sort_by_impressions=True, max_pages=1),
            timeout=120))
        print(f"  OK — {len(ads)} ads via proxy. Proxies are good to go.")
    except Exception as exc:
        print(f"  FAIL — Meta request through proxy failed: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
