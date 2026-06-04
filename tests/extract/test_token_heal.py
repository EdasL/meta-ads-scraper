from pathlib import Path
import pytest
from agf.extract.token_heal import (
    SessionTokens, extract_fb_dtsg, heal_doc_id, heal_doc_id_from_bundles,
    heal_from_html, FALLBACK_DOC_IDS,
)
from agf.bootstrap import extract_lsd
from tests.extract.conftest import FakeResponse, FakeSession

HTML = (Path(__file__).parent / "fixtures" / "ads_library_real.html").read_text()


def test_extract_lsd_reuses_plan1_regex():
    assert extract_lsd(HTML) == "AdRAQDUWxPlive"


def test_extract_fb_dtsg():
    assert extract_fb_dtsg(HTML) == "NAcMlivetoken123"


def test_heal_doc_id_from_inline_bundle():
    assert heal_doc_id(HTML) == "25464068859919530"


def test_heal_doc_id_falls_back_when_absent():
    assert heal_doc_id("<html>no bundle</html>") == FALLBACK_DOC_IDS[0]


def test_heal_from_html_returns_full_tokens():
    tokens = heal_from_html(HTML)
    assert isinstance(tokens, SessionTokens)
    assert tokens.lsd == "AdRAQDUWxPlive"
    assert tokens.fb_dtsg == "NAcMlivetoken123"
    assert tokens.doc_id == "25464068859919530"


def test_heal_from_html_raises_without_lsd():
    with pytest.raises(ValueError):
        heal_from_html("<html>challenge stub, no lsd</html>")


def test_heal_doc_id_from_bundles_fetches_when_not_inline():
    # page HTML has a script src but no inline doc_id; the bundle carries it
    page = '<html><head><script src="https://x.fbcdn.net/bundle.js"></script></head></html>'
    bundle = ('__d("AdLibrarySearchPaginationQuery_facebookRelayOperation",[],'
              '(function(a){a.exports="99999999999999999"}),null);')
    session = FakeSession(responses={("GET", "bundle.js"): [FakeResponse(200, bundle)]})
    assert heal_doc_id_from_bundles(session, page) == "99999999999999999"


def test_heal_doc_id_from_bundles_falls_back_when_no_match():
    page = '<html><head><script src="https://x.fbcdn.net/bundle.js"></script></head></html>'
    session = FakeSession(responses={("GET", "bundle.js"): [FakeResponse(200, "no docid here")]})
    assert heal_doc_id_from_bundles(session, page) == FALLBACK_DOC_IDS[0]
