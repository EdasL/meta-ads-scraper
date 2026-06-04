from agf.extract.session_pool import WarmSession, SessionPool
from agf.extract.token_heal import SessionTokens


def _real_html():
    return ('<script>["LSD",[],{"token":"AVw"}]'
            '__d("AdLibrarySearchPaginationQuery_facebookRelayOperation",[],'
            '(function(a){a.exports="27201872659451053"}),null);</script>')


class FakeAdLibSession:
    def warm_get(self, url, **kw):
        return _real_html()


def test_warm_session_is_stale_after_max_age():
    clock = {"t": 1000.0}
    ws = WarmSession(session=object(), tokens=SessionTokens("a", None, "d"),
                     created_at=1000.0, max_age=1800.0, now=lambda: clock["t"])
    assert ws.is_stale() is False
    clock["t"] = 1000.0 + 1801
    assert ws.is_stale() is True


def test_pool_warms_once_then_reuses():
    warms = {"n": 0}

    class Counting(FakeAdLibSession):
        def warm_get(self, url, **kw):
            warms["n"] += 1
            return _real_html()

    pool = SessionPool(make_session=lambda: Counting(), max_age=1800.0)
    pool._now = lambda: 0.0
    a = pool.acquire()
    b = pool.acquire()
    assert a is b                 # reused, not re-warmed
    assert warms["n"] == 1
    assert a.tokens.doc_id == "27201872659451053"
    assert a.tokens.lsd == "AVw"


def test_pool_rewarms_when_stale():
    warms = {"n": 0}

    class Counting(FakeAdLibSession):
        def warm_get(self, url, **kw):
            warms["n"] += 1
            return _real_html()

    clock = {"t": 0.0}
    pool = SessionPool(make_session=lambda: Counting(), max_age=1800.0)
    pool._now = lambda: clock["t"]
    pool.acquire()
    clock["t"] = 2000.0           # past max_age
    pool.acquire()
    assert warms["n"] == 2        # re-warmed


def test_pool_invalidate_forces_rewarm():
    warms = {"n": 0}

    class Counting(FakeAdLibSession):
        def warm_get(self, url, **kw):
            warms["n"] += 1
            return _real_html()

    pool = SessionPool(make_session=lambda: Counting(), max_age=1800.0)
    pool._now = lambda: 0.0
    pool.acquire()
    pool.invalidate()             # e.g. after a 403/session error mid-stream
    pool.acquire()
    assert warms["n"] == 2
