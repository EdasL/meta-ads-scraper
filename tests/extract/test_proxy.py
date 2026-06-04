from agf.extract.proxy import ProxyPool


def test_from_env_parses_newline_and_comma(monkeypatch):
    monkeypatch.setenv("AGF_PROXIES", "u:p@host1:1000\nu:p@host2:2000, u:p@host3:3000")
    pool = ProxyPool.from_env()
    assert len(pool) == 3


def test_empty_env_yields_none(monkeypatch):
    monkeypatch.delenv("AGF_PROXIES", raising=False)
    pool = ProxyPool.from_env()
    assert len(pool) == 0
    assert pool.acquire() is None
    assert pool.as_curl_proxies(None) is None


def test_round_robin_rotation():
    pool = ProxyPool(["a:1@h:1", "b:2@h:2"])
    first = pool.acquire()
    second = pool.acquire()
    third = pool.acquire()
    assert {first, second} == {"a:1@h:1", "b:2@h:2"}
    assert third == first  # wrapped around


def test_dead_proxy_skipped_until_cooldown():
    pool = ProxyPool(["a:1@h:1", "b:2@h:2"], max_failures=1, cooldown=100.0)
    now = {"t": 0.0}
    pool._now = lambda: now["t"]
    p = pool.acquire()
    pool.mark_failed(p)                 # 1 failure == max_failures -> dead
    nxt = pool.acquire()
    assert nxt != p                     # dead one skipped
    assert pool.acquire() != p          # still skipped within cooldown
    now["t"] = 200.0                    # past cooldown
    revived = {pool.acquire() for _ in range(4)}
    assert p in revived                 # revived after cooldown


def test_as_curl_proxies_shape():
    pool = ProxyPool(["u:p@h:9"])
    proxies = pool.as_curl_proxies("u:p@h:9")
    assert proxies == {"http": "http://u:p@h:9", "https": "http://u:p@h:9"}
