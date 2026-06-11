from datetime import datetime, timezone
from sqlalchemy import text
from agf.parse import AdRecord
from agf.db import repo
from agf.scoring.whitespace import load_niche_crowding

NOW = int(datetime(2026, 6, 4, tzinfo=timezone.utc).timestamp())


def _adv(conn, page_id):
    return repo.upsert_advertiser(conn, AdRecord(
        f"{page_id}-AD", page_id, page_id, True, NOW, None, 1,
        "INSTALL_MOBILE_APP", "Install",
        f"https://apps.apple.com/us/app/x/id{page_id}", "body"))


def _candidate(conn, page_id, store_id, keyword, advertiser_id):
    conn.execute(text(
        "INSERT INTO discovery.candidates "
        "  (page_id, store, store_id, source_keyword, status, kept_advertiser_id) "
        "VALUES (:p, 'ios', :s, :k, 'kept', :a) "
        "ON CONFLICT (page_id, store, store_id) DO UPDATE "
        "  SET source_keyword=EXCLUDED.source_keyword, "
        "      kept_advertiser_id=EXCLUDED.kept_advertiser_id"),
        {"p": page_id, "s": store_id, "k": keyword, "a": advertiser_id})


def _weeks(conn, advertiser_id, n):
    for i in range(n):
        repo.upsert_app_advertiser_metric(
            conn, advertiser_id, f"2026-05-{4 + 7 * i:02d}", 3, 3, 100)


def test_load_niche_crowding(pg_conn):
    # "ai calorie tracker": 2 sustained + 1 unsustained member
    a1, a2, a3 = (_adv(pg_conn, p) for p in ("WS1", "WS2", "WS3"))
    # "water reminder": 1 sustained member
    a4 = _adv(pg_conn, "WS4")
    # no candidates row at all
    a5 = _adv(pg_conn, "WS5")
    _candidate(pg_conn, "WS1", "101", "ai calorie tracker", a1)
    _candidate(pg_conn, "WS2", "102", "ai calorie tracker", a2)
    _candidate(pg_conn, "WS3", "103", "ai calorie tracker", a3)
    _candidate(pg_conn, "WS4", "104", "water reminder", a4)
    _weeks(pg_conn, a1, 3)
    _weeks(pg_conn, a2, 2)
    _weeks(pg_conn, a3, 1)          # 1 week < sustained floor
    _weeks(pg_conn, a4, 4)

    by_adv, counts = load_niche_crowding(pg_conn)

    assert by_adv[a1] == ("ai calorie tracker", 2)
    assert by_adv[a2] == ("ai calorie tracker", 2)
    # unsustained member still inherits its niche's crowding
    assert by_adv[a3] == ("ai calorie tracker", 2)
    assert by_adv[a4] == ("water reminder", 1)
    assert a5 not in by_adv
    # one count per distinct niche (the shared test DB may hold other niches)
    assert {1, 2} <= set(counts)


def test_multi_keyword_advertiser_takes_most_crowded_niche(pg_conn):
    a1, a2 = (_adv(pg_conn, p) for p in ("WS6", "WS7"))
    # a1 sits in a crowded niche (with a2) AND alone in a sparse one
    _candidate(pg_conn, "WS6", "106", "sleep tracker", a1)
    _candidate(pg_conn, "WS6", "206", "snore recorder", a1)
    _candidate(pg_conn, "WS7", "107", "sleep tracker", a2)
    _weeks(pg_conn, a1, 2)
    _weeks(pg_conn, a2, 2)

    by_adv, _counts = load_niche_crowding(pg_conn)

    assert by_adv[a1] == ("sleep tracker", 2)   # most crowded wins
