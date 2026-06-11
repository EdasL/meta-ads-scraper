"""Niche crowding for the whitespace axis (GemScore v2, spec 2026-06-11).

A niche is a discovery taxonomy keyword (discovery.candidates.source_keyword).
Crowding is how many distinct kept advertisers under that keyword show
sustained ad activity (>= 2 weeks observed) — the "already copied by
everybody" dial. Advertisers with no keyword lineage (seeded before keyword
discovery) simply don't appear in the map; the axis floors to neutral."""
from sqlalchemy import text

SUSTAINED_MIN_WEEKS = 2  # mirrors the persistence veto: 1 week is not presence


def load_niche_crowding(conn):
    """Returns (by_advertiser, niche_counts).

    by_advertiser: {advertiser_id: (source_keyword, sustained_count)} — when an
    advertiser was discovered under several keywords, the MOST crowded one wins
    (conservative: never overstate whitespace).
    niche_counts: one sustained-advertiser count per distinct niche, only
    niches with >= 1 sustained advertiser — the percentile population for the
    whitespace axis."""
    rows = conn.execute(text("""
        WITH member AS (
            SELECT DISTINCT c.source_keyword AS kw, c.kept_advertiser_id AS aid
            FROM discovery.candidates c
            WHERE c.kept_advertiser_id IS NOT NULL AND c.source_keyword IS NOT NULL
        ), sustained AS (
            SELECT m.kw, m.aid
            FROM member m
            JOIN adtech.app_advertiser_metrics w ON w.advertiser_id = m.aid
            GROUP BY m.kw, m.aid
            HAVING count(DISTINCT w.week_start) >= :min_weeks
        ), counts AS (
            SELECT k.kw, count(s.aid) AS n_adv
            FROM (SELECT DISTINCT kw FROM member) k
            LEFT JOIN sustained s ON s.kw = k.kw
            GROUP BY k.kw
        )
        SELECT m.aid, m.kw, c.n_adv
        FROM member m JOIN counts c ON c.kw = m.kw
    """), {"min_weeks": SUSTAINED_MIN_WEEKS}).all()
    by_adv: dict[int, tuple[str, int]] = {}
    for r in rows:
        cur = by_adv.get(r.aid)
        if cur is None or int(r.n_adv) > cur[1]:
            by_adv[int(r.aid)] = (r.kw, int(r.n_adv))
    per_niche = {r.kw: int(r.n_adv) for r in rows}
    return by_adv, sorted(n for n in per_niche.values() if n >= 1)
