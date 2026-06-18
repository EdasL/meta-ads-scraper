# tests/test_analyze_ratings.py
from sqlalchemy import text
from scripts.analyze_ratings import analyze_ratings


def test_analyze_ratings_breaks_down_by_trait(pg_conn):
    # analyze_ratings aggregates the WHOLE table, so clear it inside this rolled-back
    # txn for a deterministic count (committed rows in the shared agf_it DB return on
    # rollback). Without this the assertion drifts with whatever ratings the DB holds.
    pg_conn.execute(text("DELETE FROM frontend.app_ratings"))
    rows = [("u@x.com", 1, "dislike", '{"cohort":"Games","gem_score":72,"has_funnel":false}'),
            ("u@x.com", 2, "dislike", '{"cohort":"Games","gem_score":68,"has_funnel":false}'),
            ("u@x.com", 3, "like",    '{"cohort":"Health","gem_score":81,"has_funnel":true}')]
    for ue, aid, r, f in rows:
        pg_conn.execute(text("INSERT INTO frontend.app_ratings (user_email,advertiser_id,rating,comment,features) "
                             "VALUES (:ue,:aid,:r,'',CAST(:f AS jsonb))"),
                        {"ue": ue, "aid": aid, "r": r, "f": f})
    out = analyze_ratings(pg_conn)
    assert out["totals"] == {"like": 1, "neutral": 0, "dislike": 2}
    assert out["by_cohort"]["Games"]["dislike"] == 2
    assert out["by_funnel"]["with_funnel"]["like"] == 1
