"""Discovery-saturation report: is the surface broad or collapsing, and which keywords
have gone dry? Read-only. Run any time to see whether release-expand + the flywheel are
keeping category diversity up and novelty alive.

    AGF_DATABASE_URL=postgresql://agf:agf@localhost:5433/agf python -m scripts.discovery_metrics

Reads category histograms (kept apps + feed gems) and per-keyword yield, then prints
normalized entropy / HHI and the dead/saturated keyword tail."""
import os

from sqlalchemy import text

from agf.db.engine import get_engine
from agf.discovery.metrics import herfindahl, normalized_entropy, shannon_entropy


def _histogram(conn, sql: str) -> dict:
    return {(r[0] or "(unknown)"): r[1] for r in conn.execute(text(sql)).all()}


def report(conn) -> dict:
    kept = _histogram(conn, """
        SELECT a.genre, count(DISTINCT a.id)
        FROM public.apps a
        JOIN discovery.candidates c
          ON c.store = a.store AND c.store_id = a.store_id
        WHERE c.status = 'kept'
        GROUP BY a.genre ORDER BY 2 DESC""")
    feed = _histogram(conn, """
        SELECT cohort, count(*) FROM frontend.emerging_scaling_apps
        WHERE alert = TRUE GROUP BY cohort ORDER BY 2 DESC""")
    # per-keyword yield: candidates found vs kept, to surface dead/saturated keywords
    kw = conn.execute(text("""
        SELECT source_keyword,
               count(*) AS found,
               count(*) FILTER (WHERE status = 'kept') AS kept
        FROM discovery.candidates
        WHERE source_keyword IS NOT NULL
        GROUP BY source_keyword""")).all()
    dead = sorted([(r.source_keyword, r.found) for r in kw if r.kept == 0],
                  key=lambda t: -t[1])
    return {
        "kept": {"hist": kept, "n_categories": len(kept),
                 "normalized_entropy": normalized_entropy(kept),
                 "entropy_bits": shannon_entropy(kept), "hhi": herfindahl(kept)},
        "feed": {"hist": feed, "n_categories": len(feed),
                 "normalized_entropy": normalized_entropy(feed),
                 "entropy_bits": shannon_entropy(feed), "hhi": herfindahl(feed)},
        "keywords": {"total": len(kw), "dead": len(dead),
                     "dead_share": round(len(dead) / len(kw), 3) if kw else 0.0,
                     "worst_dead": dead[:15]},
    }


def _print(rep: dict) -> None:
    for layer in ("kept", "feed"):
        d = rep[layer]
        print(f"\n== {layer} apps: {d['n_categories']} categories ==")
        print(f"   normalized entropy {d['normalized_entropy']:.3f} "
              f"(1=even, 0=collapsed) · HHI {d['hhi']:.3f} (1=one basin)")
        top = sorted(d["hist"].items(), key=lambda t: -t[1])[:8]
        tot = sum(d["hist"].values()) or 1
        for g, n in top:
            print(f"     {n:>5} ({100*n/tot:4.1f}%)  {g}")
    k = rep["keywords"]
    print(f"\n== keywords: {k['dead']}/{k['total']} dead ({100*k['dead_share']:.0f}% kept nothing) ==")
    for kw, found in k["worst_dead"]:
        print(f"     found {found:>3}, kept 0   \"{kw}\"")


def main() -> None:
    url = os.environ.get("AGF_DATABASE_URL", "postgresql://agf:agf@localhost:5433/agf")
    with get_engine(url).connect() as conn:
        _print(report(conn))


if __name__ == "__main__":
    main()
