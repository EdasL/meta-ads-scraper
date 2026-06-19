from agf.discovery.funnel_classify import is_store_bounce


def test_mmp_and_redirect_hosts_are_bounces():
    for host in ("appstack.link", "aicalcaloriecounterdietlog.onelink.me",
                 "lnk.to", "go.link", "adj.st", "app.adjust.com", "x.app.link"):
        assert is_store_bounce(host) is True, host


def test_real_funnel_domains_are_not_bounces():
    for host in ("foodhealth.co", "web2wave.com", "meetaugust.ai", "quitwithjones.com",
                 "10mf.siiites.com", "", "myonelink.me.evil-lookalike.com"):
        assert is_store_bounce(host) is False, host


def test_suffix_match_not_substring():
    # must match at a label boundary, not anywhere in the string
    assert is_store_bounce("onelink.me") is True
    assert is_store_bounce("notonelink.me") is False


def test_record_web_funnel_skips_store_bounce():
    # the write gate returns 0 before any probe/deps when the resolved host is a bounce
    import asyncio
    from types import SimpleNamespace
    from agf.enrich_full.run_enrich import record_web_funnel
    target = SimpleNamespace(
        kind="web",
        final_url="https://appstack.link/deeplink/resolve?pid=x&af_ad=y")
    n = asyncio.run(record_web_funnel(None, 1, "https://ad.example/x", target, None))
    assert n == 0
