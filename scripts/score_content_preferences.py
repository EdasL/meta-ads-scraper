"""Per-user similarity-to-likes scoring over content embeddings → preference_scores.

Supersedes scripts/score_preferences.py in the serving path (the 6-axis Logit now
lives in scripts/analyze_axis_coefficients.py as the GemScore-tuning knob).

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/score_content_preferences.py
"""
import os
import sys

from sqlalchemy import text

from agf.db.engine import begin, get_engine
from agf.scoring.content_preference import ContentPreferenceModel

_EMB = text("SELECT advertiser_id, embedding FROM frontend.app_embeddings")
_NAMES = text("SELECT advertiser_id, app_name, gem_score "
              "FROM frontend.emerging_scaling_apps")
_LABELS = text("SELECT advertiser_id, rating FROM frontend.app_ratings "
               "WHERE user_email = :e")
_EMAILS = text("SELECT DISTINCT user_email FROM frontend.app_ratings")


def _load(conn):
    emb = {a: [float(x) for x in v] for a, v in conn.execute(_EMB)}
    meta = {r.advertiser_id: (r.app_name, r.gem_score)
            for r in conn.execute(_NAMES)}
    return emb, meta


def score_user(conn, user_email, emb, meta) -> int:
    labels = {a: r for a, r in conn.execute(_LABELS, {"e": user_email})}
    liked = [(a, meta.get(a, ("", 0))[0] or "", emb[a])
             for a, r in labels.items() if r == "like" and a in emb]
    disliked = [(a, meta.get(a, ("", 0))[0] or "", emb[a])
                for a, r in labels.items() if r == "dislike" and a in emb]
    model = ContentPreferenceModel.fit(liked, disliked)

    scored = []
    for a, vec in emb.items():
        if model.version == "coldstart":
            gs = meta.get(a, ("", 0))[1] or 0.0
            scored.append((a, float(gs) / 100.0, "", float(gs)))
        else:
            raw = model.score(vec)
            scored.append((a, model.p_like(raw), model.why(vec), raw))
    scored.sort(key=lambda t: t[3], reverse=True)   # rank by raw score / gem_score

    conn.execute(text("DELETE FROM frontend.preference_scores WHERE user_email=:e"),
                 {"e": user_email})
    if scored:
        conn.execute(text(
            "INSERT INTO frontend.preference_scores "
            "(user_email, advertiser_id, p_like, uncertainty, queue_rank, why, "
            " model_version, scored_at) VALUES "
            "(:e,:a,:p,:u,:q,:w,:v, now())"),
            [{"e": user_email, "a": a, "p": p,
              "u": 1.0 - abs(2.0 * p - 1.0), "q": q, "w": why, "v": model.version}
             for q, (a, p, why, _raw) in enumerate(scored)])
    print(f"[cpref] {user_email}: model={model.version} scored={len(scored)} "
          f"likes={len(liked)}", flush=True)
    return len(scored)


def score_all_users(conn) -> dict:
    emb, meta = _load(conn)
    out = {}
    for (email,) in conn.execute(_EMAILS):
        out[email] = score_user(conn, email, emb, meta)
    return out


def main() -> int:
    with begin(get_engine()) as conn:
        print(f"[cpref] {score_all_users(conn)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
