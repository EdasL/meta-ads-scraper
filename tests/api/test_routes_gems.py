from fastapi.testclient import TestClient
from agf.api.app import create_app


def test_dossier_scaling_and_404(seeded):
    engine, ids = seeded
    c = TestClient(create_app(engine))
    d = c.get(f"/api/gems/{ids['P7A']}").json()
    assert d["gem"]["page_name"] == "Alpha" and d["page_category"] == "Software"
    s = c.get(f"/api/gems/{ids['P7A']}/scaling").json()
    assert [p["active_ad_count"] for p in s] == [1, 2, 4, 6]
    assert c.get("/api/gems/-999").status_code == 404
