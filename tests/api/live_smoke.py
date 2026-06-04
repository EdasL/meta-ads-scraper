"""Plan 7 LIVE assembled smoke (the RUN-IT-LIVE rule). Not a pytest test — a
throwaway driver. Seeds the real matview (Plan 5 path), drives the real FastAPI via
TestClient against real Postgres, exercises read-only enforcement, and validates the
production compose.

Run:
  AGF_TEST_DB_URL=postgresql://agf:agf@localhost:5433/agf \
  PYTHONPATH=src .venv/bin/python tests/api/live_smoke.py
"""
import os
import subprocess
from sqlalchemy import text
from fastapi.testclient import TestClient
from agf.db.engine import get_engine
from agf.db.migrate import apply_migrations
from agf.api.app import create_app
from agf.api import deps
import tests.api.conftest as cf


def main():
    eng = get_engine(os.environ["AGF_TEST_DB_URL"])
    apply_migrations(eng)
    cf._cleanup(eng)
    ids = cf._seed(eng)
    ok = []
    try:
        c = TestClient(create_app(eng))

        # 1. healthz
        r = c.get("/healthz")
        print(f"[1] healthz : {r.status_code} {r.json()}")
        ok.append(r.json().get("status") == "ok")

        # 2. shortlist ≥70 — gem in, low-score out
        sl = c.get("/api/shortlist", params={"min_score": 70}).json()
        sids = [x["advertiser_id"] for x in sl]
        print(f"[2] shortlist≥70: {[x['page_name'] for x in sl if x['advertiser_id'] in ids.values()]}")
        ok.append(ids["P7A"] in sids and ids["P7B"] in sids and ids["P7C"] not in sids)

        # 3. dossier
        d = c.get(f"/api/gems/{ids['P7A']}").json()
        print(f"[3] dossier : page={d['gem']['page_name']} cat={d['page_category']} score={d['gem']['gem_score']}")
        ok.append(d["gem"]["page_name"] == "Alpha")

        # 4. review queue + decision removes it
        q1 = [x["advertiser_id"] for x in c.get("/api/review/queue").json()]
        dec = c.post(f"/api/review/{ids['P7A']}/decision",
                     json={"verdict": "accept", "reviewer": "founder", "note": "go"})
        q2 = [x["advertiser_id"] for x in c.get("/api/review/queue").json()]
        print(f"[4] review  : queued={ids['P7A'] in q1} decision={dec.status_code} dequeued={ids['P7A'] not in q2}")
        ok.append(ids["P7A"] in q1 and dec.status_code == 200 and ids["P7A"] not in q2)

        # 5. health metrics
        hm = c.get("/api/health/metrics").json()
        print(f"[5] metrics : total={hm['total']} errors={hm['errors']} degraded={hm['degraded']}")
        ok.append("alerts" in hm and "degraded" in hm)

        # 6. read-only conn denies writes
        class _Req: ...
        req = _Req(); req.app = c.app
        it = deps.get_conn(req); conn = next(it)
        denied = False
        try:
            conn.execute(text("CREATE TEMP TABLE _probe(i int)"))
        except Exception:
            denied = True
        finally:
            it.close()
        print(f"[6] read-only: write denied={denied}")
        ok.append(denied)

        # 7. production compose validates
        env = {**os.environ, "POSTGRES_PASSWORD": "x", "MINIO_ROOT_PASSWORD": "y",
               "POSTGRES_USER": "agf", "POSTGRES_DB": "agf", "MINIO_ROOT_USER": "agf"}
        compose = os.path.join(os.path.dirname(__file__), "..", "..", "ops", "docker-compose.yml")
        rc = subprocess.run(["docker", "compose", "-f", compose, "config", "-q"],
                            capture_output=True, text=True, env=env).returncode
        print(f"[7] compose : config -q exit={rc}")
        ok.append(rc == 0)
    finally:
        cf._cleanup(eng)

    print(f"\nRESULT: {sum(ok)}/{len(ok)} live checks passed -> "
          f"{'ALL GREEN' if all(ok) else 'SOME FAILED'}")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
