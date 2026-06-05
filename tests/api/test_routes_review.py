from fastapi.testclient import TestClient
from agf.api.app import create_app


def test_queue_then_decision_removes_from_queue(seeded):
    engine, ids = seeded
    c = TestClient(create_app(engine))
    rows = c.get("/api/review/queue").json()
    q = [r["advertiser_id"] for r in rows]
    assert ids["P7A"] in q and ids["P7C"] not in q
    alpha = next(r for r in rows if r["advertiser_id"] == ids["P7A"])
    assert alpha["active_ad_count"] == 6 and alpha["max_longevity_days"] == 30
    ok = c.post(f"/api/review/{ids['P7A']}/decision",
                json={"verdict": "accept", "reviewer": "founder", "note": "go"})
    assert ok.status_code == 200
    q2 = [r["advertiser_id"] for r in c.get("/api/review/queue").json()]
    assert ids["P7A"] not in q2 and ids["P7B"] in q2
    bad = c.post(f"/api/review/{ids['P7B']}/decision",
                 json={"verdict": "nope", "reviewer": "x"})
    assert bad.status_code == 422            # verdict enum enforced by Pydantic
