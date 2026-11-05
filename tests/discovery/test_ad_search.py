import json
import threading

import pytest
from sqlalchemy import text

from agf.db.ad_searches import creative_hash, load_search, save_ads
from agf.db.engine import begin
from agf.discovery.ad_search import run_ad_search
from agf.discovery.ad_search_daemon import process_once
from agf.parse import AdRecord


def ad(id="10", page="100", body="Learn more"):
    return AdRecord(
        id,
        page,
        "Example",
        True,
        1700000000,
        None,
        1,
        "LEARN_MORE",
        "Learn more",
        "https://example.com",
        body,
    )


@pytest.fixture
def searches(_migrated_engine):
    ids = []

    def create(**request):
        with begin(_migrated_engine) as c:
            id = c.execute(
                text(
                    "INSERT INTO frontend.ad_searches(created_by,request) VALUES ('raw-test@local',CAST(:r AS jsonb)) RETURNING id"
                ),
                {
                    "r": json.dumps(
                        {
                            "operation": "keyword",
                            "queries": ["walking"],
                            "country": "US",
                            "match_mode": "exact",
                            **request,
                        }
                    )
                },
            ).scalar_one()
            ids.append(id)
        return id

    yield _migrated_engine, create
    with begin(_migrated_engine) as c:
        for id in ids:
            c.execute(
                text(
                    "DELETE FROM public.pipeline_jobs WHERE kind='ad_search' AND advertiser_id=:id"
                ),
                {"id": id},
            )
            c.execute(text("DELETE FROM frontend.ad_searches WHERE id=:id"), {"id": id})


class Extractor:
    def __init__(self):
        self.calls = []

    async def typeahead(self, **kw):
        self.calls.append(kw)
        return [
            {"page_id": "1", "page_name": "Same"},
            {"page_id": "2", "page_name": "Same"},
        ]

    async def search_page(self, **kw):
        self.calls.append(kw)
        if kw.get("query") == "broken":
            raise RuntimeError("secret proxy credentials")
        if kw.get("query") == "empty":
            return [], None, False
        if kw.get("cursor"):
            return [ad("20")], None, False
        return [ad(), ad("11", body="")], "next-cursor", True


def test_raw_ads_preserve_sparse_copy_and_have_no_model_dependency(
    searches, monkeypatch
):
    engine, create = searches
    id = create()
    e = Extractor()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert run_ad_search(engine, id, extractor_factory=lambda: e) == "done"
    assert e.calls[0]["search_type"] == "KEYWORD_EXACT_PHRASE"
    with begin(engine) as c:
        rows = (
            c.execute(
                text(
                    "SELECT body_text FROM frontend.ad_search_results WHERE search_id=:id ORDER BY id"
                ),
                {"id": id},
            )
            .scalars()
            .all()
        )
        assert rows == ["Learn more", ""]
        s = load_search(c, id)
        assert s["progress"]["sources"]["query:walking"]["cursor"] == "next-cursor"
        assert s["progress"]["first_result_seconds"] is not None


def test_name_collisions_return_all_candidates(searches):
    engine, create = searches
    id = create(operation="resolve", queries=["Same"], country="ALL")
    e = Extractor()
    assert run_ad_search(engine, id, extractor_factory=lambda: e) == "done"
    with begin(engine) as c:
        source = load_search(c, id)["progress"]["sources"]["query:Same"]
        assert len(source["advertisers"]) == 2
        assert source["lookup_country"] == "US"


def test_page_id_country_and_cursor_pass_through(searches):
    engine, create = searches
    id = create(operation="page", page_id="123", country="GB", cursor="next-cursor")
    e = Extractor()
    assert run_ad_search(engine, id, extractor_factory=lambda: e) == "done"
    assert e.calls == [
        {
            "countries": ["GB"],
            "active_status": "ACTIVE",
            "cursor": "next-cursor",
            "view_all_page_id": "123",
            "search_type": "page",
        }
    ]
    with begin(engine) as c:
        assert not load_search(c, id)["progress"]["sources"]["page:123"]["has_more"]


