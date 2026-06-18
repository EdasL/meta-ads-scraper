"""Fit a per-user preference model on frontend.app_ratings and write per-app
P(like) + a 75/25 review order into frontend.preference_scores.

Mirrors the GemScore pattern (Python writes, Go reads). The fitted coefficients
are logged as a GemScore-reweight proposal.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/score_preferences.py
"""
import json
import os
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.scoring.preference import (
    PreferenceModel, interleave, uncertainty, why_string,
)

_MV_FEATURES = (
    "SELECT advertiser_id, funnel_intent, whitespace, scaling_velocity, "
    "under_radar, demand, (funnel_url IS NOT NULL) AS has_funnel "
    "FROM frontend.emerging_scaling_apps"
)


def _label_rows(conn, user_email):
    rows = conn.execute(text(
        "SELECT rating, features FROM frontend.app_ratings WHERE user_email = :e"),
        {"e": user_email}).fetchall()
    out = []
    for rating, features in rows:
        f = features if isinstance(features, dict) else (
            json.loads(features) if features else {})
        out.append((rating, f))
    return out


def _matview_features(conn):
    out = []
    for r in conn.execute(text(_MV_FEATURES)).mappings():
        out.append((r["advertiser_id"], dict(r)))
    return out


def score_user(conn, user_email: str) -> int:
    model = PreferenceModel.fit(_label_rows(conn, user_email))
    apps = _matview_features(conn)
    scored = []
    why_by_id = {}
    for advertiser_id, feats in apps:
        p = model.predict(feats)
        scored.append({"advertiser_id": advertiser_id, "p_like": p,
                       "uncertainty": uncertainty(p)})
        why_by_id[advertiser_id] = why_string(model, feats)
    ordered = interleave(scored)

    conn.execute(text(
        "DELETE FROM frontend.preference_scores WHERE user_email = :e"),
        {"e": user_email})
    if ordered:
        conn.execute(text(
            "INSERT INTO frontend.preference_scores "
            "(user_email, advertiser_id, p_like, uncertainty, queue_rank, why, "
            " model_version, scored_at) VALUES "
            "(:e, :a, :p, :u, :q, :w, :v, now())"),
            [{"e": user_email, "a": r["advertiser_id"], "p": r["p_like"],
              "u": r["uncertainty"], "q": r["queue_rank"],
              "w": why_by_id[r["advertiser_id"]], "v": model.version}
             for r in ordered])
    print(f"[pref] {user_email}: model={model.version} scored={len(ordered)} "
          f"coefs={json.dumps(model.coefficients)}", flush=True)
    return len(ordered)


def score_all_users(conn) -> dict:
    emails = [r[0] for r in conn.execute(text(
        "SELECT DISTINCT user_email FROM frontend.app_ratings")).fetchall()]
    return {e: score_user(conn, e) for e in emails}


def main() -> None:
    url = os.environ.get("AGF_DATABASE_URL")
    engine = get_engine(url) if url else get_engine()
    with begin(engine) as conn:
        counts = score_all_users(conn)
    print(f"[pref] done: {counts}", flush=True)
    if not counts:
        print("[pref] no rated users yet", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
