"""Cursor-resumable single-page search: FakeExtractor.search_page scripting (drives T4's
adaptive-depth sweep tests) + a network-free check that Extractor.search_page delegates to
search_ads with max_pages=1 and threads the seed cursor."""
import asyncio
import inspect

from agf.extract.fake import FakeExtractor
from agf.extract.facade import Extractor
from agf.extract.token_heal import SessionTokens
from agf.parse import AdRecord


def _ad(i):
    return AdRecord(i, "P", "N", True, 1, None, 1, "X", "x",
                    "https://apps.apple.com/us/app/x/id1", "b")


# --- FakeExtractor.search_page: scripted per-page results -------------------------------

def test_fake_search_page_returns_pages_sequentially_threading_cursor():
    fx = FakeExtractor().script_pages("sleep", [[_ad("1"), _ad("2")], [_ad("3")]])

    recs0, cur0, next0 = asyncio.run(fx.search_page(query="sleep"))
    assert [r.ad_archive_id for r in recs0] == ["1", "2"]
    assert next0 is True and cur0 is not None      # more pages remain

    recs1, cur1, next1 = asyncio.run(fx.search_page(query="sleep", cursor=cur0))
    assert [r.ad_archive_id for r in recs1] == ["3"]
    assert next1 is False and cur1 is None         # last page → exhausted


def test_fake_search_page_has_next_true_until_last_page():
    fx = FakeExtractor().script_pages("q", [[_ad("a")], [_ad("b")], [_ad("c")]])
    flags, cursor = [], None
    for _ in range(3):
        recs, cursor, has_next = asyncio.run(fx.search_page(query="q", cursor=cursor))
        flags.append(has_next)
    assert flags == [True, True, False]


def test_fake_search_page_beyond_exhaustion_returns_empty():
    fx = FakeExtractor().script_pages("q", [[_ad("a")]])
    _, cursor, has_next = asyncio.run(fx.search_page(query="q"))
    assert has_next is False and cursor is None
    # a caller that ignores has_next and pages once more gets a clean empty terminal
    recs, cur, nxt = asyncio.run(fx.search_page(query="q", cursor="p1"))
    assert recs == [] and cur is None and nxt is False


def test_fake_search_page_unknown_query_is_empty_terminal():
    fx = FakeExtractor()
    assert asyncio.run(fx.search_page(query="nope")) == ([], None, False)


def test_fake_search_page_updates_stats():
    fx = FakeExtractor().script_pages("q", [[_ad("a")], [_ad("b")]])
    st = {}
    asyncio.run(fx.search_page(query="q", stats=st))
    assert st == {"pages": 1, "has_next": True}
    st2 = {}
    asyncio.run(fx.search_page(query="q", cursor="p1", stats=st2))
    assert st2 == {"pages": 1, "has_next": False}


def test_fake_search_and_search_page_are_independent():
    # scripting pages must not disturb the existing flat search() behavior
    fx = FakeExtractor(keyword_ads={"q": [_ad("flat")]}).script_pages("q", [[_ad("p")]])
    assert [r.ad_archive_id for r in asyncio.run(fx.search(query="q"))] == ["flat"]


# --- Real Extractor.search_page: signature + delegation (no network) ---------------------

class _StubPool:
    def acquire(self):
        return type("WS", (), {"session": object(),
                               "tokens": SessionTokens("L", "D", "27201872659451053")})()

    def invalidate(self):
        pass


def test_extractor_search_page_signature_and_arity():
    sig = inspect.signature(Extractor.search_page)
    assert "cursor" in sig.parameters
    assert sig.parameters["cursor"].default is None


def test_extractor_search_page_delegates_single_page_with_seed_cursor(monkeypatch):
    captured = {}

    def fake_search_ads(session, tokens, **kw):
        captured.update(kw)
        return [_ad("1")], "CURSOR2", True

    monkeypatch.setattr("agf.extract.facade.search_ads", fake_search_ads)
    ex = Extractor(pool=_StubPool(), sleep=lambda s: None)
    recs, next_cursor, has_next = asyncio.run(
        ex.search_page(query="sleep", cursor="CURSOR1"))
    assert [r.ad_archive_id for r in recs] == ["1"]
    assert (next_cursor, has_next) == ("CURSOR2", True)
    assert captured["max_pages"] == 1         # single page only
    assert captured["cursor"] == "CURSOR1"    # seed cursor threaded through
