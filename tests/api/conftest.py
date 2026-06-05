"""Committed-matview fixtures for API tests. The matview only reflects COMMITTED
rows after REFRESH, so these use the migrated engine directly (not the rolled-back
pg_conn) and clean up their sentinels — mirrors Plan 5's e2e."""
import pytest
from datetime import date, timedelta
from sqlalchemy import text
from agf.db.engine import begin
from agf.scoring.run_scoring import refresh_emerging_matview

SENT = ("P7A", "P7B", "P7C")          # cohort-shared sentinel page_ids
WEEK0 = date(2026, 3, 2)


def _seed(engine):
    ids = {}
    # (page_id, cohort, page_name, gem_score, sv, ur, fi, active, longd, alert)
    specs = [
        ("P7A", "Health & Fitness×US", "Alpha", 88.0, 0.9, 0.9, 0.8, 6, 30, True),
        ("P7B", "Health & Fitness×US", "Bravo", 74.0, 0.8, 0.7, 0.7, 4, 22, True),
        ("P7C", "Education×US", "Charlie", 40.0, 0.5, 0.3, 0.4, 2, 18, False),
    ]
    with begin(engine) as conn:
        for pid, cohort, name, gs, sv, ur, fi, active, longd, alert in specs:
            adv = conn.execute(text(
                "INSERT INTO public.advertisers (page_id, page_name, page_category) "
                "VALUES (:p,:n,'Software') ON CONFLICT (page_id) DO UPDATE "
                "SET page_name=EXCLUDED.page_name RETURNING id"),
                {"p": pid, "n": name}).scalar_one()
            ids[pid] = adv
            conn.execute(text("""
                INSERT INTO adtech.advertiser_scores
                  (advertiser_id, cohort, gem_score, scaling_velocity, under_radar,
                   funnel_intent, active_ad_count, max_longevity_days, alert, vetoed, veto_reason)
                VALUES (:a,:c,:gs,:sv,:ur,:fi,:aac,:mld,:al,FALSE,NULL)
                ON CONFLICT (advertiser_id) DO UPDATE SET gem_score=EXCLUDED.gem_score,
                  alert=EXCLUDED.alert, cohort=EXCLUDED.cohort"""),
                {"a": adv, "c": cohort, "gs": gs, "sv": sv, "ur": ur, "fi": fi,
                 "aac": active, "mld": longd, "al": alert})
            for i, n in enumerate((1, 2, 4, active)):
                conn.execute(text("""INSERT INTO adtech.app_advertiser_metrics
                    (advertiser_id, week_start, active_ad_count, distinct_creative_count, total_eu_reach)
                    VALUES (:a,:w,:n,:n,:r) ON CONFLICT (advertiser_id, week_start) DO NOTHING"""),
                    {"a": adv, "w": WEEK0 + timedelta(weeks=i), "n": n, "r": n * 100})
        # enrichment backfill (§3.3): ig_* on Alpha; Bravo is owned by Alpha so the
        # dossier resolves a parent_company name. NULL-tolerant for everyone else.
        conn.execute(text("UPDATE public.advertisers SET ig_username='alphafit', "
                          "ig_followers=1200 WHERE id=:a"), {"a": ids["P7A"]})
        conn.execute(text("UPDATE public.advertisers SET parent_company_id=:p "
                          "WHERE id=:a"), {"p": ids["P7A"], "a": ids["P7B"]})
    refresh_emerging_matview(engine)
    return ids


def _cleanup(engine):
    with begin(engine) as conn:
        ids = conn.execute(text("SELECT id FROM public.advertisers WHERE page_id = ANY(:p)"),
                           {"p": list(SENT)}).scalars().all()
        if ids:
            for t in ("frontend.review_decisions", "adtech.advertiser_scores",
                      "adtech.app_advertiser_metrics"):
                conn.execute(text(f"DELETE FROM {t} WHERE advertiser_id = ANY(:i)"), {"i": ids})
            # break the self-FK (Bravo→Alpha) before deleting the rows
            conn.execute(text("UPDATE public.advertisers SET parent_company_id=NULL "
                              "WHERE id = ANY(:i)"), {"i": ids})
            conn.execute(text("DELETE FROM public.advertisers WHERE id = ANY(:i)"), {"i": ids})
    refresh_emerging_matview(engine)


@pytest.fixture
def seeded(_migrated_engine):
    _cleanup(_migrated_engine)
    ids = _seed(_migrated_engine)
    try:
        yield _migrated_engine, ids
    finally:
        _cleanup(_migrated_engine)
