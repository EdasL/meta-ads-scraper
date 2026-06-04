from fastapi.testclient import TestClient
from agf.api.app import create_app


def test_shortlist_ranked_filtered(seeded):
    engine, ids = seeded
    c = TestClient(create_app(engine))
    r = c.get("/api/shortlist", params={"min_score": 70})
    got = [row["advertiser_id"] for row in r.json() if row["advertiser_id"] in ids.values()]
    assert got == [ids["P7A"], ids["P7B"]]
    r2 = c.get("/api/shortlist", params={"cohort": "Education×US", "min_score": 0})
    assert ids["P7A"] not in [row["advertiser_id"] for row in r2.json()]
