import pytest
import httpx
from agf.livesearch import bootstrap, search_us, itunes_lookup
from agf.enrich import parse_lookup


@pytest.mark.integration
def test_bootstrap_and_search_returns_ads():
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        lsd, doc_id = bootstrap(client)
        assert lsd and doc_id
        ads = search_us(client, "workout app", lsd, doc_id, max_pages=1)
        assert len(ads) > 0, "expected at least one ad from a US keyword search"


@pytest.mark.integration
def test_itunes_lookup_live():
    with httpx.Client(timeout=30) as client:
        meta = parse_lookup(itunes_lookup(client, "389801252"))  # Instagram
        assert meta and meta.name
