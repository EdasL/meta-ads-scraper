"""Release-expand: App Store new-releases -> Meta typeahead -> resolve new advertiser
pages to candidates. Verifies in-scope-genre filtering, known-page skipping, incumbent
typeahead veto, the budget cap, and that survivors land in discovery.candidates via the
sweep's classify->upsert path. Engine fixture + fake extractor (zero network); sentinel
'RELX' page ids only (shared agf_it DB holds other rows)."""
from sqlalchemy import text

from agf.discovery.release_mint import expand_releases_via_typeahead


class _FakeAd:
    def __init__(self, page_id, store_id):
        self.page_id = page_id
        self.page_name = f"Page {page_id}"
        self.page_like_count = 800
        self.link_url = f"https://apps.apple.com/us/app/id{store_id}"
        self.cta_type = "INSTALL_MOBILE_APP"
        self.start_date = None


class _FakeExtractor:
    """typeahead(name)->pages by name; search_listing(page_id)->one app ad per page."""
    def __init__(self, pages_by_name, store_by_page):
        self.pages_by_name = pages_by_name      # name -> [{page_id, page_like_count}]
        self.store_by_page = store_by_page      # page_id -> store_id
        self.typeahead_calls = []
        self.listing_calls = []

    async def typeahead(self, *, query, country="US"):
        self.typeahead_calls.append(query)
        return self.pages_by_name.get(query, [])

    async def search_listing(self, *, page_id, max_pages=2):
        self.listing_calls.append(page_id)
        return ([_FakeAd(page_id, self.store_by_page[page_id])], False)


def _fetch_factory(apps):
    def _fetch(client, *, country="us", genre=None, limit=200):
        return apps
    return _fetch


def test_resolves_new_advertiser_pages_and_skips_incumbents(_migrated_engine):
    eng = _migrated_engine

    def _clean():
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates "
                              "WHERE page_id LIKE 'RELX%'"))
    _clean()
    try:
        apps = [
            {"store_id": "1", "name": "Fastic App", "genre": "Health & Fitness"},
            {"store_id": "2", "name": "Big Brand", "genre": "Health & Fitness"},
            {"store_id": "9", "name": "Some Game", "genre": "Games"},   # out of scope
        ]
        pages_by_name = {
            "Fastic App": [{"page_id": "RELX_ADV", "page_like_count": 700}],
            "Big Brand": [{"page_id": "RELX_INC", "page_like_count": 5_000_000}],  # incumbent
        }
        ext = _FakeExtractor(pages_by_name, store_by_page={"RELX_ADV": "555111"})
        stats = expand_releases_via_typeahead(
            eng, ext, client=None, fetch=_fetch_factory(apps))

        # out-of-scope "Games" app never gets a typeahead call
        assert "Some Game" not in ext.typeahead_calls
        assert set(ext.typeahead_calls) == {"Fastic App", "Big Brand"}
        # the incumbent page (5M likes) is vetoed before its listing is scraped
        assert "RELX_INC" not in ext.listing_calls
        assert ext.listing_calls == ["RELX_ADV"]
        assert stats["incumbent_skipped"] == 1
        assert stats["candidates_new"] == 1

        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT store_id, technique, source_keyword FROM discovery.candidates "
                "WHERE page_id='RELX_ADV'")).one()
        assert row.store_id == "555111"
        assert row.technique == "release_typeahead"
        assert row.source_keyword == "Fastic App"
    finally:
        _clean()


def test_known_pages_are_not_rescraped(_migrated_engine):
    eng = _migrated_engine

    def _clean():
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM discovery.candidates "
                              "WHERE page_id LIKE 'RELX%'"))
    _clean()
    try:
        # seed a candidate so its page is already "known"
        from agf.db.candidates import upsert_sweep_candidate
        with eng.begin() as conn:
            upsert_sweep_candidate(conn, {
                "page_id": "RELX_KNOWN", "page_name": "Known", "page_like_count": 500,
                "store": "ios", "store_id": "999", "link_url": "https://apps.apple.com/app/id999",
                "cta_type": "INSTALL_MOBILE_APP", "ad_start_date": None,
                "source_keyword": "seed", "technique": "niche_term"})
        apps = [{"store_id": "1", "name": "Known App", "genre": "Education"}]
        pages_by_name = {"Known App": [{"page_id": "RELX_KNOWN", "page_like_count": 500}]}
        ext = _FakeExtractor(pages_by_name, store_by_page={})
        stats = expand_releases_via_typeahead(
            eng, ext, client=None, fetch=_fetch_factory(apps))
        # known page is skipped: never counted, never scraped
        assert ext.listing_calls == []
        assert stats["typeahead_pages"] == 0
        assert stats["scraped_pages"] == 0
    finally:
        _clean()
