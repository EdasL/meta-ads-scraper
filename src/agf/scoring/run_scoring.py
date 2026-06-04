"""Score every advertiser, write adtech.advertiser_scores, refresh the product
matview. score_all is conn-first (no commit); refresh uses its own autocommit
connection (REFRESH ... CONCURRENTLY cannot run in a transaction block)."""
from collections import defaultdict
from sqlalchemy import text
from agf.scoring.cohorts import pctile
from agf.scoring.features import build_features
from agf.scoring.gemscore import (scaling_velocity, under_radar, funnel_intent, gem_score)
from agf.scoring.vetoes import apply_vetoes

ALERT_THRESHOLD = 70.0           # master-plan §2: GemScore >= 70 -> review queue


def _eligible_advertiser_ids(conn):
    return [a for (a,) in conn.execute(text(
        "SELECT DISTINCT advertiser_id FROM public.ads "
        "WHERE advertiser_id IS NOT NULL ORDER BY advertiser_id")).all()]


def _values(members, attr):
    return [getattr(m, attr) for m in members if getattr(m, attr) is not None]


def _sv_components(fv, members):
    # all "higher raw = faster scaling"; longevity uses the bounded share so a big
    # incumbent's raw run-length can't dominate a fast-growing gem.
    return {
        "wow": pctile(fv.wow_log_growth, _values(members, "wow_log_growth")),
        "accel": pctile(fv.accel, _values(members, "accel")),
        "ewma": pctile(fv.ewma_velocity, _values(members, "ewma_velocity")),
        "longevity": pctile(fv.longevity_share, _values(members, "longevity_share")),
    }


def _ur_components(fv, members):
    # higher raw (more ratings/followers/reach, older) => MORE known => LOWER UR
    def inv(attr, val):
        return None if val is None else 1.0 - pctile(val, _values(members, attr))
    return {
        "rating": inv("rating_count", fv.rating_count),
        "followers": inv("ig_followers", fv.ig_followers),
        "reach": inv("total_eu_reach", fv.total_eu_reach),
        "newness": inv("release_age_days", fv.release_age_days),
    }


def _fi_components(fv):
    return {
        "fingerprint": 1.0 if fv.has_funnel else 0.0,
        "cta": 1.0 if fv.install_cta else 0.0,
        "monetize": 1.0 if (fv.has_app or fv.has_funnel) else 0.0,
    }


def score_all(conn, as_of) -> int:
    """Build features, normalize within cohort, score, veto, write advertiser_scores.
    conn-first: no commit. Returns the number of advertisers scored."""
    fvs = [build_features(conn, aid, as_of) for aid in _eligible_advertiser_ids(conn)]
    by_cohort = defaultdict(list)
    for fv in fvs:
        by_cohort[fv.cohort].append(fv)

    n = 0
    for cohort, members in by_cohort.items():
        for fv in members:
            sv = scaling_velocity(_sv_components(fv, members))
            ur = under_radar(_ur_components(fv, members))
            fi = funnel_intent(_fi_components(fv))
            gs = gem_score(sv, ur, fi)
            veto = apply_vetoes(fv)
            alert = (gs >= ALERT_THRESHOLD) and not veto.vetoed
            conn.execute(text("""
                INSERT INTO adtech.advertiser_scores
                  (advertiser_id, cohort, gem_score, scaling_velocity, under_radar,
                   funnel_intent, active_ad_count, max_longevity_days, alert,
                   vetoed, veto_reason, scored_at)
                VALUES (:adv, :coh, :gs, :sv, :ur, :fi, :aac, :mld, :alert,
                        :vetoed, :reason, clock_timestamp())
                ON CONFLICT (advertiser_id) DO UPDATE SET
                  cohort=EXCLUDED.cohort, gem_score=EXCLUDED.gem_score,
                  scaling_velocity=EXCLUDED.scaling_velocity, under_radar=EXCLUDED.under_radar,
                  funnel_intent=EXCLUDED.funnel_intent, active_ad_count=EXCLUDED.active_ad_count,
                  max_longevity_days=EXCLUDED.max_longevity_days, alert=EXCLUDED.alert,
                  vetoed=EXCLUDED.vetoed, veto_reason=EXCLUDED.veto_reason,
                  scored_at=clock_timestamp()
            """), {"adv": fv.advertiser_id, "coh": cohort, "gs": gs, "sv": sv,
                   "ur": ur, "fi": fi, "aac": fv.active_ad_count,
                   "mld": fv.max_longevity_days, "alert": alert,
                   "vetoed": veto.vetoed, "reason": veto.reason})
            n += 1
    return n


def refresh_emerging_matview(engine) -> None:
    """REFRESH ... CONCURRENTLY on a dedicated autocommit connection (master-plan
    hard rule: cannot run inside a transaction block; needs the UNIQUE index)."""
    raw = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        raw.exec_driver_sql(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY frontend.emerging_scaling_apps")
    finally:
        raw.close()


def run_scoring(engine, as_of) -> int:
    """Convenience orchestrator: score in one committed txn, then refresh."""
    from agf.db.engine import begin
    with begin(engine) as conn:
        n = score_all(conn, as_of)
    refresh_emerging_matview(engine)
    return n
