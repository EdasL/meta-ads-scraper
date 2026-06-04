from agf.api import queries
from agf.db.engine import begin


def test_shortlist_ranked_and_filtered(seeded):
    engine, ids = seeded
    with engine.connect() as conn:
        rows = queries.shortlist(conn, min_score=70)
        got = [r["advertiser_id"] for r in rows if r["advertiser_id"] in ids.values()]
        assert got == [ids["P7A"], ids["P7B"]]            # ranked by gem_score DESC, ≥70
        hf = queries.shortlist(conn, cohort="Health & Fitness×US", min_score=0)
        assert ids["P7C"] not in [r["advertiser_id"] for r in hf]   # cohort filter excludes Edu
        assert queries.shortlist(conn, min_score=70, limit=1)[0]["advertiser_id"] == ids["P7A"]


def test_dossier_and_scaling_and_missing(seeded):
    engine, ids = seeded
    with engine.connect() as conn:
        d = queries.gem_dossier(conn, ids["P7A"])
        assert d["gem"]["page_name"] == "Alpha" and d["page_category"] == "Software"
        assert queries.gem_dossier(conn, -999) is None
        series = queries.gem_scaling(conn, ids["P7A"])
        assert [p["active_ad_count"] for p in series] == [1, 2, 4, 6]


def test_review_queue_and_record_decision(seeded):
    engine, ids = seeded
    with engine.connect() as conn:
        q = [r["advertiser_id"] for r in queries.review_queue(conn, min_score=70)]
        assert ids["P7A"] in q and ids["P7B"] in q and ids["P7C"] not in q
    with begin(engine) as conn:
        queries.record_decision(conn, ids["P7A"], "accept", "founder", "looks great")
    with engine.connect() as conn:
        q2 = [r["advertiser_id"] for r in queries.review_queue(conn, min_score=70)]
        assert ids["P7A"] not in q2 and ids["P7B"] in q2   # decided -> out of queue