def test_empty_and_failed_sources_are_distinct(searches):
    engine, create = searches
    id = create(queries=["empty", "broken"])
    e = Extractor()
    assert run_ad_search(engine, id, extractor_factory=lambda: e) == "done"
    with begin(engine) as c:
        p = load_search(c, id)["progress"]
        assert p["partial"]
        assert p["sources"]["query:empty"]["status"] == "done"
        assert p["sources"]["query:broken"]["status"] == "fetch_failed"
        assert "secret" not in json.dumps(p)


def test_all_sources_failed_is_error(searches):
    engine, create = searches
    id = create(queries=["broken"])
    assert run_ad_search(engine, id, extractor_factory=Extractor) == "error"
    with begin(engine) as c:
        assert load_search(c, id)["error_text"] == "retrieval_failed"


def test_pages_persist_before_slow_sources_finish(searches):
    engine, create = searches
    id = create(queries=["fast", "slow"])
    ready = threading.Event()
    checked = []

    class Incremental(Extractor):
        async def search_page(self, **kw):
            if kw["query"] == "slow":
                assert ready.wait(5)
                with begin(engine) as c:
                    assert (
                        c.execute(
                            text(
                                "SELECT count(*) FROM frontend.ad_search_results WHERE search_id=:id"
                            ),
                            {"id": id},
                        ).scalar()
                        == 2
                    )
                    assert load_search(c, id)["status"] == "running"
                    checked.append(True)
                return [], None, False
            return await super().search_page(**kw)

    import time
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_ad_search, engine, id, extractor_factory=Incremental)
        for _ in range(100):
            with begin(engine) as c:
                count = c.execute(
                    text(
                        "SELECT count(*) FROM frontend.ad_search_results WHERE search_id=:id"
                    ),
                    {"id": id},
                ).scalar()
            if count:
                ready.set()
                break
            time.sleep(0.01)
        assert future.result() == "done"
    assert checked == [True]


def test_daemon_crash_closes_search_and_job(searches):
    engine, create = searches
    id = create()
    with begin(engine) as c:
        job = c.execute(
            text(
                "INSERT INTO public.pipeline_jobs(kind,advertiser_id,run_after) VALUES ('ad_search',:id,'2000-01-01') RETURNING id"
            ),
            {"id": id},
        ).scalar_one()
    reached = []
    assert process_once(engine, runner=lambda *a: reached.append(a))
    assert reached == [(id, job)]
    with begin(engine) as c:
        assert load_search(c, id)["status"] == "error"


def test_expired_lease_preserves_partial_ads(searches):
    engine, create = searches
    id = create()
    with begin(engine) as c:
        c.execute(
            text("UPDATE frontend.ad_searches SET status='running' WHERE id=:id"),
            {"id": id},
        )
        save_ads(c, id, [ad()], source_term="query:test", country="US")
        c.execute(
            text(
                "INSERT INTO public.pipeline_jobs(kind,advertiser_id,status,attempts,lease_expires_at) VALUES ('ad_search',:id,'running',1,now()-interval '1 minute')"
            ),
            {"id": id},
        )
    process_once(engine, runner=lambda *a: None)
    with begin(engine) as c:
        s = load_search(c, id)
        assert s["error_text"] == "lease_expired"
        assert s["progress"]["partial"]
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM frontend.ad_search_results WHERE search_id=:id"
                ),
                {"id": id},
            ).scalar()
            == 1
        )


def test_creative_hash_ignores_signed_url_query():
    a = ad()
    b = ad("11")
    a.image_urls = ["https://cdn.test/a.jpg?sig=1"]
    b.image_urls = ["https://cdn.test/a.jpg?sig=2"]
    assert creative_hash(a) == creative_hash(b)
