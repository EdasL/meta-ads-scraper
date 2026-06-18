"""analyze_ratings.py — aggregate frontend.app_ratings by trait for tuning.

Read-only: queries only, no DB mutations.

Run:
    PYTHONPATH=src AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf \\
    .venv/bin/python scripts/analyze_ratings.py
"""
import json
import os
from collections import defaultdict

from sqlalchemy import text

from agf.db.engine import get_engine

_RATINGS = ("like", "neutral", "dislike")

_ZERO = lambda: {"like": 0, "neutral": 0, "dislike": 0}  # noqa: E731


def _score_band(gem_score) -> str:
    if gem_score is None:
        return "unknown"
    s = float(gem_score)
    if s < 60:
        return "<60"
    if s < 70:
        return "60-70"
    if s < 80:
        return "70-80"
    return "80+"


def analyze_ratings(conn) -> dict:
    """Return trait-bucketed counts from frontend.app_ratings.

    Args:
        conn: SQLAlchemy connection (read-only usage).

    Returns:
        {
            totals: {like, neutral, dislike},
            by_cohort: {cohort_str -> {like, neutral, dislike}},
            by_score_band: {"<60"|"60-70"|"70-80"|"80+" -> {like, neutral, dislike}},
            by_funnel: {"with_funnel"|"no_funnel" -> {like, neutral, dislike}},
            by_incumbent: {"incumbent"|"emerging" -> {like, neutral, dislike}},
        }
    """
    rows = conn.execute(text(
        "SELECT rating, features FROM frontend.app_ratings"
    )).fetchall()

    totals: dict = _ZERO()
    by_cohort: dict = defaultdict(_ZERO)
    by_score_band: dict = defaultdict(_ZERO)
    by_funnel: dict = defaultdict(_ZERO)
    by_incumbent: dict = defaultdict(_ZERO)

    for rating, features in rows:
        # Normalise rating
        r = rating if rating in _RATINGS else "neutral"
        totals[r] += 1

        # Parse features JSONB — may arrive as dict or raw JSON string
        if features is None:
            f: dict = {}
        elif isinstance(features, str):
            try:
                f = json.loads(features)
            except (ValueError, TypeError):
                f = {}
        else:
            f = features  # already a dict (psycopg2 jsonb auto-parse)

        # by_cohort
        cohort = f.get("cohort") or "unknown"
        by_cohort[cohort][r] += 1

        # by_score_band
        band = _score_band(f.get("gem_score"))
        by_score_band[band][r] += 1

        # by_funnel
        has_funnel = f.get("has_funnel")
        funnel_key = "with_funnel" if has_funnel else "no_funnel"
        by_funnel[funnel_key][r] += 1

        # by_incumbent (page_like_count > 10000)
        plc = f.get("page_like_count")
        incumbent_key = "incumbent" if (plc is not None and float(plc) > 10000) else "emerging"
        by_incumbent[incumbent_key][r] += 1

    return {
        "totals": totals,
        "by_cohort": dict(by_cohort),
        "by_score_band": dict(by_score_band),
        "by_funnel": dict(by_funnel),
        "by_incumbent": dict(by_incumbent),
    }


def _fmt_bucket(name: str, counts: dict) -> str:
    total = sum(counts.values())
    parts = "  ".join(f"{r}: {counts.get(r, 0)}" for r in _RATINGS)
    return f"  {name:<20} {parts}  (total: {total})"


def main() -> None:
    db_url = os.environ.get("AGF_DATABASE_URL")
    engine = get_engine(db_url) if db_url else get_engine()
    with engine.connect() as conn:
        out = analyze_ratings(conn)

    print("=== Ratings analysis ===\n")

    t = out["totals"]
    total = sum(t.values())
    print(f"Totals: {total} ratings — like: {t['like']}  neutral: {t['neutral']}  dislike: {t['dislike']}\n")

    for section, label in [
        ("by_cohort", "By cohort"),
        ("by_score_band", "By gem_score band"),
        ("by_funnel", "By funnel"),
        ("by_incumbent", "By incumbent signal"),
    ]:
        print(f"{label}:")
        data = out[section]
        if not data:
            print("  (no data)")
        else:
            for key in sorted(data):
                print(_fmt_bucket(key, data[key]))
        print()


if __name__ == "__main__":
    main()
